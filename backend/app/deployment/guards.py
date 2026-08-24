"""
Startup checks that refuse a configuration only safe on a laptop.

This app has **no authentication, by design** — SECURITY.md states the trust
model plainly ("single-user, local-first developer tool… Do not expose port 8001
to the public internet"). That is documented in prose, and prose does not stop a
deploy. These checks do.

Every one of them is inert while `DEPLOYMENT=local`, which is the default, so
this file changes nothing about how the tool runs today. It exists so that the
day someone hosts this, the assumptions it was built on fail loudly at startup
instead of silently holding.

Two severities, deliberately not one:

- **Errors refuse to start.** Reserved for the settings that expose you: no
  authentication, the SSRF guard switched off, a CORS list still pointed at
  localhost. There is no override flag — an escape hatch for "serve this
  unauthenticated on the internet" is the exact footgun the file is here to
  prevent.
- **Warnings log and continue.** Operational facts that make a deploy worse but
  not unsafe — single-process caches, SQLite, an LLM URL still pointing at the
  Docker host. These are things you can knowingly ship with, so blocking on them
  would only teach people to route around the guard.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from app.config import settings
from app.deployment.process_local import blocking_at_scale
from app.deployment.profile import DeploymentProfile, current_profile

logger = logging.getLogger(__name__)

# Hosts that mean "this machine". Deliberately NOT shared with the similar set
# in services/cms_targets.py: that one answers "is the CMS I am pushing to on
# this machine", a question about a push destination. Sharing them would tie the
# deployment layer to a push feature, and nothing under app/deployment/ should
# be imported by the pipeline or vice versa.
_LOOPBACK_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal", "gateway.docker.internal"}
)


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def check(profile: DeploymentProfile | None = None) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for this configuration. Pure — no logging, no
    raising — so the whole matrix is testable without starting an app."""
    profile = profile or current_profile()
    if not profile.is_hosted:
        return [], []

    errors: list[str] = []
    warnings: list[str] = []

    if profile.auth == "none":
        errors.append(
            "DEPLOYMENT=hosted with DEPLOYMENT_AUTH=none: this app authenticates "
            "nothing — every endpoint is open, and /api/cms/push accepts CMS "
            "credentials in the request body. Put an authenticating reverse proxy "
            "in front and set DEPLOYMENT_AUTH=proxy to attest to it, or add "
            "authentication to the routers. See SECURITY.md."
        )

    if settings.scrape_allow_private_hosts:
        errors.append(
            "DEPLOYMENT=hosted with SCRAPE_ALLOW_PRIVATE_HOSTS=true: the SSRF "
            "guard is disabled, so a caller can drive this backend into your "
            "private network and cloud metadata. That flag is for scraping a "
            "LAN target from your own machine only."
        )

    local_origins = [o for o in settings.cors_origins if _host_of(o) in _LOOPBACK_HOSTS]
    if not settings.cors_origins:
        errors.append(
            "DEPLOYMENT=hosted with an empty CORS_ORIGINS: no browser can reach "
            "this. Set the real frontend origin(s)."
        )
    elif "*" in settings.cors_origins:
        errors.append(
            "DEPLOYMENT=hosted with CORS_ORIGINS containing '*': the API sends "
            "allow_credentials=True, so a wildcard origin is never correct."
        )
    elif local_origins:
        errors.append(
            f"DEPLOYMENT=hosted but CORS_ORIGINS still names {', '.join(local_origins)} "
            "— a leftover from local development. Set the deployed frontend's origin."
        )

    at_scale = blocking_at_scale()
    if at_scale:
        warnings.append(
            "Run ONE worker, or fix these first: "
            + "; ".join(f"{i.module.rsplit('.', 1)[-1]}.{i.attribute} ({i.at_scale.split('.')[0]})" for i in at_scale)
            + ". Full inventory in app/deployment/process_local.py."
        )

    warnings.append(
        f"State lives in SQLite at {settings.sitegen_db_path} — single-process and "
        "local to this container. Put it on persistent storage, or accept that "
        "in-flight crawl jobs do not survive a restart."
    )

    for label, url in (("LLM_BASE_URL", settings.llm_base_url), ("CMS_API_BASE_URL", settings.cms_api_base_url)):
        if _host_of(url) in _LOOPBACK_HOSTS:
            warnings.append(
                f"{label}={url} points at this machine. Correct for a laptop; "
                "almost certainly wrong for a hosted deploy."
            )

    return errors, warnings


class DeploymentUnsafe(RuntimeError):
    """Raised at startup when a hosted deploy is configured unsafely."""


def enforce() -> None:
    """Log the warnings, refuse to start on the errors. Called from the app
    lifespan, before anything binds or serves."""
    profile = current_profile()
    errors, warnings = check(profile)
    if profile.is_hosted:
        logger.info("Deployment profile: hosted (auth=%s)", profile.auth)
    for warning in warnings:
        logger.warning("Deployment: %s", warning)
    if errors:
        raise DeploymentUnsafe(
            "Refusing to start — DEPLOYMENT=hosted with settings that are only "
            "safe locally:\n  - " + "\n  - ".join(errors)
        )
