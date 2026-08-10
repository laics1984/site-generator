"""
Scrape endpoints. A crawl runs as a background JOB: POST /start returns a
job_id immediately and the frontend polls /jobs/{id}. A 1-2 minute crawl
therefore never rides on a single HTTP request, and can report progress and be
cancelled mid-flight.

The extracted SourceContent + brand candidate come back in the job's `result`,
so the frontend can show a confirmation step before any LLM call is spent.

A synchronous POST /preview used to run the whole crawl inline and return the
same payload. It was the original implementation, the job model superseded it,
and its last caller was gone — so it is deleted rather than kept as a debug
entrypoint: it bypassed progress, cancellation, result re-use and orphan
reaping, which makes it a path that no longer tests what production does. To
drive a crawl by hand, POST /start and poll /jobs/{id} — the same route the app
takes. Result RE-USE, which was that endpoint's in-process cache, now lives on
the job path where the traffic actually is (`CrawlJobManager.find_reusable`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.services.crawl_jobs import get_manager
from app.services.crawl_orchestrator import run_crawl_job
from app.services.scraper import ScrapeError, extend_crawl
from app.services.sitemap import probe_sitemap
from app.services.url_guard import UnsafeUrlError, assert_public_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scrape", tags=["scrape"])


class ExtendCrawlRequest(BaseModel):
    entry_url: str = Field(..., description="The original entry URL (final_url from the prior crawl).")
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

    Frontend usage: pass the prior crawl's `final_url` as `entry_url`, its
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

    An identical crawl (same URL, same options) that finished within
    `scrape_cache_ttl_seconds` is handed straight back instead of re-run. A
    crawl is the most expensive thing this service does, and a double-click,
    a browser Back, or a regeneration would otherwise re-render every page.
    The response is shape-identical, so the frontend polls the returned id
    exactly as it would a fresh one and gets `status: "done"` on its first tick.
    """
    try:
        await assert_public_url(payload.url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    mgr = get_manager()
    options = {
        "respect_robots": payload.respect_robots,
        "crawl": payload.crawl,
        "crawl_max_pages": payload.crawl_max_pages,
        "crawl_max_depth": payload.crawl_max_depth,
    }

    # Housekeeping on the cheapest possible trigger: fail rows a dead process
    # left mid-flight, then drop results past the retention window.
    await mgr.reap_orphans()
    await mgr.purge_expired(max_age_seconds=settings.scrape_cache_ttl_seconds)

    reusable = await mgr.find_reusable(
        payload.url, options, max_age_seconds=settings.scrape_cache_ttl_seconds
    )
    if reusable is not None:
        logger.info(
            "Reusing crawl job %s for %s (%.0fs old)",
            reusable.id, payload.url, time.time() - reusable.created_at,
        )
        return {"job_id": reusable.id, "status": reusable.status, "reused": True}

    job = await mgr.create(payload.url, options=options)
    task = asyncio.create_task(run_crawl_job(job.id))
    mgr.register_task(job.id, task)
    return {"job_id": job.id, "status": job.status, "reused": False}


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
    try:
        await assert_public_url(payload.url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = await probe_sitemap(payload.url)
    return {
        "has_sitemap": result.has_sitemap,
        "total_urls": result.total_urls,
        "urls": result.urls[:50],  # cap what we ship over the wire
        "sources": result.sources,
    }
