"""What the source said, and whether the generated site says it too.

A pure read of a finished site against the source it was built from: each fact
(phone, email, address, hours, website), each usable photo, each review and
each offering the read found, reported as placed or as missing with a reason.
It is advisory, like `ux_audit`: it describes a site and never changes or
blocks one. A gap it reports is something to raise in the generator, not a
build to fail.

Photos are counted from the read's usable pool (`SourceContent.image_metadata`
after `facebook_photo_selection`); a photo the read itself rejected is
reported where the read reports it, in the pre-generation inventory, with its
reason. "Placed" is asked the way every "already shown" test asks it, by
`image_urls.image_identity`, over the same tree walk the push uploads
(`push_orchestrator._collect_image_srcs`), so a photo counts as on the site
exactly when it would be published.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from urllib.parse import urlparse

from app.models.builder_schema import BuilderElement, BuilderElementContent, GeneratedSite
from app.models.content_blocks import ImageMetadata, SourceContent
from app.models.coverage import CoverageCount, CoverageItem, SourceCoverage
from app.models.facebook import FacebookPage
from app.services.image_urls import image_identity

# A phone number is the same number however it is spaced or prefixed: compare
# the trailing digits, which survive "+60 3-1234 5678" vs "03 1234 5678".
_PHONE_TAIL_DIGITS = 8
# An address is rewritten by nobody (the authority pass copies it), but a
# template may split it across lines or drop the country: most of its words
# appearing is what "the site states the address" means.
_ADDRESS_MIN_SHARE = 0.7
_REVIEW_PROBE_CHARS = 40


def _trees(site: GeneratedSite) -> Iterator[BuilderElement]:
    """Every element a visitor of the primary site sees: page bodies, header,
    footer. A translated page is the same content in another language."""
    for page in site.pages:
        if page.locale:
            continue
        yield from page.body_schema.elements
    for chrome in (site.header_schema, site.footer_schema):
        if chrome is not None:
            yield chrome


def _norm(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


class _SiteText:
    """The site's words and links, read once."""

    def __init__(self, site: GeneratedSite) -> None:
        texts: list[str] = []
        hrefs: list[str] = []
        for root in _trees(site):
            for node in root.walk():
                content = node.content
                if isinstance(content, BuilderElementContent):
                    texts.extend(v for v in (content.innerText, content.alt, content.caption) if v)
                    if content.href:
                        hrefs.append(content.href)
        joined = "\n".join(texts + hrefs)
        self.words = _norm(joined)
        self.digits = re.sub(r"\D", "", joined)
        self.lower = joined.lower()
        self.hosts = {urlparse(h).hostname or "" for h in hrefs}

    def has_phone(self, phone: str) -> bool:
        tail = re.sub(r"\D", "", phone)[-_PHONE_TAIL_DIGITS:]
        return bool(tail) and tail in self.digits

    def has_words(self, text: str, min_share: float = 1.0) -> bool:
        words = _norm(text).split()
        if not words:
            return False
        if min_share >= 1.0:
            return " ".join(words) in self.words
        present = set(self.words.split())
        return sum(word in present for word in words) / len(words) >= min_share

    def has_host(self, url: str) -> bool:
        host = (urlparse(url if "//" in url else f"//{url}").hostname or "").removeprefix("www.")
        return bool(host) and any(h.removeprefix("www.") == host for h in self.hosts)


def _fact(label: str, value: str | None, placed: bool) -> CoverageItem | None:
    if not value:
        return None
    return CoverageItem(kind="fact", label=label, value=value, placed=placed,
                        reason="" if placed else "not on any page")


def _facts(page: FacebookPage, text: _SiteText) -> Iterable[CoverageItem | None]:
    yield _fact("Phone", page.phone, bool(page.phone) and text.has_phone(page.phone or ""))
    email = page.primary_email
    yield _fact("Email", email, bool(email) and (email or "").lower() in text.lower)
    address = page.address_line
    yield _fact("Address", address, bool(address) and text.has_words(address or "", _ADDRESS_MIN_SHARE))
    hours = page.hours_text
    yield _fact("Opening hours", hours, bool(page.hours) and page.hours[0].opens in text.lower)
    yield _fact("Website", page.website, bool(page.website) and text.has_host(page.website or ""))


def _photo_reason(meta: ImageMetadata, placed_ids: set[str]) -> str:
    from app.services.image_match import must_show_whole
    from app.services.facebook_photo_selection import photo_rejection_reason

    if image_identity(meta) in placed_ids:
        return ""
    if must_show_whole(meta):
        return "its words are its content, and no slot shows it whole"
    return photo_rejection_reason(meta, "gallery") or "no slot left for it"


def _photos(source: SourceContent, site: GeneratedSite) -> Iterable[CoverageItem]:
    from app.services.push_orchestrator import _collect_image_srcs

    srcs: dict[str, BuilderElement] = {}
    for root in _trees(site):
        _collect_image_srcs(root, srcs)
    by_url = {meta.url: meta for meta in source.image_metadata}
    placed_ids = {image_identity(by_url.get(url) or ImageMetadata(url=url)) for url in srcs}
    for meta in source.image_metadata:
        reason = _photo_reason(meta, placed_ids)
        yield CoverageItem(kind="photo", label=meta.url, placed=not reason, reason=reason)


def _reviews(page: FacebookPage, text: _SiteText) -> Iterable[CoverageItem]:
    for review in page.reviews:
        placed = text.has_words(review.text[:_REVIEW_PROBE_CHARS])
        yield CoverageItem(kind="review", label=review.author, value=review.text[:120],
                           placed=placed, reason="" if placed else "not quoted on any page")


def _offerings(source: SourceContent, text: _SiteText) -> Iterable[CoverageItem]:
    knowledge = source.page_knowledge
    for offering in knowledge.offerings if knowledge else []:
        placed = text.has_words(offering.name)
        yield CoverageItem(kind="offering", label=offering.name, placed=placed,
                           reason="" if placed else "not named on any page")


def coverage_report(
    source: SourceContent, site: GeneratedSite, facts: FacebookPage | None = None,
) -> SourceCoverage:
    text = _SiteText(site)
    items: list[CoverageItem] = []
    if facts is not None:
        items.extend(item for item in _facts(facts, text) if item is not None)
        items.extend(_reviews(facts, text))
    items.extend(_offerings(source, text))
    items.extend(_photos(source, site))
    summary: dict[str, CoverageCount] = {}
    for item in items:
        count = summary.setdefault(item.kind, CoverageCount())
        count.read += 1
        count.placed += item.placed
    return SourceCoverage(items=items, summary=summary)
