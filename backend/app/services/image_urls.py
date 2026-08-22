"""
Image URL resolution shared by the scraper and the logo detector.

These are the low-level, DOM-in/URL-out helpers: pull the best `src` off an
`<img>` (srcset, `<picture>`, lazy-load attributes), absolutize it, upgrade
known image-CDN transform URLs to a full-size variant, and recognise the
filename patterns that mark an icon or a logo.

They live here rather than in `scraper.py` because `logo_extraction.py` needs
exactly the same resolution — a logo hidden behind a srcset or a Wix blur-up
placeholder has to resolve identically to any other image — and `scraper.py`
imports `logo_extraction`, so the dependency has to point this way.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlparse

from bs4 import Tag

_IMG_EXT_OK = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
_LOGO_HINTS = ("logo", "brandmark", "wordmark", "header-logo")
_BAD_IMG_HINTS = (
    "tracking",
    "pixel",
    "spacer",
    "blank",
    "sprite",
    "1x1",
    "loader",
    "loading",
)


# Wix bakes the image transform into the URL PATH, e.g.
#   …/media/{id}/v1/fill/w_119,h_79,al_c,q_80,…,blur_2,enc_avif,quality_auto/{file}
# so a scraped <img src> (or srcset entry) frequently points at a tiny, blurred
# blur-up placeholder rather than the real photo. We rewrite it to a crisp,
# high-res variant with the blur removed. The media id encodes the original
# dimensions (…_d_{W}_{H}…), so we can cap the long edge while preserving aspect
# — the untransformed original can be 20 MB+, which would blow the CMS upload cap.
_WIX_MEDIA_RE = re.compile(
    r"^(https?://static\.wixstatic\.com/media/([^/?#]+))(?:/v1/[^?#]*)?",
    re.IGNORECASE,
)
_WIX_DIMS_RE = re.compile(r"_d_(\d+)_(\d+)")
_WIX_MAX_EDGE = 2560


def upgrade_source_image_url(url: str) -> str:
    """Rewrite known image-CDN transform URLs to a crisp, full-size variant.

    Only matches unambiguous image-CDN transform URLs, so it is a no-op for page
    links — safe to run on every absolutized URL.
    """
    m = _WIX_MEDIA_RE.match(url)
    if not m:
        return url
    base, media_id = m.group(1), m.group(2)
    dims = _WIX_DIMS_RE.search(media_id)
    if dims:
        ow, oh = int(dims.group(1)), int(dims.group(2))
        if ow > 0 and oh > 0:
            # Cap the long edge and derive the short edge by FLOOR division —
            # Wix validates the requested dims against the original aspect and
            # 403s if the short edge doesn't match its own floor(…) computation.
            if ow >= oh:
                tw = min(ow, _WIX_MAX_EDGE)
                th = max(1, oh * tw // ow)
            else:
                th = min(oh, _WIX_MAX_EDGE)
                tw = max(1, ow * th // oh)
            return f"{base}/v1/fill/w_{tw},h_{th},al_c,q_90/{media_id}"
    # Original dimensions unknown → bare original (Wix serves it; usually small).
    return base


def absolute_url(base: str, src: str) -> str | None:
    if not src or src.startswith("data:"):
        return None
    return upgrade_source_image_url(urljoin(base, src))


def looks_like_icon(url: str, alt: str) -> bool:
    low = url.lower()
    if any(h in low for h in _BAD_IMG_HINTS):
        return True
    if "favicon" in low:
        return True
    if alt and len(alt) > 0 and alt.lower() in {"icon", "logo icon"}:
        return True
    return False


# Matches background-image / background shorthand containing a url().
# Handles quoted and unquoted URLs, with optional whitespace.
# Examples matched:
#   background-image: url("https://example.com/hero.jpg")
#   background-image: url('https://example.com/hero.jpg')
#   background: #333 url(https://example.com/banner.webp) no-repeat center
#
# Lives here rather than in scraper.py because section_extraction.py needs the
# same test to recognise a badge tile built from a styled div, and scraper.py
# imports section_extraction — so the dependency has to point this way.
BG_URL_RE = re.compile(
    r'background(?:-image)?\s*:[^;{]*url\(\s*["\']?([^"\')\s]+)["\']?\s*\)',
    re.IGNORECASE,
)


# Separators a filename uses between words, plus the percent-decoded space.
_FILENAME_WORD_SEP_RE = re.compile(r"[\s\-_+.]+")
# "@2x"/"@3x" density suffixes and "-1024x768" dimension suffixes are packaging
# noise, never part of the name.
_FILENAME_NOISE_RE = re.compile(r"@[0-9]+x\b|\b[0-9]{2,}x[0-9]{2,}\b", re.IGNORECASE)
_HASHY_RE = re.compile(r"^[0-9a-f]{16,}$|^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
# Words that carry no meaning on their own — a name made only of these is a
# camera/CMS default, not something the site chose to say.
_GENERIC_NAME_WORDS = frozenset({
    "img", "image", "images", "photo", "photos", "pic", "pics", "picture",
    "dsc", "dscn", "screenshot", "untitled", "file", "upload", "uploads",
    "download", "thumb", "thumbnail", "banner", "bg", "background", "icon",
    "default", "placeholder", "asset", "assets", "media", "item", "slide",
    "cropped", "resized", "scaled", "final", "copy", "new", "temp", "tmp",
    "test", "small", "large", "medium", "big", "min", "max", "full",
})


def _split_filename_words(stem: str) -> list[str]:
    """Filename stem → its words, separators and packaging noise removed."""
    cleaned = _FILENAME_NOISE_RE.sub(" ", stem)
    return [w for w in _FILENAME_WORD_SEP_RE.split(cleaned) if w]


def descriptive_name_from_url(url: str) -> str:
    """A human-readable name from a DESCRIPTIVE filename, else "".

    On a badge/logo wall the filename is routinely the only place the source
    states what the image *is* — the tiles carry no text and ``alt`` is empty,
    so ``img/awards/Asia Pacific Top Excellence Brand 2008.jpg`` is the site's
    own wording for that award and the only honest caption available.

    Deliberately conservative: this is the last resort in the caption ranking,
    and a wrong caption on an award is worse than none. A name has to be at
    least two words and say something a camera or CMS wouldn't have written on
    its own, so ``MFA2.png``, ``KPM.png`` and ``IMG_4821.jpg`` all yield "" and
    the tile stays uncaptioned rather than guessing.
    """
    try:
        path = urlparse(url).path
    except ValueError:
        return ""
    basename = unquote(path.rsplit("/", 1)[-1])
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    if not stem or _HASHY_RE.match(stem.lower()):
        return ""

    words = [w for w in _split_filename_words(stem) if len(w) > 1]
    # Case-insensitive dedupe, first spelling wins: "Excellence -excellence"
    # is one word said twice by a careless export, not emphasis.
    seen: set[str] = set()
    unique: list[str] = []
    for word in words:
        key = word.lower()
        if key not in seen:
            seen.add(key)
            unique.append(word)

    if len(unique) < 2:
        return ""
    if all(w.lower() in _GENERIC_NAME_WORDS or w.isdigit() for w in unique):
        return ""
    if not any(sum(c.isalpha() for c in w) >= 3 for w in unique):
        return ""
    return " ".join(unique)


# Directories that mark a BADGE gallery rather than the site's own brand mark.
# A file under /awards/ is an award someone gave this business; the site's logo
# does not live there.
_BADGE_DIR_HINTS = (
    "award",
    "certificate",
    "certification",
    "accreditation",
    "accredited",
    "recognition",
    "partner",
    "sponsor",
    "client",
    "affiliate",
)

# Above this many words a basename is a DESCRIPTION that happens to contain the
# word "logo", not a logo filename. See looks_like_logo_url.
_MAX_LOGO_NAME_WORDS = 3


def looks_like_logo_url(url: str) -> bool:
    """True when the file NAME says logo (assets/logo.png, site-logo.svg).

    Filename only — the query string could be anything — with two guards that
    keep a *partner/award badge* from being mistaken for the site's own mark:

    * A badge-gallery directory (/awards/, /certificates/, /partners/…)
      disqualifies outright. Those files belong to somebody else's brand.
    * The basename has to read as a logo filename *as a whole*. A substring
      test alone ranked ``img/awards/Logo of the 21st century the prestigious
      brand.png`` as a real brand mark, so an award medal became the site's
      header logo (CLAUDE.md contract #3). Past a few words a basename is a
      description that merely contains the word, not a name for the logo file.
    """
    path = urlparse(url).path.lower()
    directory = path.rsplit("/", 1)[0]
    if any(hint in directory for hint in _BADGE_DIR_HINTS):
        return False
    basename = path.rsplit("/", 1)[-1]
    if "logo" not in basename:
        return False
    stem = basename.rsplit(".", 1)[0]
    return len(_split_filename_words(stem)) <= _MAX_LOGO_NAME_WORDS


def iter_srcset_candidates(srcset: str):
    """Yield (url, descriptor) pairs from a srcset string.

    A srcset URL may itself contain commas — Wix bakes its transform into the
    path (``…/v1/fill/w_461,h_161,al_c,q_85,…/file.png``) and data URIs are
    comma-heavy — so a naive ``split(",")`` shatters them and yields a garbage
    trailing fragment. Per the HTML grammar, a candidate URL is a run of
    non-whitespace and the (optional) descriptor follows after whitespace, with
    candidates separated by commas; tokenize accordingly.
    """
    i, n = 0, len(srcset)
    while i < n:
        # Skip separators (whitespace and the commas between candidates).
        while i < n and (srcset[i].isspace() or srcset[i] == ","):
            i += 1
        if i >= n:
            break
        # URL: everything up to the next whitespace (internal commas kept).
        start = i
        while i < n and not srcset[i].isspace():
            i += 1
        url = srcset[start:i]
        descriptor = ""
        if url.endswith(","):
            # No descriptor — the comma directly separates candidates.
            url = url.rstrip(",")
        else:
            while i < n and srcset[i].isspace():
                i += 1
            dstart = i
            while i < n and srcset[i] != ",":
                i += 1
            descriptor = srcset[dstart:i].strip()
            if i < n and srcset[i] == ",":
                i += 1
        if url:
            yield url, descriptor


def best_srcset_candidate(srcset: str | None) -> tuple[str | None, float]:
    """Return the highest-density/width URL from a srcset string and its score.

    The score is the winning ``w``/``x`` descriptor (``x`` scaled by 1000 so any
    density beats any raw width). A score of ``1.0`` means the winning candidate
    carried no real descriptor, so callers can treat it as "no measured size
    advantage" and keep a good base ``src``.
    """
    if not srcset:
        return None, -1.0
    best_url: str | None = None
    best_score = -1.0
    for url, descriptor in iter_srcset_candidates(srcset):
        descriptor = descriptor.lower()
        score = 1.0
        try:
            if descriptor.endswith("w"):
                score = float(descriptor[:-1])
            elif descriptor.endswith("x"):
                score = float(descriptor[:-1]) * 1000.0
        except ValueError:
            score = 1.0
        if score > best_score:
            best_score = score
            best_url = url
    return best_url, best_score


def image_src_from_tag(img: Tag) -> str | None:
    """Prefer real responsive image URLs over placeholders."""
    src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
    if isinstance(src, list):
        src = src[0] if src else None
    src = src if isinstance(src, str) else None

    srcset = (
        img.get("srcset")
        or img.get("data-srcset")
        or img.get("data-lazy-srcset")
    )
    if isinstance(srcset, list):
        srcset = srcset[0] if srcset else None
    srcset = srcset if isinstance(srcset, str) else None
    srcset_candidate, srcset_score = best_srcset_candidate(srcset)

    source = img.find_previous_sibling("source")
    if source is None and isinstance(img.parent, Tag) and img.parent.name == "picture":
        sources = [s for s in img.parent.find_all("source") if isinstance(s, Tag)]
        source = sources[-1] if sources else None
    if isinstance(source, Tag):
        source_srcset = source.get("srcset") or source.get("data-srcset")
        if isinstance(source_srcset, list):
            source_srcset = source_srcset[0] if source_srcset else None
        picture_candidate, picture_score = best_srcset_candidate(
            source_srcset if isinstance(source_srcset, str) else None
        )
        if picture_candidate:
            srcset_candidate, srcset_score = picture_candidate, picture_score

    src_low = (src or "").lower().split("?", 1)[0]
    if srcset_candidate and (
        not src
        or looks_like_icon(src, "")
        or src_low.endswith(".svg")
        or "placeholder" in src_low
    ):
        return srcset_candidate
    # A responsive <img> usually keeps a small/medium fallback in `src` while the
    # full-resolution variants live only in `srcset`. Prefer the largest srcset
    # candidate so figure images are captured at full size — but only when it
    # carries a real width/density descriptor (score > 1); a descriptor-less
    # 1-URL srcset is no better than `src`, so keep the base then.
    if srcset_candidate and srcset_score > 1.0:
        return srcset_candidate
    return src or srcset_candidate


def tag_classes(tag: Tag) -> str:
    return " ".join(
        tag.get("class") if isinstance(tag.get("class"), list) else []
    ).lower()
