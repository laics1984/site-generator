"""SSRF guard for the scrape/fetch layer.

The generator fetches arbitrary URLs supplied by the user (the scrape endpoints)
or discovered in scraped pages (crawl links, image srcs). Without a guard a caller
could point the backend at internal services it can otherwise reach — the CMS at
``host.docker.internal``, cloud metadata at ``169.254.169.254``, or RFC1918 hosts
on the Docker/host network.

``assert_public_url`` resolves a URL's host to its IP(s) and refuses any that are
loopback / private / link-local / reserved / multicast. It runs at every fetch
boundary (see callers in ``fast_fetch``, ``scraper``, ``sitemap``, ``image_vision``,
and the ``/api/scrape`` router).

Opt out for local development via ``settings.scrape_allow_private_hosts`` (lets you
scrape ``http://localhost`` targets on your own machine). Fixed, known hosts
(Pexels, the configured CMS) don't route through this guard.

Note on redirects: callers that follow redirects validate the *initial* URL here;
the fetch choke points (``_goto_and_render``, ``try_fast_fetch``) also re-run the
guard, and each page render is independently guarded, so a redirect that lands on
an internal host is refused when that host is next fetched. A blind SSRF via a
single mid-chain redirect that never returns is the documented residual risk.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from app.config import settings

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Hostnames we refuse regardless of what they resolve to — internal aliases that
# must never be reachable through user-supplied URLs.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "host.docker.internal",
        "gateway.docker.internal",
        "metadata.google.internal",
    }
)


class UnsafeUrlError(ValueError):
    """A URL is malformed, uses a disallowed scheme, or resolves to a non-public
    address. Callers at the HTTP boundary map this to a 400."""


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def assert_public_url(url: str, *, allow_private: bool | None = None) -> str:
    """Validate that ``url`` is safe to fetch. Returns it unchanged, or raises
    :class:`UnsafeUrlError`.

    ``allow_private`` overrides ``settings.scrape_allow_private_hosts`` (mainly a
    test hook). DNS resolution runs on the event loop's resolver so it never
    blocks other coroutines.
    """
    if allow_private is None:
        allow_private = settings.scrape_allow_private_hosts

    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"URL scheme must be http or https: {url!r}")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError(f"URL has no host: {url!r}")

    if allow_private:
        return url

    if host.lower() in _BLOCKED_HOSTNAMES:
        raise UnsafeUrlError(f"Refusing to fetch internal host {host!r}")

    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Cannot resolve host {host!r}: {exc}") from exc

    if not infos:
        raise UnsafeUrlError(f"Host {host!r} did not resolve to any address")

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _ip_is_public(ip):
            raise UnsafeUrlError(
                f"Refusing to fetch {host!r}: resolves to non-public address {ip}"
            )
    return url


async def is_public_url(url: str, *, allow_private: bool | None = None) -> bool:
    """Boolean convenience wrapper over :func:`assert_public_url`."""
    try:
        await assert_public_url(url, allow_private=allow_private)
        return True
    except UnsafeUrlError:
        return False
