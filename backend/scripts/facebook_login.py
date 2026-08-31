#!/usr/bin/env python3
"""Sign in to Facebook once, and hand the session to the backend.

    ./dev.sh fb-login            # from the repo root

Why this is a script and not an endpoint: the backend normally runs in a
container with no display, so it cannot open the window a login needs. This runs
on YOUR machine, opens a real Chromium, waits while you sign in, and POSTs the
resulting Playwright storage state to `POST /api/facebook/session`. From then on
every Facebook read reuses it — the About panel (address, hours, phone,
category) only renders for a signed-in visitor.

Three details that are load-bearing:

* **The same User-Agent the backend renders with** (`settings.http_user_agent`,
  already the single source for httpx, robots and Playwright). A session created
  under Chromium's own UA and then replayed under a different one is the pattern
  Facebook checkpoints.
* **A persistent profile directory**, outside the repo. The second run is
  usually instant: already signed in, detected, refreshed, closed — no typing.
* **We wait for the session COOKIES, not for a page to look right.** Facebook's
  markup changes weekly; `c_user` + `xs` is what "signed in" actually means, and
  it is the same pair `browser_session` validates on the way in.

Nothing here is destructive, so unlike the CMS scripts there is no `--apply`
gate: it writes one session that the app's own UI can disconnect. Use a
secondary account — automating a personal one is against Facebook's terms and
can get it limited.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Run from anywhere: the repo's backend/ has to be importable for app.config.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

from app.config import settings  # noqa: E402

# Cookies that carry the sign-in. Mirrors `browser_session._SESSION_COOKIES`,
# which validates the same pair when the POST lands — that check is the
# authority, this one just avoids sending something we already know is not a
# session.
SESSION_COOKIES = ("c_user", "xs")

DEFAULT_PROFILE_DIR = Path.home() / ".webtree" / "facebook-login-profile"
POLL_SECONDS = 1.0


def _log(message: str) -> None:
    print(f"  {message}", flush=True)


async def _signed_in_cookies(context) -> dict | None:
    """The storage state once the session cookies are present, else None."""
    cookies = {c["name"]: c for c in await context.cookies("https://www.facebook.com")}
    if not all(name in cookies for name in SESSION_COOKIES):
        return None
    return await context.storage_state()


async def _display_name(page) -> str | None:
    """A friendly label for the UI. Cosmetic — never worth failing over."""
    try:
        title = (await page.title() or "").strip()
    except Exception:
        return None
    # "Facebook" alone is the logged-out shell; anything richer names the account.
    if not title or title.lower() in {"facebook", "log in to facebook"}:
        return None
    return title.removesuffix(" | Facebook").strip() or None


async def capture(
    *, profile_dir: Path, timeout_seconds: int, keep_open: bool
) -> tuple[dict, str | None]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            user_agent=settings.http_user_agent,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.facebook.com/", wait_until="domcontentloaded")

            state = await _signed_in_cookies(context)
            if state is not None:
                _log("Already signed in from a previous run.")
            else:
                _log("Sign in to Facebook in the window that just opened.")
                _log(f"(waiting up to {timeout_seconds // 60} minutes — Ctrl-C to give up)")
                deadline = asyncio.get_event_loop().time() + timeout_seconds
                while state is None:
                    if asyncio.get_event_loop().time() > deadline:
                        raise TimeoutError(
                            "Timed out waiting for the sign-in. Nothing was saved."
                        )
                    if not context.pages:
                        raise RuntimeError(
                            "The browser window was closed before the sign-in "
                            "finished. Nothing was saved."
                        )
                    await asyncio.sleep(POLL_SECONDS)
                    state = await _signed_in_cookies(context)
                _log("Signed in.")

            label = await _display_name(page)
            if keep_open:
                _log("--keep-open: close the window yourself when you're done.")
                while context.pages:
                    await asyncio.sleep(POLL_SECONDS)
            return state, label
        finally:
            await context.close()


def send(backend: str, state: dict, label: str | None) -> dict:
    response = httpx.post(
        f"{backend.rstrip('/')}/api/facebook/session",
        json={"storage_state": state, "label": label},
        timeout=30.0,
    )
    if response.status_code >= 400:
        detail = response.text
        try:
            detail = response.json().get("detail", detail)
        except ValueError:
            pass
        raise SystemExit(f"  Backend refused the session ({response.status_code}): {detail}")
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--backend",
        default="http://localhost:8001",
        help="Where the site-generator backend is listening (default: %(default)s).",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
        help="Persistent Chromium profile, so re-runs skip the typing "
        "(default: %(default)s).",
    )
    parser.add_argument(
        "--timeout", type=int, default=600, help="Seconds to wait for the sign-in."
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Leave the window open after capturing, for debugging.",
    )
    args = parser.parse_args()

    _log(f"Opening Chromium (profile: {args.profile_dir})")
    try:
        state, label = asyncio.run(
            capture(
                profile_dir=args.profile_dir,
                timeout_seconds=args.timeout,
                keep_open=args.keep_open,
            )
        )
    except KeyboardInterrupt:
        _log("Cancelled. Nothing was saved.")
        return 130
    except (TimeoutError, RuntimeError) as exc:
        _log(str(exc))
        return 1

    status = send(args.backend, state, label)
    days = status.get("expires_in_days")
    who = f" as {status['label']}" if status.get("label") else ""
    _log(
        f"Session sent to the backend{who}"
        + (f" — good for about {days} days." if days is not None else ".")
    )
    _log("Facebook reads will now use it. Disconnect any time from the app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
