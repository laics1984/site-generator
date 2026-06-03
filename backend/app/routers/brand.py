"""
Brand-related endpoints: extract a palette from an uploaded logo, or fetch a
logo + palette from a URL (favicon / og:image).
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.models.brand import BrandIdentity
from app.services.logo import (
    LogoExtraction,
    extract_palette_from_image_bytes,
    fetch_logo_from_url,
)
from app.services.theme import build_theme

router = APIRouter(prefix="/api/brand", tags=["brand"])


@router.post("/extract-from-upload")
async def extract_from_upload(
    file: UploadFile = File(..., description="Logo image (PNG/JPG/WebP/SVG)"),
    name: str = "",
    mood: str = "modern",
) -> dict:
    """
    Extract a brand palette from an uploaded logo. Returns the palette + a
    preview theme so the frontend can render a swatch panel before generation.
    """
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty upload")

    try:
        extraction = extract_palette_from_image_bytes(contents)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not parse image: {exc}") from exc

    theme = build_theme(extraction.seed_hex, mood=mood)  # type: ignore[arg-type]

    return {
        "brand": _brand_payload(extraction, name=name, mood=mood),
        "theme_preview": theme.to_builder_styles(),
        "google_fonts": theme.typography.google_fonts,
    }


@router.post("/extract-from-url")
async def extract_from_url(url: str, name: str = "", mood: str = "modern") -> dict:
    """
    Best-effort logo + palette fetch from a public URL. Tries favicon paths.
    """
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must be http(s)://")

    image_bytes = await fetch_logo_from_url(url)
    if not image_bytes:
        raise HTTPException(
            status_code=404,
            detail="No logo found at common locations on that URL",
        )
    try:
        extraction = extract_palette_from_image_bytes(image_bytes)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not parse image: {exc}") from exc

    theme = build_theme(extraction.seed_hex, mood=mood)  # type: ignore[arg-type]
    return {
        "brand": _brand_payload(extraction, name=name, mood=mood),
        "theme_preview": theme.to_builder_styles(),
        "google_fonts": theme.typography.google_fonts,
    }


def _brand_payload(
    extraction: LogoExtraction, *, name: str, mood: str
) -> dict:
    brand = BrandIdentity(
        name=name or "Untitled",
        logo_data_url=extraction.logo_data_url,
        extracted_palette=extraction.palette,
        mood=mood,  # type: ignore[arg-type]
    )
    return brand.model_dump(mode="json")
