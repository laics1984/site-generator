"""Durable two-page preview plans; credentials and CMS tokens are never stored."""

from __future__ import annotations

import json
from uuid import uuid4

from app.models.builder_schema import GeneratedSite
from app.services.db import connect


async def create_project(request: dict, site: GeneratedSite) -> str:
    project_id = str(uuid4())
    async with connect() as db:
        await db.execute(
            "INSERT INTO generation_projects (id, request_json, preview_site_json) VALUES (?, ?, ?)",
            (project_id, json.dumps(request), site.model_dump_json()),
        )
        await db.commit()
    return project_id


async def get_project(project_id: str) -> dict | None:
    async with connect() as db:
        cursor = await db.execute("SELECT * FROM generation_projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
    if row is None:
        return None
    project = dict(row)
    for name in ("request_json", "preview_site_json"):
        project[name.removesuffix("_json")] = json.loads(project.pop(name))
    async with connect() as db:
        cursor = await db.execute(
            "SELECT slug, page_id, published FROM generation_project_pages WHERE project_id=?", (project_id,)
        )
        pages = await cursor.fetchall()
        project["page_ids"] = {row["slug"]: row["page_id"] for row in pages}
        project["published_slugs"] = [row["slug"] for row in pages if row["published"]]
    return project


async def list_projects() -> list[dict]:
    async with connect() as db:
        cursor = await db.execute(
            "SELECT id, cms_target, entity_id, public_url, status, created_at, updated_at "
            "FROM generation_projects ORDER BY updated_at DESC LIMIT 50"
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def bind_project(project_id: str, *, target: str, entity_id: str, public_url: str | None) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE generation_projects SET cms_target=?, entity_id=?, public_url=?, "
            "status='preview_targeted', updated_at=strftime('%s','now') WHERE id=?",
            (target, entity_id, public_url, project_id),
        )
        await db.commit()


async def record_page(project_id: str, slug: str, page_id: str) -> None:
    async with connect() as db:
        await db.execute(
            "INSERT INTO generation_project_pages (project_id, slug, page_id) VALUES (?, ?, ?) "
            "ON CONFLICT(project_id, slug) DO UPDATE SET page_id=excluded.page_id",
            (project_id, slug, page_id),
        )
        await db.commit()


async def mark_page_published(project_id: str, slug: str) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE generation_project_pages SET published=1 WHERE project_id=? AND slug=?",
            (project_id, slug),
        )
        await db.commit()


async def mark_preview_published(project_id: str) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE generation_projects SET status='preview_published', updated_at=strftime('%s','now') WHERE id=?",
            (project_id,),
        )
        await db.commit()


async def mark_complete(project_id: str) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE generation_projects SET status='complete', updated_at=strftime('%s','now') WHERE id=?",
            (project_id,),
        )
        await db.commit()
