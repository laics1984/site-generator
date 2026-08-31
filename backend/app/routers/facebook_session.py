"""
The signed-in Facebook session: connect it, ask about it, drop it.

Logged out, Facebook serves a Page's og: tags and little else. Signed in, the
About panel renders — address, opening hours, phone, category — which is most of
what a site actually gets built from. So this exists to let one browser session,
captured once, ride along with every later render.

**The backend never captures the session itself, and can't.** It runs in a
container with no display; a login needs a real window on the operator's own
machine. `scripts/facebook_login.py` opens one, waits for the sign-in, and POSTs
the resulting Playwright storage state here. That split is why this is three
small endpoints over a file rather than a login flow: the interesting half
happens somewhere this process cannot reach.

A session lifecycle is **not a crawl job**, which is why it isn't in
`scrape.py`: no progress, no cancellation, no result to reuse — it is a setting
that happens to be a secret. One concern per router, as elsewhere.

The POST takes cookies on an unauthenticated endpoint, consistent with the
documented trust model (SECURITY.md: single-user, localhost; `/api/cms/push`
already accepts CMS credentials the same way). What would NOT be consistent is a
hosted deploy holding one person's Facebook cookies for every user to browse as
— `deployment/guards.py` refuses to start in that configuration.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.services import browser_session
from app.services.browser_session import SessionRejected

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/facebook/session", tags=["facebook"])

_NAME = "facebook"


class SaveSessionRequest(BaseModel):
    # The Playwright storage state verbatim — `{"cookies": [...], "origins":
    # [...]}`. Untyped beyond "an object" on purpose: it is Playwright's format,
    # not ours, and re-declaring its shape here would be a second spelling to
    # keep in sync. `browser_session.save` validates what we actually depend on.
    storage_state: dict = Field(..., description="Playwright storage_state")
    # A human label for the UI ("Signed in as …"). Cosmetic; never trusted.
    label: str | None = None


def _disabled() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=(
            "Signed-in Facebook reads are turned off "
            "(FACEBOOK_SESSION_ENABLED=false)."
        ),
    )


@router.get("")
async def get_session() -> dict:
    """Whether a session is connected, and for how much longer.

    Carries no cookies — only the facts the UI shows.
    """
    if not settings.facebook_session_enabled:
        return {**browser_session.SessionStatus(name=_NAME, connected=False).as_dict(),
                "enabled": False}
    return {**browser_session.status(_NAME).as_dict(), "enabled": True}


@router.post("")
async def save_session(payload: SaveSessionRequest) -> dict:
    """Store a session captured by `scripts/facebook_login.py`."""
    if not settings.facebook_session_enabled:
        raise _disabled()
    try:
        status = browser_session.save(
            _NAME, payload.storage_state, label=payload.label
        )
    except SessionRejected as exc:
        # 422: the payload is well-formed JSON that isn't a signed-in session —
        # almost always a window closed before the login finished.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**status.as_dict(), "enabled": True}


@router.delete("")
async def delete_session() -> dict:
    """Forget the session. Idempotent — disconnecting twice is not an error."""
    browser_session.clear(_NAME)
    return {
        **browser_session.SessionStatus(name=_NAME, connected=False).as_dict(),
        "enabled": settings.facebook_session_enabled,
    }
