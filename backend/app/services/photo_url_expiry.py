"""Signed photo links die; a site must not be generated on dead ones.

Facebook's CDN signs every photo URL, and the signature carries its own
deadline (`image_urls.signed_url_expiry`) — measured, five days after the read.
The push re-downloads every photo to re-host it, so a site generated from a
week-old read used to publish with every Facebook photo silently removed by
`push_orchestrator._strip_invalid_images`: a dead link reads as "nothing here",
not as an error.

Generation applies three rules, in this order:

1. **Renew.** A Facebook source whose links die within
   `facebook_photo_url_refresh_hours` is read again, and every link is swapped
   for the fresh one naming the same picture. `photo_identity` is the key: the
   filename is the picture, the signed query one rendition of it, so a renewed
   link is the same photo by construction.
2. **Leave out what is dead.** A link that is dead and could not be renewed —
   the read failed, or the photo has scrolled off the Page since — is cut from
   the source (`source_images.without_images`) so nothing is generated around
   it, and a warning says how many. A dead brand mark falls back to the
   wordmark, keeping its palette.
3. **Say when the rest dies.** A link still alive but due inside the window is
   kept, with a warning naming the deadline, because the push refuses a dead
   link outright rather than stripping it.

Nothing is read when nothing is due, so a fresh read costs a scan of strings.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypeVar

from pydantic import BaseModel

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.content_blocks import SourceContent
from app.services.image_urls import (
    SIGNED_URL_GRACE_SECONDS,
    is_cropped_photo_url,
    photo_identity,
    renew_signed_urls,
    signed_url_expiry,
    signed_urls_in,
)
from app.services.source_images import without_images

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Reads the source again and answers photo identity → a freshly signed URL.
FreshLinks = Callable[[SourceContent], Awaitable[dict[str, str]]]


@dataclass(frozen=True)
class LiveLinks:
    """The source and brand a generation should use, and what it should say."""

    source: SourceContent
    brand: BrandIdentity | None
    warnings: list[str] = field(default_factory=list)


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from _strings(getattr(value, name))
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _rewritten(value: T, rewrite: Callable[[str], str]) -> T:
    """A copy of `value` with `rewrite` applied to every string inside it.
    Unchanged branches are returned as they are, so a copy costs only the
    path down to what changed."""
    if isinstance(value, str):
        return rewrite(value)  # type: ignore[return-value]
    if isinstance(value, BaseModel):
        changed = {}
        for name in type(value).model_fields:
            current = getattr(value, name)
            new = _rewritten(current, rewrite)
            if new is not current:
                changed[name] = new
        return value.model_copy(update=changed) if changed else value
    if isinstance(value, (list, tuple)):
        items = [_rewritten(item, rewrite) for item in value]
        if all(new is old for new, old in zip(items, value)):
            return value
        return type(value)(items)  # type: ignore[return-value]
    if isinstance(value, dict):
        items = {key: _rewritten(item, rewrite) for key, item in value.items()}
        if all(items[key] is item for key, item in value.items()):
            return value
        return items  # type: ignore[return-value]
    return value


def links_due(*values: object, before: float) -> dict[str, float]:
    """Every signed photo link inside `values` that dies before `before`,
    mapped to its deadline."""
    due: dict[str, float] = {}
    for text in (text for value in values for text in _strings(value)):
        for url in signed_urls_in(text):
            expiry = signed_url_expiry(url)
            if expiry is not None and expiry <= before:
                due[url] = expiry
    return due


async def _read_page_again(source: SourceContent) -> dict[str, str]:
    """Read the Page this source came from and name each picture's fresh link.

    The default reader chain, so a configured token is used when there is one.
    Within one read a picture can appear as a crop and as its original; the
    original wins, because that is what the first read kept.
    """
    from app.services.facebook_source import fetch_facebook_page
    from app.services.facebook_urls import parse_ref

    page = await fetch_facebook_page(parse_ref(source.source_ref))
    fresh: dict[str, str] = {}
    for url in page.photo_urls:
        identity = photo_identity(url)
        if identity and (identity not in fresh or is_cropped_photo_url(fresh[identity])):
            fresh[identity] = url
    return fresh


def _without_dead_mark(brand: BrandIdentity, dead: set[str]) -> BrandIdentity:
    """The brand with a dead mark withdrawn: the header falls back to the
    wordmark (`logo_render_ok` is the gate it reads) and the palette stays."""
    updates: dict[str, object] = {}
    if brand.logo_url in dead:
        updates.update(logo_url=None, logo_render_ok=False)
    if brand.favicon_url in dead:
        updates["favicon_url"] = None
    return brand.model_copy(update=updates) if updates else brand


def _when(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%d %b %Y, %H:%M UTC")


async def keep_photo_links_alive(
    source: SourceContent,
    brand: BrandIdentity | None,
    *,
    now: float | None = None,
    fresh_links: FreshLinks | None = None,
) -> LiveLinks:
    """The source and brand with every due photo link renewed or withdrawn.

    `fresh_links` is the test seam; by default a Facebook source is read again.
    Never raises: a failed read leaves rule 2 to do its job, with a warning.
    """
    now = time.time() if now is None else now
    horizon = now + settings.facebook_photo_url_refresh_hours * 3600
    if not links_due(source, brand, before=horizon):
        return LiveLinks(source, brand)

    warnings: list[str] = []
    if source.source_kind == "facebook":
        try:
            fresh = await (fresh_links or _read_page_again)(source)
        except Exception as exc:  # each reader raises its own error type
            logger.info("Photo links: couldn't read %s again (%s)", source.source_ref, exc)
            warnings.append(
                f"The Facebook Page couldn't be read again to renew its photo links ({exc})."
            )
        else:
            source = _rewritten(source, lambda text: renew_signed_urls(text, fresh))
            brand = _rewritten(brand, lambda text: renew_signed_urls(text, fresh))
            logger.info(
                "Photo links: renewed from a fresh read of %s (%d pictures named)",
                source.source_ref, len(fresh),
            )

    dead = set(links_due(source, brand, before=now + SIGNED_URL_GRACE_SECONDS))
    if dead:
        source = without_images(source, dead)
        if brand is not None:
            brand = _without_dead_mark(brand, dead)
        pictures = len({photo_identity(url) or url for url in dead})
        warnings.append(
            f"{pictures} photo{'s' if pictures != 1 else ''} from Facebook "
            f"{'have' if pictures != 1 else 'has'} expired and couldn't be renewed, "
            "so the site was built without "
            f"{'them' if pictures != 1 else 'it'}."
        )
    remaining = links_due(source, brand, before=horizon)
    if remaining:
        warnings.append(
            f"Facebook photo links in this site expire on {_when(min(remaining.values()))}. "
            "Publish before then, or generate again to renew them."
        )
    return LiveLinks(source, brand, warnings)
