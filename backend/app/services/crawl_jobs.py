"""
Crawl job manager — durable + cancellable.

A job lives in SQLite (services/db.py) and tracks: status, options, progress,
and finally the result payload. While running, the manager keeps an in-memory
`CancelToken` per job so the orchestrator can check it between pages and bail.

State machine:

   queued ──► running ──► done
                  │
                  ├──► failed     (uncaught exception)
                  └──► cancelled  (user clicked Cancel)

We deliberately keep this single-process. Multi-process would need either a
real queue (arq/celery) or row-level locking — both overkill for the current
single-tenant install. If/when that changes, the SQL is portable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from app.services.db import connect

logger = logging.getLogger(__name__)


JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]
_TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "failed", "cancelled"})


@dataclass
class CrawlJob:
    """In-memory snapshot of a job — what the API returns."""

    id: str
    entry_url: str
    host: str
    status: JobStatus
    options: dict[str, Any]
    progress: dict[str, Any]   # {pages_done, pages_estimate, current_url, current_step}
    result: dict[str, Any] | None
    error: str | None
    started_at: float | None
    finished_at: float | None
    created_at: float

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    @property
    def elapsed_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at or time.time()
        return end - self.started_at


class CancelToken:
    """Lightweight flag the orchestrator polls. Threadsafe via asyncio lock."""

    def __init__(self) -> None:
        self._flag = False

    def cancel(self) -> None:
        self._flag = True

    @property
    def cancelled(self) -> bool:
        return self._flag


class CrawlJobManager:
    """Process-wide registry of running jobs + their cancel tokens."""

    def __init__(self) -> None:
        self._tokens: dict[str, CancelToken] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # --- writes -----------------------------------------------------------------

    async def create(self, entry_url: str, options: dict[str, Any]) -> CrawlJob:
        job_id = uuid.uuid4().hex
        host = urlparse(entry_url).netloc
        async with connect() as conn:
            await conn.execute(
                """
                INSERT INTO crawl_jobs (id, entry_url, host, status, options_json, progress_json)
                VALUES (?, ?, ?, 'queued', ?, '{}')
                """,
                (job_id, entry_url, host, json.dumps(options, ensure_ascii=False)),
            )
            await conn.commit()
        self._tokens[job_id] = CancelToken()
        logger.info("Created crawl job %s for %s", job_id, entry_url)
        return await self._reload(job_id)

    async def mark_running(self, job_id: str) -> None:
        await self._update(job_id, {"status": "running", "started_at": time.time()})

    async def mark_done(self, job_id: str, result: dict[str, Any]) -> None:
        await self._update(
            job_id,
            {
                "status": "done",
                "finished_at": time.time(),
                "result_json": json.dumps(result, ensure_ascii=False),
            },
        )
        self._tokens.pop(job_id, None)
        self._tasks.pop(job_id, None)

    async def mark_failed(self, job_id: str, error: str) -> None:
        await self._update(
            job_id,
            {
                "status": "failed",
                "finished_at": time.time(),
                "error": error,
            },
        )
        self._tokens.pop(job_id, None)
        self._tasks.pop(job_id, None)

    async def mark_cancelled(self, job_id: str) -> None:
        await self._update(
            job_id,
            {"status": "cancelled", "finished_at": time.time()},
        )
        self._tokens.pop(job_id, None)
        self._tasks.pop(job_id, None)

    async def update_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        async with connect() as conn:
            await conn.execute(
                "UPDATE crawl_jobs SET progress_json = ? WHERE id = ?",
                (json.dumps(progress, ensure_ascii=False), job_id),
            )
            await conn.commit()

    # --- cancellation -----------------------------------------------------------

    def get_cancel_token(self, job_id: str) -> CancelToken | None:
        return self._tokens.get(job_id)

    def is_cancelled(self, job_id: str) -> bool:
        tok = self._tokens.get(job_id)
        return tok.cancelled if tok else False

    async def cancel(self, job_id: str) -> bool:
        """Flip the cancel flag. Orchestrator picks it up between pages.
        Returns True if a running job was flagged; False if the job was already
        terminal or unknown."""
        job = await self.get(job_id)
        if job is None or job.is_terminal:
            return False
        tok = self._tokens.get(job_id)
        if tok:
            tok.cancel()
        # If the job hasn't started yet (status=queued), mark it cancelled now;
        # if it's running, the orchestrator will call mark_cancelled when it
        # observes the flag.
        if job.status == "queued":
            await self.mark_cancelled(job_id)
        return True

    def register_task(self, job_id: str, task: asyncio.Task) -> None:
        self._tasks[job_id] = task

    # --- reads ------------------------------------------------------------------

    async def get(self, job_id: str) -> CrawlJob | None:
        async with connect() as conn:
            cur = await conn.execute(
                "SELECT * FROM crawl_jobs WHERE id = ?", (job_id,)
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    async def delete(self, job_id: str) -> bool:
        """Hard-delete a terminal job + its pages. No-op if non-terminal."""
        job = await self.get(job_id)
        if job is None or not job.is_terminal:
            return False
        async with connect() as conn:
            await conn.execute("DELETE FROM crawl_jobs WHERE id = ?", (job_id,))
            await conn.commit()
        return True

    # --- internals --------------------------------------------------------------

    async def _update(self, job_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values())
        vals.append(job_id)
        async with connect() as conn:
            await conn.execute(
                f"UPDATE crawl_jobs SET {cols} WHERE id = ?", vals
            )
            await conn.commit()

    async def _reload(self, job_id: str) -> CrawlJob:
        job = await self.get(job_id)
        assert job is not None
        return job


def _row_to_job(row) -> CrawlJob:
    return CrawlJob(
        id=row["id"],
        entry_url=row["entry_url"],
        host=row["host"],
        status=row["status"],
        options=_safe_json(row["options_json"]),
        progress=_safe_json(row["progress_json"]),
        result=_safe_json(row["result_json"]) if row["result_json"] else None,
        error=row["error"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        created_at=row["created_at"],
    )


def _safe_json(s: str | None) -> dict[str, Any]:
    if not s:
        return {}
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {}


# Process-global singleton — there's only one site-generator process.
_manager: CrawlJobManager | None = None


def get_manager() -> CrawlJobManager:
    global _manager
    if _manager is None:
        _manager = CrawlJobManager()
    return _manager
