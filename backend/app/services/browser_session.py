"""A saved browser session — one Playwright ``storage_state``, keyed by name.

Some sources only show what they show to a signed-in visitor. Facebook is the
one that forced this: logged out, ``www.facebook.com/<page>/about`` serves the
og: tags and the tab chrome and nothing else; the About panel — address, hours,
phone, category, the facts a site is actually built from — renders only for a
session.

The division of responsibility is the point of the module:

* **here** — hold a session, say whether it is still good, hand it over.
* **whoever captured it** — not our business. Today that is
  ``scripts/facebook_login.py``, which opens a real window on the operator's own
  machine because the backend usually runs in a container and cannot. Tomorrow
  it could be anything; nothing downstream would change.

Two rules keep a stale session from becoming a mystery:

1. **Expiry is read from the cookies, never invented.** A session's own cookies
   state when they die, so `load` can refuse a dead one and the read degrades to
   an anonymous render — which works, it just sees less. Guessing a TTL here
   would mean either discarding good sessions or replaying dead ones into a
   render that then fails for a reason nobody can see.
2. **A session is the cookies that carry it.** A `storage_state` captured before
   the user actually signed in is a well-formed JSON file that authenticates
   nothing, and saving it would look exactly like success. `save` requires the
   named cookies to be present, so the failure lands at the moment of capture
   where it can be explained.

The contents are secrets — full account access for whoever holds them. They are
never logged, the file is 0600, and `settings.browser_session_dir` puts it on the
data volume rather than in the repo tree.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class SessionRejected(ValueError):
    """A payload that isn't a usable signed-in session."""


# name → the cookies that actually carry the sign-in. Presence of all of them is
# what "connected" means, and the earliest of their expiries is when it stops
# being true. Facebook's pair: `c_user` is the account id, `xs` the session
# secret; either one alone authenticates nothing.
_SESSION_COOKIES: dict[str, tuple[str, ...]] = {
    "facebook": ("c_user", "xs"),
}

SESSION_NAMES = tuple(_SESSION_COOKIES)


@dataclass(frozen=True, slots=True)
class SessionStatus:
    """What the UI needs to say about a session, carrying none of its secrets."""

    name: str
    connected: bool
    saved_at: float | None = None
    expires_at: float | None = None
    label: str | None = None

    @property
    def expires_in_days(self) -> int | None:
        if self.expires_at is None:
            return None
        return max(0, int((self.expires_at - time.time()) // 86400))

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "connected": self.connected,
            "saved_at": self.saved_at,
            "expires_at": self.expires_at,
            "expires_in_days": self.expires_in_days,
            "label": self.label,
        }


def _disconnected(name: str) -> SessionStatus:
    return SessionStatus(name=name, connected=False)


def _path(name: str) -> Path:
    if name not in _SESSION_COOKIES:
        raise SessionRejected(f"Unknown browser session '{name}'.")
    return Path(settings.browser_session_dir) / f"{name}.json"


def _cookies_of(storage_state: dict) -> dict[str, dict]:
    """The session's cookies by name. Later duplicates win, as a browser's do."""
    found: dict[str, dict] = {}
    for cookie in storage_state.get("cookies") or []:
        if isinstance(cookie, dict) and isinstance(cookie.get("name"), str):
            found[cookie["name"]] = cookie
    return found


def _expiry_of(name: str, storage_state: dict) -> float | None:
    """When the sign-in stops working: the earliest expiry among its cookies.

    Playwright writes ``-1`` for a session cookie — no stated expiry rather than
    an immediate one — so those don't constrain the answer. All of them being
    session-scoped yields None, which `load` reads as "no reason to refuse it".
    """
    cookies = _cookies_of(storage_state)
    stated = [
        float(cookies[wanted]["expires"])
        for wanted in _SESSION_COOKIES[name]
        if wanted in cookies
        and isinstance(cookies[wanted].get("expires"), (int, float))
        and float(cookies[wanted]["expires"]) > 0
    ]
    return min(stated) if stated else None


def _read(name: str) -> dict | None:
    path = _path(name)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Browser session '%s' unreadable: %s", name, exc)
        return None
    try:
        record = json.loads(raw)
    except ValueError:
        logger.warning("Browser session '%s' is not valid JSON — ignoring it.", name)
        return None
    return record if isinstance(record, dict) else None


def save(name: str, storage_state: dict, *, label: str | None = None) -> SessionStatus:
    """Persist a captured session. Raises `SessionRejected` if it isn't one."""
    # The name first: it decides which cookies "signed in" even means, so an
    # unknown one has to fail as a rejection rather than a KeyError two checks
    # further down.
    if name not in _SESSION_COOKIES:
        raise SessionRejected(f"Unknown browser session '{name}'.")

    if not isinstance(storage_state, dict) or not isinstance(
        storage_state.get("cookies"), list
    ):
        raise SessionRejected(
            "That isn't a Playwright storage state — expected an object with a "
            "'cookies' list."
        )

    required = _SESSION_COOKIES[name]
    present = _cookies_of(storage_state)
    absent = [wanted for wanted in required if wanted not in present]
    if absent:
        raise SessionRejected(
            f"Not signed in — the {name} session is missing "
            f"{', '.join(absent)}. Complete the login in the browser window "
            "before the session is captured."
        )

    expires_at = _expiry_of(name, storage_state)
    if expires_at is not None and expires_at <= time.time():
        raise SessionRejected(
            f"That {name} session has already expired. Sign in again."
        )

    record = {
        "name": name,
        "saved_at": time.time(),
        "expires_at": expires_at,
        "label": label or None,
        "storage_state": storage_state,
    }

    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so an interrupted save can't leave a half-written
    # session behind, and 0600 before it is ever linked into place.
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(record, stream)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        # Never leave a stray temp file holding cookies.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    status = SessionStatus(
        name=name,
        connected=True,
        saved_at=record["saved_at"],
        expires_at=expires_at,
        label=record["label"],
    )
    logger.info(
        "Browser session '%s' saved (expires %s)",
        name,
        "unstated" if expires_at is None else f"in {status.expires_in_days} days",
    )
    return status


def status(name: str) -> SessionStatus:
    """Whether `name` is connected, and until when. Never touches the cookies."""
    record = _read(name)
    if not record or not isinstance(record.get("storage_state"), dict):
        return _disconnected(name)

    expires_at = record.get("expires_at")
    expires_at = float(expires_at) if isinstance(expires_at, (int, float)) else None
    if expires_at is not None and expires_at <= time.time():
        return SessionStatus(name=name, connected=False, expires_at=expires_at)

    saved_at = record.get("saved_at")
    return SessionStatus(
        name=name,
        connected=True,
        saved_at=float(saved_at) if isinstance(saved_at, (int, float)) else None,
        expires_at=expires_at,
        label=record.get("label") if isinstance(record.get("label"), str) else None,
    )


def load(name: str) -> dict | None:
    """The `storage_state` to hand Playwright, or None when there isn't a live one.

    Returning None for an expired session — rather than the dead cookies — is
    what makes the degrade silent and correct: the render runs anonymously and
    still reads the Page, instead of failing in a way that looks like a bug.
    """
    if not status(name).connected:
        return None
    record = _read(name)
    if not record:
        return None
    state = record.get("storage_state")
    return state if isinstance(state, dict) else None


def clear(name: str) -> None:
    """Forget the session. Idempotent."""
    try:
        _path(name).unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("Could not remove browser session '%s': %s", name, exc)
        return
    logger.info("Browser session '%s' cleared", name)
