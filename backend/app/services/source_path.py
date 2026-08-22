"""One definition of "the slug for a scraped page's URL path".

Three places independently derived this: ``page_inference`` (which builds the
scaffold slugs), ``source_router`` (which indexes the crawled pages so a
scaffold can find its source) and ``routers.generate`` (which re-attaches
deterministic blocks to the page they came from). They have to agree exactly —
a scaffold whose slug normalizes differently from its source page simply never
matches, and the router falls back to the ENTRY page, so every subpage would be
generated from the homepage's content. That failure is silent, which is why the
rule lives in one place now.
"""

from __future__ import annotations

# Extensions that name a page rather than a file to download. Stripping them
# keeps the generated site's URLs clean (``/about-awards-and-recognition``
# rather than ``/about-awards-and-recognition-php``) and keeps the extension
# out of humanized menu labels, which read "Media Interview.php" otherwise.
#
# Document extensions are deliberately absent: a link to a .pdf is a file, and
# it is handled as a download rather than becoming a page.
_PAGE_EXTENSIONS = (
    ".php",
    ".html",
    ".htm",
    ".asp",
    ".aspx",
    ".jsp",
    ".jspx",
    ".cgi",
    ".shtml",
    ".phtml",
)


def strip_page_extension(segment: str) -> str:
    """``"about-awards.php"`` → ``"about-awards"``; other names unchanged."""
    lowered = segment.lower()
    for extension in _PAGE_EXTENSIONS:
        if lowered.endswith(extension) and len(segment) > len(extension):
            return segment[: -len(extension)]
    return segment


def normalize_source_slug(url_path: str | None) -> str:
    """A scraped ``url_path`` → the slug the generator keys pages by.

    Strips surrounding slashes, lowercases, and drops a page extension from the
    LAST segment only — a directory called ``news.php`` mid-path is somebody's
    odd routing, not an extension, and rewriting it would break the match it is
    supposed to make. The empty string means the homepage.
    """
    if not url_path or url_path == "/":
        return ""
    slug = url_path.strip("/").lower()
    if not slug:
        return ""
    head, sep, tail = slug.rpartition("/")
    return f"{head}{sep}{strip_page_extension(tail)}"


def is_record_url(source_ref: str | None) -> bool:
    """True when a URL addresses one RECORD of a page rather than the page.

    A PHP album viewer serves every album from ``gallery-photo.php?id=107``:
    same path, same slug, one page as far as the generator is concerned. When
    several crawled pages collapse onto one slug, the query-less URL is the
    page itself and the rest are its records — so the page keeps its own title
    ("Photo Gallery") instead of borrowing whichever album happened to be
    crawled first ("Kindergarten Mid Year Party And Birthday Celebration").
    """
    return "?" in (source_ref or "")
