"""
Diversity engine — steers consecutive generations away from identical chrome.

Problem: seeded rotation alone makes each *brand* idempotent, but a shop that
generates ten sites in an afternoon still sees the same header/footer pairing
whenever the fit tables agree — "different colours on the same template".

Fix: record every site's chrome picks in SQLite (same file as crawl jobs) and
let the director's picker avoid what was chosen most recently — both by the
same site (a *regeneration* should explore, not repeat itself) and by other
recent sites (a *batch* should not converge). Selection stays fit-first: the
picker only ever chooses among the candidates the fit tables already approved,
so avoidance can bend taste but never override it.

Everything here is fail-open: a missing/locked DB, a fresh install, or the
`diversity_engine_enabled` kill switch all degrade to "no history", which
makes pick_diverse() return the seeded default. Generation never blocks on
this module.
"""

from __future__ import annotations

import hashlib
import logging
import time

from app.config import settings

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS design_choices (
    site_key    TEXT NOT NULL,
    area        TEXT NOT NULL,
    choice      TEXT NOT NULL,
    chosen_at   REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (site_key, area, chosen_at)
);
CREATE INDEX IF NOT EXISTS idx_design_choices_area_time
    ON design_choices(area, chosen_at DESC);
"""

_SCHEMA_READY = False


async def _ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    from app.services import db

    async with db.connect() as conn:
        await conn.executescript(_SCHEMA)
        await conn.commit()
    _SCHEMA_READY = True


def seeded_index(seed: str, salt: str, size: int) -> int:
    """Stable index into a candidate list. md5 (not hash()) so the pick
    survives interpreter restarts — regeneration must be idempotent when no
    history says otherwise. Mirrors hero_director._rotation_index."""
    if size <= 0:
        return 0
    digest = hashlib.md5(f"{seed}:{salt}".encode()).hexdigest()
    return int(digest[:8], 16) % size


def pick_diverse(
    candidates: list[str],
    *,
    seed: str,
    salt: str,
    avoid: set[str] | None = None,
) -> str:
    """Pick one of `candidates` (already fit-ordered / fit-approved).

    Base pick is seeded (idempotent per brand). When the seeded pick is in
    `avoid` (recently used), walk forward through the rotation to the first
    non-avoided candidate. If everything is avoided — small vocabularies wrap
    fast — the seeded pick stands: fit beats novelty at the margin.
    """
    if not candidates:
        raise ValueError("pick_diverse needs at least one candidate")
    start = seeded_index(seed, salt, len(candidates))
    if not avoid:
        return candidates[start]
    for offset in range(len(candidates)):
        candidate = candidates[(start + offset) % len(candidates)]
        if candidate not in avoid:
            return candidate
    return candidates[start]


async def recent_choices(area: str, *, site_key: str, window: int = 6) -> set[str]:
    """Choices to steer away from for `area`: this site's own latest pick
    (regeneration should explore) plus the last `window` picks across all
    sites (batches shouldn't converge). Fail-open: any DB trouble → empty set."""
    if not settings.diversity_engine_enabled:
        return set()
    try:
        from app.services import db

        await _ensure_schema()
        avoid: set[str] = set()
        async with db.connect() as conn:
            cursor = await conn.execute(
                "SELECT choice FROM design_choices WHERE site_key = ? AND area = ? "
                "ORDER BY chosen_at DESC LIMIT 1",
                (site_key, area),
            )
            row = await cursor.fetchone()
            if row:
                avoid.add(row["choice"])
            cursor = await conn.execute(
                "SELECT choice FROM design_choices WHERE area = ? "
                "ORDER BY chosen_at DESC LIMIT ?",
                (area, window),
            )
            rows = await cursor.fetchall()
        # The most recent global picks weigh in only when the vocabulary is
        # big enough that avoiding them still leaves fit-approved room; the
        # picker itself falls back to the seeded default when saturated.
        avoid.update(r["choice"] for r in rows[: max(1, window // 2)])
        return avoid
    except Exception as exc:  # noqa: BLE001 — advisory signal, never blocks
        logger.debug("diversity history unavailable (%s); proceeding without", exc)
        return set()


async def record_choice(area: str, choice: str, *, site_key: str) -> None:
    """Append one pick to the history. Fail-open like recent_choices."""
    if not settings.diversity_engine_enabled:
        return
    try:
        from app.services import db

        await _ensure_schema()
        async with db.connect() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO design_choices "
                "(site_key, area, choice, chosen_at) VALUES (?, ?, ?, ?)",
                (site_key, area, choice, time.time()),
            )
            await conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("diversity history write failed (%s); ignored", exc)
