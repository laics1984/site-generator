"""CmsClient keeps a push alive across the CMS's short-lived access tokens.

The CMS answers an expired JWT with 401 {"code": "TOKEN_EXPIRED"} from its
auth middleware, before any controller runs, so the client may log in again and
replay the request once without double-applying it.
"""

from __future__ import annotations

import asyncio
import unittest

import httpx

from app.services.cms_client import CmsApiError, CmsClient

EXPIRED = {"error": "Token expired", "code": "TOKEN_EXPIRED"}


def _client(handler) -> CmsClient:
    client = CmsClient(base_url="http://cms.test")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def _login_response(token: str) -> httpx.Response:
    return httpx.Response(200, json={"token": token, "user": {}, "entity": []})


class RenewExpiredJwtTest(unittest.IsolatedAsyncioTestCase):
    async def test_an_expired_token_is_replaced_and_the_request_replayed_once(self):
        logins = 0
        seen_tokens: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal logins
            if request.url.path == "/api/auth/login":
                logins += 1
                return _login_response(f"jwt-{logins}")
            seen_tokens.append(request.headers["Authorization"])
            if request.headers["Authorization"] == "Bearer jwt-1":
                return httpx.Response(401, json=EXPIRED)
            return httpx.Response(200, json={"data": {"id": "p1"}})

        client = _client(handler)
        await client.login("push@example.com", "secret")
        page = await client.get_page("tok", "p1")
        await client.aclose()

        self.assertEqual(page, {"id": "p1"})
        self.assertEqual(logins, 2)
        self.assertEqual(seen_tokens, ["Bearer jwt-1", "Bearer jwt-2"])
        self.assertEqual(client.jwt, "jwt-2")

    async def test_concurrent_expiries_share_one_login(self):
        logins = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal logins
            if request.url.path == "/api/auth/login":
                logins += 1
                await asyncio.sleep(0.01)
                return _login_response(f"jwt-{logins}")
            if request.headers["Authorization"] == "Bearer jwt-1":
                return httpx.Response(401, json=EXPIRED)
            return httpx.Response(200, json={"data": {"id": request.url.path}})

        client = _client(handler)
        await client.login("push@example.com", "secret")
        await asyncio.gather(*(client.get_page("tok", f"p{i}") for i in range(5)))
        await client.aclose()

        self.assertEqual(logins, 2)

    async def test_an_upload_is_replayed_intact(self):
        bodies: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/auth/login":
                return _login_response("jwt-new")
            bodies.append(request.read())
            if request.headers["Authorization"] == "Bearer jwt-old":
                return httpx.Response(401, json=EXPIRED)
            return httpx.Response(200, json={"t": "p", "i": "https://cdn.test/a.png"})

        client = _client(handler)
        client.jwt = "jwt-old"
        client._credentials = ("push@example.com", "secret")
        url = await client.upload_media("tok", file_bytes=b"\x89PNG-bytes", filename="a.png", content_type="image/png")
        await client.aclose()

        self.assertEqual(url, "https://cdn.test/a.png")
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0], bodies[1])
        self.assertIn(b"\x89PNG-bytes", bodies[1])

    async def test_other_401s_are_not_retried(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(401, json={"error": "Invalid token", "code": "TOKEN_INVALID"})

        client = _client(handler)
        client.jwt = "jwt"
        client._credentials = ("push@example.com", "secret")
        with self.assertRaises(CmsApiError) as raised:
            await client.get_page("tok", "p1")
        await client.aclose()

        self.assertEqual(raised.exception.status, 401)
        self.assertEqual(calls, 1)

    async def test_a_token_set_without_login_is_not_renewed(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(401, json=EXPIRED)

        client = _client(handler)
        client.jwt = "handed-in"
        with self.assertRaises(CmsApiError):
            await client.get_page("tok", "p1")
        await client.aclose()

        self.assertEqual(calls, 1)

    async def test_a_second_expiry_after_renewing_is_final(self):
        logins = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal logins
            if request.url.path == "/api/auth/login":
                logins += 1
                return _login_response(f"jwt-{logins}")
            return httpx.Response(401, json=EXPIRED)

        client = _client(handler)
        await client.login("push@example.com", "secret")
        with self.assertRaises(CmsApiError):
            await client.get_page("tok", "p1")
        await client.aclose()

        self.assertEqual(logins, 2)


if __name__ == "__main__":
    unittest.main()
