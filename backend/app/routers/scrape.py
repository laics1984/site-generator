"""
Scrape endpoint. Returns extracted SourceContent + brand candidate so the
frontend can show a confirmation step before spending an LLM call.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.crawl_jobs import get_manager
from app.services.crawl_orchestrator import run_crawl_job
from app.services.scraper import ScrapeError, ScrapeResult, extend_crawl, scrape_url
from app.services.sitemap import probe_sitemap

router = APIRouter(prefix="/api/scrape", tags=["scrape"])


# 5-minute in-process cache to absorb double-clicks + back-button traffic.
_CACHE: dict[str, tuple[float, ScrapeResult]] = {}
_CACHE_TTL = 300


class ScrapePreviewRequest(BaseModel):
    url: str = Field(..., description="The page to scrape (http or https).")
    respect_robots: bool = Field(
        default=True,
        description="Honour robots.txt. Defaults to true; flip only if you own the site.",
    )
    crawl: bool = Field(
        default=True,
        description=(
            "Walk same-domain links to discover sub-pages. Adds 10-30s but lets "
            "the generator mirror the source site's structure. Set false for fast "
            "single-page generation."
        ),
    )
    crawl_max_pages: int = Field(
        default=20,
        ge=0,
        le=40,
        description=(
            "Cap on additional pages discovered (excluding the entry). "
            "Raising this only affects crawl time — it does NOT increase LLM "
            "cost, which is driven by how many pages the user selects in the "
            "page picker downstream."
        ),
    )
    crawl_max_depth: int = Field(
        default=3,
        ge=1,
        le=4,
        description=(
            "Maximum link-hops away from the entry page. NOT URL-path depth — "
            "a flat-URL page like /our-team still counts as depth N if it took "
            "N clicks from the homepage to reach. 3 hops surfaces pages hidden "
            "from the homepage menu (linked only from /services or /about). "
            "Bump to 4 for very deep marketing sites."
        ),
    )


@router.post("/preview")
async def scrape_preview(payload: ScrapePreviewRequest) -> dict[str, Any]:
    cache_key = (
        f"{int(payload.respect_robots)}:{int(payload.crawl)}:"
        f"{payload.crawl_max_pages}:{payload.crawl_max_depth}:{payload.url}"
    )
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and (now - cached[0]) < _CACHE_TTL:
        result = cached[1]
    else:
        try:
            result = await scrape_url(
                payload.url,
                respect_robots=payload.respect_robots,
                crawl=payload.crawl,
                crawl_max_pages=payload.crawl_max_pages,
                crawl_max_depth=payload.crawl_max_depth,
            )
        except ScrapeError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=408, detail="Scrape timed out") from exc
        _CACHE[cache_key] = (now, result)
        _gc_cache(now)

    return {
        "url": result.url,
        "final_url": result.final_url,
        "source_content": result.source_content.model_dump(mode="json"),
        "brand_candidate": (
            result.brand_candidate.model_dump(mode="json")
            if result.brand_candidate
            else None
        ),
        "image_candidates": [asdict(c) for c in result.image_candidates],
        "fetched_at": result.fetched_at,
        "discovered_count": len(result.source_content.discovered_pages),
        # URLs the BFS frontier had queued but didn't process because the cap
        # was reached. Frontend uses these to offer "Crawl N more".
        "unvisited_urls": result.unvisited_urls,
        "unvisited_count": len(result.unvisited_urls),
    }


def _gc_cache(now: float) -> None:
    expired = [k for k, (ts, _) in _CACHE.items() if (now - ts) > _CACHE_TTL]
    for k in expired:
        _CACHE.pop(k, None)


class ExtendCrawlRequest(BaseModel):
    entry_url: str = Field(..., description="The original entry URL (final_url from prior preview).")
    seed_urls: list[str] = Field(
        ...,
        description="Unvisited URLs from the prior crawl to resume from.",
        min_length=1,
    )
    already_seen: list[str] = Field(
        default_factory=list,
        description="URLs the prior crawl already visited; we won't re-fetch them.",
    )
    max_more: int = Field(default=20, ge=1, le=40, description="How many more pages to fetch this pass.")
    crawl_max_depth: int = Field(default=3, ge=1, le=4)
    respect_robots: bool = True


@router.post("/extend")
async def scrape_extend(payload: ExtendCrawlRequest) -> dict[str, Any]:
    """
    Resume a crawl from a saved frontier without re-rendering the entry page.

    Frontend usage: pass the prior preview's `final_url` as `entry_url`, its
    `unvisited_urls` as `seed_urls`, and the source URLs already in
    `discovered_pages` as `already_seen`. Returns the new pages + a fresh
    `unvisited_urls` list (which may be empty when the crawl is now exhausted).
    """
    try:
        result = await extend_crawl(
            payload.entry_url,
            payload.seed_urls,
            already_seen=payload.already_seen,
            max_more=payload.max_more,
            crawl_max_depth=payload.crawl_max_depth,
            respect_robots=payload.respect_robots,
        )
    except ScrapeError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=408, detail="Extend crawl timed out") from exc

    return {
        "additional_pages": [p.model_dump(mode="json") for p in result.additional_pages],
        "added_count": len(result.additional_pages),
        "unvisited_urls": result.unvisited_urls,
        "unvisited_count": len(result.unvisited_urls),
    }


class StartCrawlRequest(BaseModel):
    """Async crawl kickoff. Returns a job_id; poll /api/scrape/jobs/{id}."""

    url: str
    respect_robots: bool = True
    crawl: bool = True
    crawl_max_pages: int = Field(default=20, ge=0, le=40)
    crawl_max_depth: int = Field(default=3, ge=1, le=4)


@router.post("/start")
async def start_crawl(payload: StartCrawlRequest) -> dict[str, Any]:
    """
    Kick off a crawl as a background task. Returns immediately with a job_id.
    Frontend polls GET /api/scrape/jobs/{id} for status + progress + result.
    """
    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http(s)://")
    mgr = get_manager()
    job = await mgr.create(
        payload.url,
        options={
            "respect_robots": payload.respect_robots,
            "crawl": payload.crawl,
            "crawl_max_pages": payload.crawl_max_pages,
            "crawl_max_depth": payload.crawl_max_depth,
        },
    )
    task = asyncio.create_task(run_crawl_job(job.id))
    mgr.register_task(job.id, task)
    return {"job_id": job.id, "status": job.status}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    """Read current job state. Frontend polls this every ~1s during crawl."""
    job = await get_manager().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return {
        "id": job.id,
        "entry_url": job.entry_url,
        "host": job.host,
        "status": job.status,
        "options": job.options,
        "progress": job.progress,
        "result": job.result,
        "error": job.error,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
        "elapsed_seconds": job.elapsed_seconds,
    }


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, Any]:
    """Flag a running job for cancellation. Orchestrator checks between pages."""
    ok = await get_manager().cancel(job_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Job not found or already terminal.",
        )
    return {"job_id": job_id, "status": "cancelling"}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str) -> dict[str, Any]:
    """Tidy up a terminal job + its pages. No-op on running jobs."""
    ok = await get_manager().delete(job_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Job not found or still running.",
        )
    return {"job_id": job_id, "status": "deleted"}


class SitemapProbeRequest(BaseModel):
    url: str = Field(..., description="Entry URL to probe.")


@router.post("/probe")
async def probe(payload: SitemapProbeRequest) -> dict[str, Any]:
    """
    Fast pre-scrape sitemap probe. Returns the site's true scope (page count
    + sample URLs) so the UI can offer a Quick/Full choice BEFORE paying for
    Playwright. Falls back to {has_sitemap: false, total_urls: 0} when there's
    no sitemap — the UI then proceeds with the default cap silently.
    """
    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http(s)://")
    result = await probe_sitemap(payload.url)
    return {
        "has_sitemap": result.has_sitemap,
        "total_urls": result.total_urls,
        "urls": result.urls[:50],  # cap what we ship over the wire
        "sources": result.sources,
    }
