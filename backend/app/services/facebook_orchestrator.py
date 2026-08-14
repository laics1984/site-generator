"""Run a Facebook Page read as a background job.

The Facebook twin of `crawl_orchestrator.run_crawl_job`, sharing the same job
manager and the same `crawl_jobs` table so the existing progress polling,
result caching, cancellation and reuse all work unchanged — which is also what
gives the slow public-render fallback progress feedback it would not have had
as a synchronous request.

**The access token is never persisted.** `options_json` records only that one
was supplied; the token itself lives in a module-level dict for the lifetime of
the job and is popped on every exit path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.services.crawl_jobs import CrawlJobManager, get_manager
from app.services.facebook_source import (
    FacebookSourceError,
    fetch_facebook_page,
    to_preview_payload,
)
from app.services.facebook_urls import FacebookUrlError, parse_ref

logger = logging.getLogger(__name__)

# job_id → access token. In memory only, popped when the job leaves flight.
_TOKENS: dict[str, str] = {}


def stash_token(job_id: str, token: str | None) -> None:
    """Hold a request-scoped token for the duration of one job."""
    if token and token.strip():
        _TOKENS[job_id] = token.strip()


def _take_token(job_id: str) -> str | None:
    return _TOKENS.pop(job_id, None)


def forget_token(job_id: str) -> None:
    """Drop a stashed token — called when a job is cancelled or deleted."""
    _TOKENS.pop(job_id, None)


async def run_facebook_job(job_id: str) -> None:
    """Read the Page behind this job's entry_url and persist the result.

    Never raises into the `asyncio.create_task` caller; failures are recorded
    against the job's `error` field, same contract as `run_crawl_job`.
    """
    mgr: CrawlJobManager = get_manager()
    job = await mgr.get(job_id)
    if job is None:
        logger.warning("run_facebook_job called for unknown job %s", job_id)
        _take_token(job_id)
        return
    if job.status != "queued":
        logger.warning(
            "run_facebook_job called for job %s with status=%s — skipping",
            job_id, job.status,
        )
        _take_token(job_id)
        return

    token = _take_token(job_id)
    await mgr.mark_running(job_id)

    async def emit_progress(percent: int, step: str) -> None:
        # A Page read has no page count — `pages_done` stays 0 so the crawl UI
        # can't render a fetched-pages tally that would mean nothing here. The
        # readable step and a percent drive the Facebook variant instead.
        await mgr.update_progress(
            job_id,
            {
                "pages_done": 0,
                "percent": percent,
                "current_url": job.entry_url,
                "current_step": step,
            },
        )

    def cancelled() -> bool:
        return mgr.is_cancelled(job_id)

    try:
        ref = parse_ref(job.entry_url)
        await emit_progress(10, "Finding the Page")
        page = await fetch_facebook_page(
            ref, access_token=token, on_progress=emit_progress
        )
        if cancelled():
            await mgr.mark_cancelled(job_id)
            return
        await emit_progress(70, "Reading the profile picture")
        payload: dict[str, Any] = await to_preview_payload(page)
        payload["fetched_at"] = time.time()
    except (FacebookUrlError, FacebookSourceError) as exc:
        logger.info("Facebook job %s failed: %s", job_id, exc)
        if cancelled():
            await mgr.mark_cancelled(job_id)
        else:
            await mgr.mark_failed(job_id, str(exc))
        return
    except asyncio.CancelledError:
        logger.info("Facebook job %s asyncio-cancelled", job_id)
        await mgr.mark_cancelled(job_id)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Facebook job %s crashed", job_id)
        await mgr.mark_failed(job_id, f"{type(exc).__name__}: {exc}")
        return
    finally:
        forget_token(job_id)

    if cancelled():
        await mgr.mark_cancelled(job_id)
        return

    await mgr.mark_done(job_id, payload)
    logger.info(
        "Facebook job %s done — '%s' via %s (%d posts, %d reviews, partial=%s)",
        job_id, page.name, page.fetched_via, len(page.posts), len(page.reviews), page.partial,
    )
