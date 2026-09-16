"""One definition of "the same site" and "the same page".

Three places answered these questions, in two different spellings:
``nav_extraction.site_relative_href`` treated ``www.example.com`` and
``example.com`` as one site, while ``scraper._is_crawlable_link`` and
``content_collections._candidate_urls`` compared hosts exactly. So the header
menu could name a page the crawler refused to fetch: feruni.com links 22 of its
own pages — Contact Us, Retail Stores, a whole menu item — at the bare domain
while serving from ``www``, and every one was dropped as "another site".

Identity and address are separate on purpose:

* ``crawl_key`` is what two URLs share when they are the same page — it ignores
  the ``www.`` alias, a trailing slash and the fragment, so a page linked both
  ways is fetched once.
* ``rebase_to_origin`` is where the page is actually requested. It keeps the
  path exactly as linked, trailing slash included: stripping it made robots.txt
  rules like ``Disallow: /private/`` miss ``/private`` (so a disallowed page was
  crawled) and cost every WordPress page a 301 back to the slash.
"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

_WEB_SCHEMES = ("http", "https")


def site_host(netloc: str) -> str:
    """A host as a site identity: lowercased, without the ``www.`` alias."""
    return netloc.lower().removeprefix("www.")


def is_same_site(url: str, base_url: str) -> bool:
    """True when ``url`` lives on the same site as ``base_url``."""
    try:
        return site_host(urlparse(url).netloc) == site_host(urlparse(base_url).netloc)
    except ValueError:
        return False


def crawl_key(url: str) -> str | None:
    """The identity two URLs share when they address the same page.

    None for anything that isn't an http(s) URL. The query is kept — some sites
    route real pages through it (``?lang=en``, ``?page_id=12``).
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in _WEB_SCHEMES:
        return None
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{site_host(parsed.netloc)}{path}{query}"


def rebase_to_origin(url: str, origin_url: str) -> str:
    """``url`` requested on ``origin_url``'s scheme and host, fragment dropped.

    Only same-site URLs are rebased; anything else is returned without its
    fragment. Fetching an alias on the origin the site actually serves from
    saves the redirect the alias would answer with.
    """
    parsed = urlparse(url)
    if is_same_site(url, origin_url):
        origin = urlparse(origin_url)
        parsed = parsed._replace(scheme=origin.scheme, netloc=origin.netloc)
    return urlunparse(parsed._replace(fragment=""))
