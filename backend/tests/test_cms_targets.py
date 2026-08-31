"""
Which CMS a push lands in.

Two properties carry the feature and are pinned here:

- **The default target is today's behaviour.** A request that names no target
  must reach exactly the CMS the generator used before targets existed, and an
  install that never configures a second one must see no second target at all.
- **Label and remoteness are derived.** They are read off the configured URL
  rather than stored, so they cannot drift from where the bytes actually go.

The host check in `push_orchestrator` is covered in `test_push_orchestrator`
alongside its siblings, including that it follows the target and not settings.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.cms_client import CmsApiError, CmsClient
from app.services.push_orchestrator import PushReport
from app.services.cms_targets import (
    UnknownCmsTarget,
    available_targets,
    default_target,
    resolve_target,
)


# The push endpoint validates the whole GeneratedSite before it reaches a
# target, so the tests below need a body that parses — not one that means
# anything. Nothing here is ever pushed: push_site is stubbed.
_MINIMAL_SITE = {
    "site_name": "Acme",
    "pages": [
        {
            "slug": "",
            "title": "Home",
            "is_homepage": True,
            "body_schema": {"elements": []},
            "seo": {"title": "Home"},
        }
    ],
}


class _TargetSettings(unittest.TestCase):
    """Mutates settings in place — the suite's idiom (see conftest)."""

    def setUp(self) -> None:
        self._saved = (
            settings.cms_api_base_url,
            settings.admin_app_base_url,
            settings.cms_remote_api_base_url,
            settings.cms_remote_admin_base_url,
        )
        settings.cms_api_base_url = "http://localhost:8000"
        settings.admin_app_base_url = None
        settings.cms_remote_api_base_url = None
        settings.cms_remote_admin_base_url = None

    def tearDown(self) -> None:
        (
            settings.cms_api_base_url,
            settings.admin_app_base_url,
            settings.cms_remote_api_base_url,
            settings.cms_remote_admin_base_url,
        ) = self._saved


class DefaultTargetTest(_TargetSettings):
    def test_derived_from_the_existing_cms_settings(self) -> None:
        settings.admin_app_base_url = "http://localhost:5000"
        target = default_target()
        self.assertEqual(target.name, "default")
        self.assertEqual(target.api_base_url, "http://localhost:8000")
        self.assertEqual(target.admin_base_url, "http://localhost:5000")

    def test_is_the_only_target_until_a_remote_is_configured(self) -> None:
        self.assertEqual([t.name for t in available_targets()], ["default"])

    def test_unnamed_request_gets_it(self) -> None:
        for name in (None, "", "   "):
            with self.subTest(name=name):
                self.assertEqual(resolve_target(name).name, "default")

    def test_trailing_slash_is_normalised(self) -> None:
        settings.cms_api_base_url = "http://localhost:8000/"
        self.assertEqual(default_target().api_base_url, "http://localhost:8000")


class RemoteTargetTest(_TargetSettings):
    def test_appears_once_configured_and_sorts_after_the_default(self) -> None:
        settings.cms_remote_api_base_url = "https://app-api.example.com"
        self.assertEqual([t.name for t in available_targets()], ["default", "remote"])
        self.assertEqual(resolve_target("remote").api_base_url, "https://app-api.example.com")

    def test_empty_string_reads_as_unset(self) -> None:
        """`CMS_REMOTE_API_BASE_URL=` in .env means off, not a broken target with
        an empty base URL — the job of config's _empty_str_is_none validator."""
        settings.cms_remote_api_base_url = ""
        self.assertEqual([t.name for t in available_targets()], ["default"])

    def test_admin_url_is_optional(self) -> None:
        settings.cms_remote_api_base_url = "https://app-api.example.com"
        self.assertIsNone(resolve_target("remote").admin_base_url)

    def test_unknown_name_is_refused_and_names_what_exists(self) -> None:
        with self.assertRaises(UnknownCmsTarget) as ctx:
            resolve_target("staging")
        self.assertIn("staging", str(ctx.exception))
        self.assertIn("default", str(ctx.exception))


class DerivationTest(_TargetSettings):
    def test_label_is_host_and_port(self) -> None:
        """Two local CMSes differ only by port, so the port has to survive."""
        self.assertEqual(default_target().label, "localhost:8000")
        settings.cms_api_base_url = "https://app-api.example.com"
        self.assertEqual(default_target().label, "app-api.example.com")

    def test_is_remote_is_false_for_every_this_machine_host(self) -> None:
        for host in (
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://host.docker.internal",
            "http://gateway.docker.internal:8000",
        ):
            with self.subTest(host=host):
                settings.cms_api_base_url = host
                self.assertFalse(default_target().is_remote)

    def test_is_remote_is_true_once_the_bytes_leave(self) -> None:
        settings.cms_api_base_url = "https://app-api.example.com"
        self.assertTrue(default_target().is_remote)

    def test_api_host_drops_the_port(self) -> None:
        self.assertEqual(default_target().api_host, "localhost")


