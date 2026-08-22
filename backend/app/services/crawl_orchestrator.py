"""
Bridge between the crawl job manager (durable, cancellable) and the existing
scrape_url orchestrator. Runs a single crawl in the background, streaming
progress to SQLite, and recording the final ScrapeResult under the job row.

The actual crawl logic lives in services/scraper.py — this module is just the
job-aware wrapper.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.crawl_jobs import CrawlJobManager, get_manager
from app.services.scraper import ScrapeError, scrape_url
from app.services.source_preview import source_preview_payload

logger = logging.getLogger(__name__)


async def run_crawl_job(job_id: str) -> None:
    """
    Entry point for the background task. Reads the job from the manager,
    runs the crawl with progress + cancel hooks, persists the result.

    Designed to never raise into the asyncio.create_task caller — failures
    are recorded against the job's `error` field.
    """
    mgr: CrawlJobManager = get_manager()
    job = await mgr.get(job_id)
    if job is None:
        logger.warning("run_crawl_job called for unknown job %s", job_id)
        return
    if job.status != "queued":
        logger.warning(
            "run_crawl_job called for job %s with status=%s — skipping", job_id, job.status
        )
        return

    await mgr.mark_running(job_id)
    options = job.options or {}

    async def emit_progress(pages_done: int, current_url: str) -> None:
        await mgr.update_progress(
            job_id,
            {
                "pages_done": pages_done,
                "current_url": current_url,
                "current_step": "fetching",
            },
        )

    def cancelled() -> bool:
        return mgr.is_cancelled(job_id)

    try:
        result = await scrape_url(
            job.entry_url,
            respect_robots=options.get("respect_robots", True),
            crawl=options.get("crawl", True),
            crawl_max_pages=options.get("crawl_max_pages", 20),
            crawl_max_depth=options.get("crawl_max_depth", 3),
            on_progress=emit_progress,
            is_cancelled=cancelled,
        )
    except ScrapeError as exc:
        logger.info("Crawl job %s failed: %s", job_id, exc)
        if cancelled():
            await mgr.mark_cancelled(job_id)
        else:
            await mgr.mark_failed(job_id, str(exc))
        return
    except asyncio.CancelledError:
        logger.info("Crawl job %s asyncio-cancelled", job_id)
        await mgr.mark_cancelled(job_id)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Crawl job %s crashed", job_id)
        await mgr.mark_failed(job_id, f"{type(exc).__name__}: {exc}")
        return

    if cancelled():
        # Crawl returned because the cancel flag fired mid-loop
        await mgr.mark_cancelled(job_id)
        return

    payload: dict[str, Any] = _result_to_payload(result)
    await mgr.mark_done(job_id, payload)
    logger.info(
        "Crawl job %s done — %d discovered, %d unvisited",
        job_id,
        len(result.source_content.discovered_pages),
        len(result.unvisited_urls),
    )


def _result_to_payload(result) -> dict[str, Any]:
    """A crawl's `ScrapeResult` in the shared preview shape.

    The shape itself lives in services/source_preview.py, where the document
    and Facebook readers build the same one — it used to be defined here and
    hand-copied by the others, which meant a new field reached one reader and
    silently not the rest."""
    return source_preview_payload(
        url=result.url,
        final_url=result.final_url,
        source_content=result.source_content,
        brand_candidate=result.brand_candidate,
        image_candidates=result.image_candidates,
        fetched_at=result.fetched_at,
        unvisited_urls=result.unvisited_urls,
    )
