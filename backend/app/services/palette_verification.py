"""Observe the cached public HTML without purging or bypassing its cache.

The CMS account supplies the public URL. Never forward CMS credentials to it.
This confirms one public response, not every CDN location or every site route.
"""
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from app.models.brand import CreativePalette
from app.services.color_system import normalize_hex


class _PublicColors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.colors: dict[str, str] = {}
        self.versioned = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        self.versioned = self.versioned or "wt-color-system-v2" in classes
        if "wt-site" not in classes:
            return
        for declaration in (attributes.get("style") or "").split(";"):
            key, _, value = declaration.partition(":")
            if key.strip() in {"--builder-color-"+name for name in ("primary", "secondary", "accent", "surface")}:
                try:
                    self.colors[key.strip().removeprefix("--builder-color-")] = normalize_hex(value)
                except ValueError:
                    pass


async def verify_public_palette(client, entity_token: str, expected: CreativePalette,
                                *, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    entities = await client.list_entities()
    entity = next((item for item in entities if item.get("entity_api_token") == entity_token), {})
    url = entity.get("public_url")
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return {"state": "unavailable", "message": "This site's public address is not available yet."}
    try:
        # A separate anonymous client: no JWT, builder cookie or cache-busting
        # header/query. Bound downloads so verification stays inexpensive.
        async with httpx.AsyncClient(transport=transport, timeout=15, follow_redirects=True,
                                     max_redirects=3) as http:
            async with http.stream("GET", url) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2_000_000:
                        raise ValueError("Public response too large")
        observed = _PublicColors()
        observed.feed(body.decode("utf-8", errors="replace"))
        matches = observed.versioned and observed.colors == expected.model_dump()
        return {"state": "verified" if matches else "pending", "public_url": url,
                "message": ("The public page now shows the selected colors." if matches else
                            "The public page shows different colors. Refresh live colors to check for newer edits, or check again after its caches refresh.")}
    except (httpx.HTTPError, ValueError):
        return {"state": "unavailable", "public_url": url,
                "message": "The palette is saved, but the public page could not be checked. Try again shortly."}