class ClientFactoryTest(_TargetSettings):
    def test_for_target_sets_the_base_url(self) -> None:
        settings.cms_remote_api_base_url = "https://app-api.example.com/"
        client = CmsClient.for_target(resolve_target("remote"))
        self.assertEqual(client.base_url, "https://app-api.example.com")

    def test_for_default_still_means_the_default_target(self) -> None:
        self.assertEqual(CmsClient.for_default().base_url, "http://localhost:8000")


class RedirectGuardTest(unittest.IsolatedAsyncioTestCase):
    """The misconfiguration a remote target invites: an http:// base URL for an
    https-only CMS. Redirects are not followed on CMS calls (httpx turns a 301
    on a POST into a GET, which would half-apply a push), so login says so
    plainly instead of letting a bare 3xx become the generator's own status."""

    async def test_a_redirected_login_is_a_descriptive_error(self) -> None:
        client = CmsClient(base_url="http://app-api.example.com")
        redirect = httpx.Response(
            301,
            headers={"location": "https://app-api.example.com/api/auth/login"},
            request=httpx.Request("POST", "http://app-api.example.com/api/auth/login"),
        )
        with patch.object(
            httpx.AsyncClient, "post", new=AsyncMock(return_value=redirect)
        ):
            with self.assertRaises(CmsApiError) as ctx:
                await client.login("a@b.c", "x")
        await client.aclose()
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("redirected", str(ctx.exception))
        self.assertIn("https://app-api.example.com", str(ctx.exception))
        self.assertIsNone(client.jwt)


class TargetsEndpointTest(_TargetSettings):
    """The frontend reads no import.meta.env, so this endpoint is the only way
    it can learn which CMSes exist and what they are called."""

    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(app)

    def test_lists_the_default_target_alone_by_default(self) -> None:
        body = self.client.get("/api/cms/targets").json()
        self.assertEqual(
            body,
            [
                {
                    "name": "default",
                    "label": "localhost:8000",
                    "api_base_url": "http://localhost:8000",
                    "is_remote": False,
                }
            ],
        )

    def test_lists_the_remote_second_when_configured(self) -> None:
        settings.cms_remote_api_base_url = "https://app-api.example.com"
        body = self.client.get("/api/cms/targets").json()
        self.assertEqual([t["name"] for t in body], ["default", "remote"])
        self.assertTrue(body[1]["is_remote"])

    def test_unknown_target_is_a_400_not_a_500(self) -> None:
        for path, payload in (
            (
                "/api/cms/test-connection",
                {"email": "a@b.c", "password": "x", "target": "staging"},
            ),
            (
                "/api/cms/push",
                {"site": _MINIMAL_SITE, "email": "a@b.c", "password": "x", "target": "staging"},
            ),
        ):
            with self.subTest(path=path):
                resp = self.client.post(path, json=payload)
                self.assertEqual(resp.status_code, 400)
                self.assertIn("staging", resp.json()["detail"])

    def test_test_connection_uses_the_named_target(self) -> None:
        settings.cms_remote_api_base_url = "https://app-api.example.com"
        seen: list[str] = []

        async def _login(self_, email, password):  # noqa: ANN001
            seen.append(self_.base_url)
            return "jwt"

        with patch.object(CmsClient, "login", new=_login):
            resp = self.client.post(
                "/api/cms/test-connection",
                json={"email": "a@b.c", "password": "x", "target": "remote"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(seen, ["https://app-api.example.com"])


class AdminLinkTest(_TargetSettings):
    """A successful remote push must not be followed by a localhost deep link."""

    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(app)
        settings.admin_app_base_url = "http://localhost:5000"
        settings.cms_remote_api_base_url = "https://app-api.example.com"
        settings.cms_remote_admin_base_url = "https://app.example.com"

    def _push(self, target: str) -> dict:
        report = PushReport(success=True)
        with patch(
            "app.routers.cms.push_site", new=AsyncMock(return_value=report)
        ):
            return self.client.post(
                "/api/cms/push",
                json={
                    "site": _MINIMAL_SITE,
                    "email": "a@b.c",
                    "password": "x",
                    "target": target,
                },
            ).json()

    def test_admin_link_follows_the_target(self) -> None:
        self.assertEqual(
            self._push("default")["admin_url"], "http://localhost:5000/webpages/list"
        )
        self.assertEqual(
            self._push("remote")["admin_url"], "https://app.example.com/webpages/list"
        )

    def test_no_link_when_that_target_has_no_admin_configured(self) -> None:
        settings.cms_remote_admin_base_url = None
        self.assertIsNone(self._push("remote")["admin_url"])


if __name__ == "__main__":
    unittest.main()
