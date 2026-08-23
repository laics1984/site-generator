"""
Which CMS a push lands in.

The generator runs locally, but the CMS it writes into need not. A *target* is a
named destination chosen per push, so one local install can drop a draft into
the local CMS and a real site into production without an .env edit or a restart.

Two rules keep it honest:

- **The wire carries a NAME, never a URL.** `POST /api/cms/push` runs on an
  unauthenticated local backend (SECURITY.md) and forwards the operator's CMS
  email + password. A caller-supplied base URL would turn that endpoint into a
  credential-forwarding proxy to any host; an allowlisted name cannot. It is
  also what keeps `url_guard`'s "fixed, known hosts (Pexels, the configured CMS)
  don't route through this guard" true — targets stay config-derived.
- **Label and remoteness are DERIVED, never configured.** The host on the push
  button then reports where the bytes actually go, the way `/health/llm` reports
  the live model rather than a configured name. A label typed once into .env
  goes stale in silence: the current .env says
  `CMS_API_BASE_URL=http://host.docker.internal` with no port, so even "Local"
  would already be a half-truth.

`cms_api_base_url` / `admin_app_base_url` stay the one home for the default
target (docker-compose rewrites the former for container networking); the remote
target simply does not exist unless `cms_remote_api_base_url` is set, so an
untouched install has one target and the publish drawer is byte-identical to
before. A third target is an edit to `available_targets` and nowhere else — the
rest of the push is written against the tuple, not against "two".
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from app.config import settings

DEFAULT_TARGET_NAME = "default"
REMOTE_TARGET_NAME = "remote"

# Hosts that mean "this machine" — the Docker aliases included, since the
# backend reaches a CMS on the host through them. Mirrors the intent of
# url_guard's private-host set without importing it: that one refuses a fetch,
# this one only decides whether to warn the operator.
_LOCAL_HOSTNAMES = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "::1",
        "host.docker.internal",
        "gateway.docker.internal",
    }
)


class UnknownCmsTarget(ValueError):
    """Raised when a request names a target that isn't configured."""


@dataclass(frozen=True, slots=True)
class CmsTarget:
    """One CMS the generator can push into.

    `api_base_url` is the ADMIN API origin: the CMS's routes/api.php can serve
    admin and public routes on separate hosts (ADMIN_API_DOMAIN +
    ALLOW_LEGACY_SHARED_API_HOST) and a push only ever calls admin routes.
    """

    name: str
    api_base_url: str
    admin_base_url: str | None = None

    @property
    def api_host(self) -> str:
        """Hostname only — what an already-hosted asset URL is compared against."""
        try:
            return urlparse(self.api_base_url).hostname or ""
        except ValueError:
            return ""

    @property
    def label(self) -> str:
        """What the UI shows. host:port, so two local CMSes stay distinguishable."""
        try:
            netloc = urlparse(self.api_base_url).netloc
        except ValueError:
            netloc = ""
        return netloc or self.api_base_url

    @property
    def is_remote(self) -> bool:
        """True when the bytes leave this machine. A fact, not a judgement —
        which is why the UI warns on it rather than on a configured flag."""
        return self.api_host.lower() not in _LOCAL_HOSTNAMES


def _clean(url: str | None) -> str | None:
    url = (url or "").strip().rstrip("/")
    return url or None


def available_targets() -> tuple[CmsTarget, ...]:
    """Every configured target, default first (the UI's selection order).

    The default target is derived from cms_api_base_url / admin_app_base_url and
    cannot be redeclared by the remote settings — one URL, one home.
    """
    targets = [
        CmsTarget(
            name=DEFAULT_TARGET_NAME,
            api_base_url=_clean(settings.cms_api_base_url) or "",
            admin_base_url=_clean(settings.admin_app_base_url),
        )
    ]
    remote_api = _clean(settings.cms_remote_api_base_url)
    if remote_api:
        targets.append(
            CmsTarget(
                name=REMOTE_TARGET_NAME,
                api_base_url=remote_api,
                admin_base_url=_clean(settings.cms_remote_admin_base_url),
            )
        )
    return tuple(targets)


def default_target() -> CmsTarget:
    """The target a request that names none lands in — today's behaviour."""
    return available_targets()[0]


def resolve_target(name: str | None) -> CmsTarget:
    """Look a target up by wire name. Blank/None ⇒ the default target.

    Deliberately NOT cached: the test suite mutates `settings.<field>` in place
    (tests/conftest.py documents the pain of the @lru_cache'd Pexels client),
    and building two frozen dataclasses is free.
    """
    wanted = (name or "").strip()
    if not wanted:
        return default_target()
    for target in available_targets():
        if target.name == wanted:
            return target
    known = ", ".join(t.name for t in available_targets())
    raise UnknownCmsTarget(f"Unknown CMS target {wanted!r}. Configured targets: {known}.")
