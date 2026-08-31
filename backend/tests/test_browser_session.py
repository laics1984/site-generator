"""The saved browser session: what counts as one, and when it stops counting.

`conftest._offline_facebook` already points `browser_session_dir` at a scratch
directory for every test, so nothing here can see or overwrite a real signed-in
session on the developer's machine.
"""

import json
import logging
import os
import stat
import time
import unittest

from app.config import settings
from app.services import browser_session
from app.services.browser_session import SessionRejected

_YEAR = 365 * 86400


def _cookie(name: str, *, expires: float | int = -1) -> dict:
    return {
        "name": name,
        "value": f"{name}-value",
        "domain": ".facebook.com",
        "path": "/",
        "expires": expires,
        "httpOnly": True,
        "secure": True,
        "sameSite": "None",
    }


def _signed_in(*, expires: float | int | None = None) -> dict:
    """A storage state shaped like the one Playwright hands back after a login."""
    when = time.time() + _YEAR if expires is None else expires
    return {
        "cookies": [
            _cookie("datr", expires=when),
            _cookie("c_user", expires=when),
            _cookie("xs", expires=when),
        ],
        "origins": [],
    }


class SaveLoadTest(unittest.TestCase):
    def test_round_trip(self):
        state = _signed_in()
        status = browser_session.save("facebook", state, label="Acme Coffee")
        self.assertTrue(status.connected)
        self.assertEqual(status.label, "Acme Coffee")
        self.assertEqual(browser_session.load("facebook"), state)

    def test_status_carries_no_cookies(self):
        """The UI renders this; it must not be able to leak the session."""
        browser_session.save("facebook", _signed_in())
        rendered = json.dumps(browser_session.status("facebook").as_dict())
        self.assertNotIn("xs-value", rendered)
        self.assertNotIn("c_user", rendered)

    def test_nothing_saved_reads_as_disconnected(self):
        self.assertFalse(browser_session.status("facebook").connected)
        self.assertIsNone(browser_session.load("facebook"))

    def test_clear_is_idempotent(self):
        browser_session.save("facebook", _signed_in())
        browser_session.clear("facebook")
        browser_session.clear("facebook")  # must not raise
        self.assertIsNone(browser_session.load("facebook"))

    def test_the_file_is_not_world_readable(self):
        browser_session.save("facebook", _signed_in())
        path = os.path.join(settings.browser_session_dir, "facebook.json")
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode & (stat.S_IRWXG | stat.S_IRWXO), 0, oct(mode))

    def test_an_unknown_session_name_is_refused(self):
        with self.assertRaises(SessionRejected):
            browser_session.save("myspace", _signed_in())


class NotASessionTest(unittest.TestCase):
    """A well-formed file that authenticates nothing is the dangerous case: it
    looks exactly like success and fails much later, in a render."""

    def test_a_state_without_the_session_cookies_is_refused(self):
        anonymous = {"cookies": [_cookie("datr", expires=time.time() + _YEAR)], "origins": []}
        with self.assertRaises(SessionRejected) as ctx:
            browser_session.save("facebook", anonymous)
        self.assertIn("c_user", str(ctx.exception))
        self.assertIn("xs", str(ctx.exception))

    def test_half_a_session_is_refused(self):
        """`c_user` is only an account id — on its own it authenticates nothing."""
        half = {"cookies": [_cookie("c_user", expires=time.time() + _YEAR)], "origins": []}
        with self.assertRaises(SessionRejected):
            browser_session.save("facebook", half)

    def test_a_non_storage_state_is_refused(self):
        for payload in ({}, {"cookies": "nope"}, {"origins": []}):
            with self.subTest(payload=payload):
                with self.assertRaises(SessionRejected):
                    browser_session.save("facebook", payload)

    def test_a_rejected_save_leaves_nothing_behind(self):
        with self.assertRaises(SessionRejected):
            browser_session.save("facebook", {"cookies": []})
        self.assertEqual(os.listdir(settings.browser_session_dir), [])


class ExpiryTest(unittest.TestCase):
    """Read from the cookies, never invented — so a dead session degrades to an
    anonymous render instead of failing somewhere nobody can see."""

    def test_expiry_is_the_earliest_of_the_session_cookies(self):
        soon = time.time() + 86400
        later = time.time() + _YEAR
        state = {
            "cookies": [
                _cookie("c_user", expires=later),
                _cookie("xs", expires=soon),
                # A short-lived cookie that does NOT carry the sign-in must not
                # drag the answer down with it.
                _cookie("wd", expires=time.time() + 60),
            ],
            "origins": [],
        }
        status = browser_session.save("facebook", state)
        self.assertAlmostEqual(status.expires_at, soon, places=3)

    def test_a_session_scoped_cookie_states_no_expiry(self):
        """Playwright writes -1 for a session cookie: no stated expiry, not an
        immediate one."""
        status = browser_session.save("facebook", _signed_in(expires=-1))
        self.assertIsNone(status.expires_at)
        self.assertIsNone(status.expires_in_days)
        self.assertIsNotNone(browser_session.load("facebook"))

    def test_saving_an_already_expired_session_is_refused(self):
        with self.assertRaises(SessionRejected):
            browser_session.save("facebook", _signed_in(expires=time.time() - 60))

    def test_load_refuses_a_session_that_expired_after_it_was_saved(self):
        browser_session.save("facebook", _signed_in(expires=time.time() + 2))
        self.assertIsNotNone(browser_session.load("facebook"))
        # Rewrite the record's expiry rather than sleeping.
        path = os.path.join(settings.browser_session_dir, "facebook.json")
        with open(path, encoding="utf-8") as stream:
            record = json.load(stream)
        record["expires_at"] = time.time() - 1
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(record, stream)

        self.assertIsNone(browser_session.load("facebook"))
        self.assertFalse(browser_session.status("facebook").connected)

    def test_expires_in_days_never_goes_negative(self):
        browser_session.save("facebook", _signed_in())
        self.assertGreaterEqual(browser_session.status("facebook").expires_in_days, 364)


class CorruptFileTest(unittest.TestCase):
    """A session that can't be read is a missing session, never a crash — the
    render it feeds still works without one."""

    def _write(self, text: str) -> None:
        os.makedirs(settings.browser_session_dir, exist_ok=True)
        path = os.path.join(settings.browser_session_dir, "facebook.json")
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(text)

    def test_unparseable_json_reads_as_disconnected(self):
        self._write("{not json")
        with self.assertLogs("app.services.browser_session", level=logging.WARNING):
            self.assertFalse(browser_session.status("facebook").connected)
        self.assertIsNone(browser_session.load("facebook"))

    def test_json_of_the_wrong_shape_reads_as_disconnected(self):
        self._write('{"saved_at": 1, "storage_state": "not-an-object"}')
        self.assertFalse(browser_session.status("facebook").connected)
        self.assertIsNone(browser_session.load("facebook"))


class NeverLoggedTest(unittest.TestCase):
    def test_saving_logs_the_fact_but_not_the_cookies(self):
        with self.assertLogs("app.services.browser_session", level=logging.INFO) as caught:
            browser_session.save("facebook", _signed_in(), label="Acme")
        logged = "\n".join(caught.output)
        self.assertIn("saved", logged)
        self.assertNotIn("xs-value", logged)
        self.assertNotIn("c_user-value", logged)
