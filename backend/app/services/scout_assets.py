"""Freeze the media for a Scout demo and rehost it from the saved bytes."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
from urllib.parse import urldefrag, urlsplit

import httpx
from pydantic import BaseModel, Field

from app.models.builder_schema import BuilderElementContent, GeneratedSite
from app.services.asset_fetch import ASSET_HEADERS
from app.services.cms_client import CmsApiError, CmsClient
from app.services.nav_extraction import is_document_href
from app.services.push_orchestrator import (
    _CMS_NATIVE_MIME_TO_EXT,
    _DOCUMENT_MIME_MAP,
    _IMAGE_MIME_MAP,
    _PUSH_CONCURRENCY,
    _ResolveSkip,
    _StorageBudget,
    _assert_fetchable,
    _coerce_to_cms_image,
    _decode_data_url,
    _document_ext,
    _extract_bg_photo_urls,
    _filename_from_url,
    _rewrite_bg_photo_urls,
    _site_image_sources,
    _site_roots,
    _store_bytes,
    MediaUpload,
)

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_BYTES = 48 * 1024 * 1024
MAX_FILES = 160
BACKGROUND_KEYS = ("backgroundImage", "background")


class SavedAsset(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=120)
    data: str = Field(min_length=4, max_length=((MAX_FILE_BYTES + 2) // 3) * 4)

    def unpack(self) -> tuple[bytes, str, str]:
        try:
            data = base64.b64decode(self.data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("A saved asset has invalid file data.") from exc
        if not data or len(data) > MAX_FILE_BYTES or any(c in self.filename for c in ("/", "\\", "\r", "\n")):
            raise ValueError("A saved asset has invalid file data or filename.")
        return data, self.content_type, self.filename


def asset_key(src: str) -> str:
    # The browser does not send the fragment of an HTTP URL.
    reference = src if src.startswith("data:") else urldefrag(src).url
    return hashlib.sha256(reference.encode()).hexdigest()


def _responsive_styles(site: GeneratedSite):
    for root in _site_roots(site):
        for node in root.walk():
            if node.responsiveStyles:
                for styles in (node.responsiveStyles.mobile, node.responsiveStyles.tablet):
                    if styles:
                        yield styles


def references(site: GeneratedSite) -> dict[str, bool]:
    """Reference to asset type, with True for images and False for documents."""
    sources = dict.fromkeys(_site_image_sources(site), True)
    for styles in _responsive_styles(site):
        for prop in BACKGROUND_KEYS:
            sources.update(dict.fromkeys(_extract_bg_photo_urls(styles.get(prop)), True))
    for root in _site_roots(site):
        for node in root.walk():
            if node.type != "link" or not isinstance(node.content, BuilderElementContent):
                continue
            href = node.content.href
            if not href:
                continue
            extension = _document_ext(href)
            if extension in _DOCUMENT_MIME_MAP:
                sources.setdefault(href, False)
            elif is_document_href(href):
                raise ValueError(f"The CMS cannot store the .{extension} download in this draft.")
    return sources


async def _read_reference(src: str, *, image: bool, client: httpx.AsyncClient) -> tuple[bytes, str, str]:
    if image and src.startswith("data:image/"):
        return _decode_data_url(src)
    parts = urlsplit(src)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
        raise ValueError("The draft contains an asset without an absolute web address.")
    await _assert_fetchable(src)
    async with client.stream("GET", src, headers=ASSET_HEADERS) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").partition(";")[0].lower()
        if content_type.startswith(("text/html", "application/xhtml")):
            raise ValueError("An asset address returned a web page.")
        data = bytearray()
        async for chunk in response.aiter_bytes():
            if len(data) + len(chunk) > MAX_FILE_BYTES:
                raise ValueError("An asset exceeds the saved site file limit.")
            data.extend(chunk)
    ext = _document_ext(src)
    if not image:
        content_type = _DOCUMENT_MIME_MAP[ext]
    elif not content_type.startswith("image/"):
        content_type = _IMAGE_MIME_MAP.get(ext, content_type)
    return bytes(data), content_type, _filename_from_url(src) or "asset"


async def freeze_assets(site: GeneratedSite) -> dict[str, SavedAsset]:
    sources = {asset_key(src): (src, image) for src, image in references(site).items()}
    if len(sources) > MAX_FILES:
        raise ValueError("The draft exceeds the saved site asset count limit.")
    # Batches cap both the concurrent requests and the raw bytes held at once.
    entries = list(sources.items())
    saved: dict[str, SavedAsset] = {}
    total = 0
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
        async def read(src: str, image: bool) -> tuple[bytes, str, str]:
            try:
                data, mime, filename = await asyncio.wait_for(
                    _read_reference(src, image=image, client=client), timeout=25,
                )
                if image:
                    result = await asyncio.to_thread(_coerce_to_cms_image, data, mime, filename)
                    if result is None:
                        raise ValueError("The image format could not be saved.")
                    data, mime, filename = result
                if not data or len(data) > MAX_FILE_BYTES:
                    raise ValueError("An asset is empty or too large.")
                return data, mime, filename
            except (httpx.HTTPError, _ResolveSkip, TimeoutError, ValueError) as exc:
                name = "embedded image" if src.startswith("data:") else (_filename_from_url(src) or "asset")
                detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                raise ValueError(f"Could not save {name[:80]}: {detail}") from exc

        for i in range(0, len(entries), _PUSH_CONCURRENCY):
            batch = entries[i : i + _PUSH_CONCURRENCY]
            results = await asyncio.gather(
                *(read(src, image) for _, (src, image) in batch), return_exceptions=True
            )
            for (key, _), result in zip(batch, results, strict=True):
                if isinstance(result, BaseException):
                    raise result
                data, mime, filename = result
                total += len(data)
                if total > MAX_BUNDLE_BYTES:
                    raise ValueError("The draft exceeds the saved site asset size limit.")
                saved[key] = SavedAsset(
                    filename=filename[:255], content_type=mime, data=base64.b64encode(data).decode("ascii")
                )
    return saved


def unpack_assets(site: GeneratedSite, saved: dict[str, SavedAsset]) -> dict[str, tuple[bytes, str, str]]:
    """Validate complete coverage before making any production calls."""
    sources = references(site)
    if len(saved) > MAX_FILES or {asset_key(src) for src in sources} != saved.keys():
        raise ValueError("The saved files do not match the draft's asset references.")
    assets = {}
    total = 0
    for src, image in sources.items():
        data, mime, filename = saved[asset_key(src)].unpack()
        if (image and mime not in _CMS_NATIVE_MIME_TO_EXT) or (
            not image and mime != _DOCUMENT_MIME_MAP[_document_ext(src)]
        ):
            raise ValueError("A saved asset has an unexpected file type.")
        total += len(data)
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("The saved files exceed the site asset size limit.")
        assets[src] = (data, mime, filename)
    return assets


async def upload_saved_assets(
    client: CmsClient, entity_token: str, assets: dict[str, tuple[bytes, str, str]]
) -> MediaUpload:
    """Publish uses the bundle exclusively and fails before landing any pages if an upload fails."""
    if not assets:
        return MediaUpload({}, set())
    try:
        budget = _StorageBudget.from_usage(await client.get_storage_usage(entity_token))
    except CmsApiError:
        budget = _StorageBudget()
    rewrites: dict[str, str] = {}
    reused = 0
    for src, (data, mime, filename) in assets.items():
        stored = await _store_bytes(
            client, entity_token, file_bytes=data, content_type=mime,
            filename=filename, budget=budget,
        )
        if stored is None:
            raise CmsApiError(422, "A saved asset could not be stored. Check the production site's media quota and upload settings.")
        rewrites[src], existing = stored
        reused += existing
    return MediaUpload(rewrites, set(), reused)


def rewrite_responsive_assets(site: GeneratedSite, rewrites: dict[str, str]) -> None:
    for styles in _responsive_styles(site):
        for prop in BACKGROUND_KEYS:
            value = styles.get(prop)
            if isinstance(value, str):
                styles[prop] = _rewrite_bg_photo_urls(value, rewrites)
