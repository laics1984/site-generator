"""The styles bridge only: no page, media, menu, favicon or content writes."""
from app.models.brand import ColorPalette, CreativePalette
from app.services.cms_client import CmsApiError
from app.services.palette_updates import current_theme, merge_palette_styles, PalettePatch
from app.services.theme import apply_creative_palette


async def publish_palette(client, *, site, expected_version: str, project_target: str,
                          entity_token: str) -> dict:
    data = (await client.get_builder_styles()).get("data") or {}
    existing = data.get("builder_styles") or {}
    layout = data.get("layout") or {}
    if existing.get("colorSystemVersion") != 2:
        raise CmsApiError(409, "This live site uses an older color system; its styles were kept.")
    if str(layout.get("versionId")) != expected_version:
        raise CmsApiError(409, "The live design changed. Refresh its colors and review your palette again.")
    candidate = current_theme(site)
    original = candidate.model_copy(update={
        "palette": ColorPalette.model_validate(existing["colors"]),
        "buttons": candidate.buttons.model_copy(update=existing.get("buttons", {})),
        "page": candidate.page.model_copy(update={"background": (existing.get("page") or {}).get("background", candidate.page.background)}),
    })
    updated = apply_creative_palette(original, CreativePalette(**candidate.palette.model_dump()))
    merged = merge_palette_styles(existing, original, updated)
    project = None
    target_entity = {}
    if site.generation_project_id:
        from app.services.generation_projects import get_project
        project = await get_project(site.generation_project_id)
        entities = await client.list_entities()
        target_entity = next((e for e in entities if e.get("entity_api_token") == entity_token), {})
    saved = await client.update_builder_styles(merged, expected_layout_version_id=expected_version)
    result = {"success": True, "layout_version_id": (saved.get("data", {}).get("layout") or {}).get("versionId"),
              "cache_refresh_pending": True, "project_updated": False}
    if site.generation_project_id:
        from app.services.generation_projects import save_project_palette
        if project and project.get("cms_target") == project_target and str(project.get("entity_id")) == str(target_entity.get("entity_id")):
            try:
                await save_project_palette(site.generation_project_id, PalettePatch(
                    theme=updated, builder_styles=merged, primary_color=updated.palette.primary,
                    secondary_color=updated.palette.secondary))
                result["project_updated"] = True
            except Exception:
                result["warning"] = "The live palette was saved, but the staged preview could not be updated. Retry publishing the palette before generating remaining pages."
    return result
