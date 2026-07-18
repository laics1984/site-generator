"""
SQLite-backed durable state for long-running crawls.

We pick SQLite over Postgres because:
- Site-generator is single-tenant, single-host today
- Zero ops surface — file on disk, no separate service to run
- WAL mode + sane defaults handles ~100 writes/sec which is way past our needs

If/when this becomes multi-tenant or multi-process, swap the DSN to Postgres;
the SQL is portable.

The DB file lives under `data/sitegen.db` inside the container. Bootstrap is
idempotent — `init_db()` is safe to call on every app start.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)


# Path comes from config (SITEGEN_DB_PATH); defaults to the container's data volume.
DB_PATH = Path(settings.sitegen_db_path)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS crawl_jobs (
    id              TEXT PRIMARY KEY,
    entry_url       TEXT NOT NULL,
    host            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',
        -- queued | running | done | failed | cancelled
    options_json    TEXT NOT NULL DEFAULT '{}',
    progress_json   TEXT NOT NULL DEFAULT '{}',
        -- { pages_done, pages_estimate, current_url, current_step, error }
    result_json     TEXT,
        -- final ScrapeResult-equivalent payload, populated when status=done
    error           TEXT,
    started_at      REAL,
    finished_at     REAL,
    created_at      REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_created
    ON crawl_jobs(status, created_at);

CREATE TABLE IF NOT EXISTS crawl_pages (
    job_id          TEXT NOT NULL,
    canonical_url   TEXT NOT NULL,
    depth           INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',
        -- queued | fetching | done | skipped | failed
    http_status     INTEGER,
    fetch_path      TEXT,
        -- 'httpx' | 'playwright'
    content_hash    TEXT,
    payload_json    TEXT,
        -- the per-page SourceContent dump
    error           TEXT,
    fetched_at      REAL,
    PRIMARY KEY (job_id, canonical_url),
    FOREIGN KEY (job_id) REFERENCES crawl_jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pages_job_status
    ON crawl_pages(job_id, status);
CREATE INDEX IF NOT EXISTS idx_pages_job_hash
    ON crawl_pages(job_id, content_hash);
"""


_BOOTSTRAPPED = False
_BOOTSTRAP_LOCK = asyncio.Lock()


async def init_db() -> None:
    """Create the DB file + schema if missing. Idempotent."""
    global _BOOTSTRAPPED
    async with _BOOTSTRAP_LOCK:
        if _BOOTSTRAPPED:
            return
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(DB_PATH) as conn:
            # WAL mode = better concurrent reads while one writer is busy.
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            # synchronous=NORMAL is safe with WAL and a lot faster than FULL.
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.executescript(_SCHEMA)
            await conn.commit()
        _BOOTSTRAPPED = True
        logger.info("SQLite ready at %s", DB_PATH)


@asynccontextmanager
async def connect() -> AsyncIterator[aiosqlite.Connection]:
    """Open a connection with the PRAGMAs we need set up."""
    if not _BOOTSTRAPPED:
        await init_db()
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = aiosqlite.Row
        yield conn
