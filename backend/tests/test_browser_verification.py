"""Offline coverage of the narrowly scoped HTTP verification fallback."""

import asyncio
import gzip
from contextlib import asynccontextmanager
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest import mock

import httpx
import pytest
from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

from app.config import settings
from app.services import browser, browser_verification as verification, fast_fetch, scraper
from app.services.fast_fetch import FastFetchResult, FastFetchSkipped, FastFetchSkipReason
from app.services.url_guard import UnsafeUrlError

FIXTURES = Path(__file__).parent / "fixtures"
CHALLENGE = (FIXTURES / "challenge_imunify.html").read_text()
CONTENT = '''<!doctype html><html><head><meta charset="utf-8"><title>Kindergarten</title>
<meta name="description" content="A welcoming kindergarten for families."></head>
<body><nav><a href="/about">About</a><a href="/contact">Contact</a></nav>
<main><h1>Welcome to our kindergarten</h1><h2>Learning together</h2>
<p>Selamat datang. 欢迎来到我们的幼儿园。 Our teachers welcome children and their families into a warm learning
environment with creative activities, outdoor play and friendship.</p>
<img src="/photo.jpg" alt="Our playground"></main></body></html>'''


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "playwright_fallback_enabled", True)
    monkeypatch.setattr(settings, "playwright_fallback_timeout_seconds", 1.0)
    verification._limits.clear()
    yield
    verification._limits.clear()


def http_response(monkeypatch, status=200, html=CHALLENGE, headers=None):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(
        status, text=html, headers=headers or {"content-type": "text/html"}
    ))
    monkeypatch.setattr(fast_fetch, "assert_public_url", mock.AsyncMock())
    monkeypatch.setattr(fast_fetch.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))


@pytest.mark.parametrize("status", [200, 403, 503])
async def test_supported_challenge_runs_once_and_returns_existing_contract(enabled, monkeypatch, status):
    http_response(monkeypatch, status)
    fetch = mock.AsyncMock(return_value=verification.VerificationResult(
        final_url="https://site.test/content", html=CONTENT, http_status=200
    ))
    monkeypatch.setattr(verification, "fetch_verified_html", fetch)

    result = await fast_fetch.try_fast_fetch("https://site.test/?secret=hidden")

    assert isinstance(result, FastFetchResult)
    assert result.html == CONTENT
    assert result.final_url == "https://site.test/content"
    fetch.assert_awaited_once()
    parsed = scraper._parse_rendered_html(result.html, result.final_url)
    assert parsed.source_content.nav_links
    assert "learning" in parsed.source_content.raw_text.lower()


@pytest.mark.parametrize("status,html,headers", [
    (200, CONTENT, None), (401, CHALLENGE, None), (404, CHALLENGE, None),
    (429, CHALLENGE, None), (500, CHALLENGE, None),
    (403, '<html><title>Access denied</title></html>', None),
    (200, '<html><body>Please enable JavaScript</body></html>', None),
    (403, CHALLENGE, {"cf-mitigated": "challenge", "content-type": "text/html"}),
])
async def test_unrelated_results_never_trigger_verification(enabled, monkeypatch, status, html, headers):
    http_response(monkeypatch, status, html, headers)
    fetch = mock.AsyncMock()
    monkeypatch.setattr(verification, "fetch_verified_html", fetch)
    await fast_fetch.try_fast_fetch("https://site.test/")
    fetch.assert_not_awaited()


async def test_disabled_retains_old_challenge_result(enabled, monkeypatch):
    monkeypatch.setattr(settings, "playwright_fallback_enabled", False)
    http_response(monkeypatch)
    fetch = mock.AsyncMock()
    monkeypatch.setattr(verification, "fetch_verified_html", fetch)
    result = await fast_fetch.try_fast_fetch("https://site.test/")
    assert result.reason == FastFetchSkipReason.CHALLENGED
    fetch.assert_not_awaited()


async def test_failed_verification_retains_challenged_contract(enabled, monkeypatch):
    http_response(monkeypatch)
    fetch = mock.AsyncMock(return_value=verification.VerificationResult(failure="captcha"))
    monkeypatch.setattr(verification, "fetch_verified_html", fetch)
    result = await fast_fetch.try_fast_fetch("https://site.test/")
    assert result == FastFetchSkipped(FastFetchSkipReason.CHALLENGED, 200, "captcha")
    fetch.assert_awaited_once()


@pytest.fixture
def fake_browser(monkeypatch):
    response = SimpleNamespace(status=200, headers={"content-type": "text/html"})
    response.all_headers = mock.AsyncMock(side_effect=lambda: response.headers)
    page = mock.Mock(
        url="https://site.test/", main_frame=object(), is_closed=mock.Mock(return_value=False),
        content=mock.AsyncMock(return_value=CONTENT), close=mock.AsyncMock(),
    )
    request = mock.Mock(url=page.url, frame=page.main_frame, resource_type="document", is_navigation_request=lambda: True)
    response.request = request
    context = mock.Mock(new_page=mock.AsyncMock(return_value=page), close=mock.AsyncMock())
    handlers = {}
    session = mock.Mock()
    event = {"requestId": "request-1", "request": {"url": page.url}, "resourceType": "Document", "frameId": "main"}

    async def send(method, params=None):
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "main"}}}

    session.send = mock.AsyncMock(side_effect=send)
    session.on = lambda name, handler: handlers.update({name: handler})
    page.on = lambda name, handler: handlers.update({name: handler})
    context.new_cdp_session = mock.AsyncMock(return_value=session)

    async def goto(*args, **kwargs):
        await handlers["Fetch.requestPaused"](event)
        await handlers["response"](response)
        return response

    page.goto = mock.AsyncMock(side_effect=goto)

    @asynccontextmanager
    async def managed(**kwargs):
        assert kwargs == {"ordinary": True}
        try:
            yield context
        finally:
            await page.close()
            await context.close()

    monkeypatch.setattr(verification, "browser_context", managed)
    guard = mock.AsyncMock()
    monkeypatch.setattr(verification, "assert_public_url", guard)
    return SimpleNamespace(page=page, context=context, response=response, session=session, event=event, guard=guard)


async def test_success_closes_context_and_passes_final_html(enabled, fake_browser):
    result = await verification.fetch_verified_html("https://site.test/")
    assert result.html == CONTENT
    fake_browser.page.close.assert_awaited_once()
    fake_browser.context.close.assert_awaited_once()
    assert mock.call("Fetch.continueRequest", {"requestId": "request-1", "interceptResponse": True}) in fake_browser.session.send.await_args_list


@pytest.mark.parametrize("html,status,reason", [
    ('<html><head><title>CAPTCHA</title></head><body>Solve this challenge</body></html>', 200, "captcha"),
    ('<html><head><title>Access denied</title></head><body>Blocked</body></html>', 200, "access_denied"),
    ('<html><body><input type="password">Sign in</body></html>', 200, "authentication_required"),
    (CONTENT, 401, "authentication_required"), (CONTENT, 429, "rate_limited"),
    (CONTENT, 403, "access_denied"), (CONTENT, 500, "http_error"),
    ('<html><body></body></html>', 200, "empty_page"),
    (CHALLENGE, 200, "verification_failed"), (CHALLENGE, 403, "verification_failed"),
])
async def test_failed_pages_never_become_content(enabled, fake_browser, monkeypatch, html, status, reason):
    monkeypatch.setattr(settings, "playwright_fallback_timeout_seconds", 0.3)
    fake_browser.page.content.return_value = html
    fake_browser.response.status = status
    result = await verification.fetch_verified_html("https://site.test/")
    assert result.html is None
    assert result.failure == reason
    fake_browser.context.close.assert_awaited_once()


@pytest.mark.parametrize("error,reason", [
    (PlaywrightTimeout("navigation"), "navigation_timeout"),
    (PlaywrightError("Target page, context or browser has been closed"), "browser_context_closed"),
    (PlaywrightError("net::ERR_NAME_NOT_RESOLVED"), "network_failure"),
])
async def test_browser_errors_are_structured_and_close_resources(enabled, fake_browser, error, reason):
    fake_browser.page.goto.side_effect = error
    result = await verification.fetch_verified_html("https://site.test/")
    assert result.failure == reason
    fake_browser.context.close.assert_awaited_once()


async def test_missing_response_is_structured(enabled, fake_browser):
    fake_browser.page.goto.side_effect = None
    fake_browser.page.goto.return_value = None
    assert (await verification.fetch_verified_html("https://site.test/")).failure == "html_unavailable"


async def test_prohibited_redirect_is_checked_before_dispatch(enabled, fake_browser):
    fake_browser.event["request"]["url"] = "http://169.254.169.254/latest"

    async def validate(url):
        if "169.254" in url:
            raise UnsafeUrlError("blocked")

    fake_browser.guard.side_effect = validate
    result = await verification.fetch_verified_html("https://site.test/")
    assert result.failure == "blocked_url"
    assert mock.call("Fetch.continueRequest", {"requestId": "request-1", "interceptResponse": True}) not in fake_browser.session.send.await_args_list
    assert mock.call("Fetch.failRequest", {"requestId": "request-1", "errorReason": "BlockedByClient"}) in fake_browser.session.send.await_args_list


async def test_navigation_loop_is_bounded(enabled, fake_browser, monkeypatch):
    monkeypatch.setattr(verification, "_MAX_NAVIGATIONS", 0)
    assert (await verification.fetch_verified_html("https://site.test/")).failure == "navigation_limit"
    assert mock.call("Fetch.continueRequest", {"requestId": "request-1", "interceptResponse": True}) not in fake_browser.session.send.await_args_list


async def test_cancellation_closes_resources_and_propagates(enabled, fake_browser):
    started = asyncio.Event()

    async def hang(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    fake_browser.page.goto.side_effect = hang
    task = asyncio.create_task(verification.fetch_verified_html("https://site.test/"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    fake_browser.context.close.assert_awaited_once()


async def test_concurrency_is_process_wide_for_independent_calls(enabled, monkeypatch):
    active = peak = 0

    async def retrieve(url, state):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.02)
            return verification.VerificationResult(html=CONTENT)
        finally:
            active -= 1

    monkeypatch.setattr(verification, "_retrieve", retrieve)
    monkeypatch.setattr(settings, "playwright_fallback_max_concurrency", 2)
    await asyncio.gather(*(verification.fetch_verified_html("https://site.test/") for _ in range(6)))
    assert peak == 2
    assert active == 0


async def test_logs_do_not_include_credentials_or_queries(enabled, fake_browser, caplog):
    with caplog.at_level("INFO", logger=verification.__name__):
        await verification.fetch_verified_html("https://user:password@site.test/private?token=secret")
    assert "site.test" in caplog.text
    assert all(value not in caplog.text for value in ["password", "token", "secret", "/private", CONTENT])


@pytest.mark.parametrize("error,reason", [
    (PlaywrightError("Executable doesn't exist at /private/path"), "chromium_unavailable"),
    (PlaywrightError("Failed to launch process"), "browser_launch_failed"),
])
async def test_launch_errors_are_structured(enabled, monkeypatch, error, reason):
    @asynccontextmanager
    async def failed(**kwargs):
        raise error
        yield

    monkeypatch.setattr(verification, "assert_public_url", mock.AsyncMock())
    monkeypatch.setattr(verification, "browser_context", failed)
    assert (await verification.fetch_verified_html("https://site.test/")).failure == reason


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/", "http://169.254.169.254/", "not a URL"])
async def test_invalid_or_prohibited_initial_urls_never_launch(enabled, monkeypatch, url):
    monkeypatch.setattr(settings, "scrape_allow_private_hosts", False)
    launch = mock.Mock(side_effect=AssertionError("must validate first"))
    monkeypatch.setattr(verification, "browser_context", launch)
    assert (await verification.fetch_verified_html(url)).failure == "blocked_url"
    launch.assert_not_called()


async def test_queue_wait_has_a_deadline_and_does_not_leak_capacity(enabled, monkeypatch):
    gate = asyncio.Event()
    entered = asyncio.Event()
    calls = 0

    async def retrieve(url, state):
        nonlocal calls
        calls += 1
        entered.set()
        await gate.wait()
        return verification.VerificationResult(html=CONTENT)

    monkeypatch.setattr(verification, "_retrieve", retrieve)
    first = asyncio.create_task(verification.fetch_verified_html("https://site.test/"))
    await entered.wait()
    monkeypatch.setattr(settings, "playwright_fallback_timeout_seconds", 0.03)
    assert (await verification.fetch_verified_html("https://site.test/")).failure == "overall_timeout"
    assert calls == 1
    gate.set()
    assert (await first).html == CONTENT
    assert (await verification.fetch_verified_html("https://site.test/")).html == CONTENT


async def test_contact_form_captcha_is_not_a_verification_interstitial(enabled, fake_browser):
    fake_browser.page.content.return_value = CONTENT.replace('</main>', '<div class="g-recaptcha"></div></main>')
    assert (await verification.fetch_verified_html("https://site.test/")).html is not None


async def test_disabled_service_does_not_start_browser(monkeypatch):
    monkeypatch.setattr(settings, "playwright_fallback_enabled", False)
    with mock.patch.object(verification, "browser_context") as launch:
        assert (await verification.fetch_verified_html("https://site.test/")).failure == "disabled"
    launch.assert_not_called()


@pytest.mark.parametrize("ordinary", [True, False])
async def test_lifecycle_preserves_old_profile_and_isolates_new_one(monkeypatch, ordinary):
    context = mock.Mock(close=mock.AsyncMock(), add_init_script=mock.AsyncMock(), route=mock.AsyncMock())
    instance = mock.Mock(close=mock.AsyncMock(), new_context=mock.AsyncMock(return_value=context))
    pw = mock.Mock(chromium=mock.Mock(launch=mock.AsyncMock(return_value=instance)), stop=mock.AsyncMock())
    monkeypatch.setattr(browser, "async_playwright", lambda: mock.Mock(start=mock.AsyncMock(return_value=pw)))
    async with browser.browser_context(ordinary=ordinary):
        pass
    if ordinary:
        instance.new_context.assert_awaited_once_with(service_workers="block")
        context.add_init_script.assert_not_awaited()
        context.route.assert_not_awaited()
        assert pw.chromium.launch.call_args.kwargs["args"] == ["--no-sandbox", "--block-new-web-contents"]
    else:
        context.add_init_script.assert_awaited_once_with(browser.STEALTH_INIT_SCRIPT)
        context.route.assert_awaited_once_with("**/*", browser.block_heavy_resources)
        assert instance.new_context.call_args.kwargs["user_agent"] == browser.BROWSER_USER_AGENT
    context.close.assert_awaited_once()
    instance.close.assert_awaited_once()
    pw.stop.assert_awaited_once()


@pytest.mark.parametrize("stage", ["launch", "context", "setup"])
async def test_partial_initialization_releases_acquired_resources(monkeypatch, stage):
    context = mock.Mock(close=mock.AsyncMock(), add_init_script=mock.AsyncMock(), route=mock.AsyncMock())
    instance = mock.Mock(close=mock.AsyncMock(), new_context=mock.AsyncMock(return_value=context))
    pw = mock.Mock(chromium=mock.Mock(launch=mock.AsyncMock(return_value=instance)), stop=mock.AsyncMock())
    target = {"launch": pw.chromium.launch, "context": instance.new_context, "setup": context.route}[stage]
    target.side_effect = PlaywrightError("failed")
    monkeypatch.setattr(browser, "async_playwright", lambda: mock.Mock(start=mock.AsyncMock(return_value=pw)))
    with pytest.raises(PlaywrightError):
        async with browser.browser_context():
            pass
    pw.stop.assert_awaited_once()
    if stage != "launch":
        instance.close.assert_awaited_once()
    if stage == "setup":
        context.close.assert_awaited_once()


async def test_lazy_context_launches_once_only_when_needed(monkeypatch):
    entered = exited = 0
    context = mock.Mock(new_page=mock.AsyncMock())

    @asynccontextmanager
    async def managed():
        nonlocal entered, exited
        entered += 1
        await asyncio.sleep(0.01)
        try:
            yield context
        finally:
            exited += 1

    monkeypatch.setattr(browser, "browser_context", managed)
    async with browser.lazy_browser_context():
        pass
    assert entered == exited == 0
    async with browser.lazy_browser_context() as lazy:
        await asyncio.gather(*(lazy.new_page() for _ in range(4)))
    assert entered == exited == 1


async def test_http_success_reaches_scraper_without_starting_chromium(monkeypatch):
    fetch = mock.AsyncMock(return_value=FastFetchResult("https://site.test/", CONTENT, 200))
    monkeypatch.setattr(scraper, "try_fast_fetch", fetch)
    monkeypatch.setattr(scraper, "assert_public_url", mock.AsyncMock())
    launch = mock.Mock(side_effect=AssertionError("HTTP success must not start Playwright"))
    monkeypatch.setattr(browser, "async_playwright", launch)
    monkeypatch.setattr(scraper, "build_brand_candidate", mock.AsyncMock(return_value=None))
    result = await scraper.scrape_url("https://site.test/", respect_robots=False)
    assert result.source_content.nav_links
    launch.assert_not_called()


@pytest.fixture
def local_site():
    """No third-party traffic: a real browser executes only these local fixtures."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            requests.append((path, self.headers.get("Cookie", "")))
            status, body, headers = 200, CONTENT, {}
            if path == "/verify":
                body = (FIXTURES / "verification_redirect.html").read_text()
            elif path == "/content" and "fixture_verified=1" not in self.headers.get("Cookie", ""):
                status, body = 401, "Sign in"
            elif path == "/redirect":
                status, body, headers = 302, "", {"Location": "/redirect-again"}
            elif path == "/redirect-again":
                status, body, headers = 302, "", {"Location": f"http://localhost:{self.server.server_port}/secret"}
            elif path == "/resources":
                body = CONTENT.replace('/photo.jpg', f'http://localhost:{self.server.server_port}/secret')
            elif path == "/frames":
                body = CONTENT.replace('</main>', f'<iframe src="http://localhost:{self.server.server_port}/secret"></iframe></main>')
            elif path == "/popup":
                body = CONTENT + f'<script>window.open("http://localhost:{self.server.server_port}/secret")</script>'
            elif path == "/websocket":
                body = CONTENT + f'<script>new WebSocket("ws://localhost:{self.server.server_port}/secret")</script>'
            elif path == "/worker":
                body = CONTENT + '<script>new Worker("/worker.js")</script>'
            elif path == "/worker.js":
                body = f'fetch("http://localhost:{self.server.server_port}/secret")'
            elif path == "/compressed":
                body = gzip.compress(CONTENT.encode("utf-8"))
                headers = {"Content-Encoding": "gzip"}
            elif path == "/latin":
                body = CONTENT.replace('charset="utf-8"', 'charset="iso-8859-1"').replace('欢迎来到我们的幼儿园。', "Bienvenue à l'école.").encode("iso-8859-1")
            self.send_response(status)
            content_type = "application/javascript; charset=utf-8" if path == "/worker.js" else "text/html; charset=utf-8"
            if path == "/latin":
                content_type = "text/html; charset=iso-8859-1"
            self.send_header("Content-Type", content_type)
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else body.encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", requests=requests)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.fixture
def local_browser(enabled, monkeypatch):
    monkeypatch.setattr(settings, "playwright_fallback_timeout_seconds", 8)
    original_guard = verification.assert_public_url

    async def local_guard(url):
        # Only our exact fixture address may use loopback. `localhost` remains
        # prohibited, so redirect/subresource tests exercise the actual guard.
        if urlsplit(url).hostname == "127.0.0.1":
            return url
        return await original_guard(url, allow_private=False)

    monkeypatch.setattr(verification, "assert_public_url", local_guard)


async def test_real_chromium_executes_verification_js_and_discards_session(local_site, local_browser):
    for _ in range(2):
        result = await verification.fetch_verified_html(local_site.url + "/verify")
        assert result.failure is None
        assert result.final_url == local_site.url + "/content"
        parsed = scraper._parse_rendered_html(result.html, result.final_url)
        assert parsed.source_content.nav_links
    assert [cookie for path, cookie in local_site.requests if path == "/verify"] == ["", ""]
    assert all("fixture_verified=1" in cookie for path, cookie in local_site.requests if path == "/content")


@pytest.mark.parametrize("path", ["/redirect", "/resources", "/frames"])
async def test_real_chromium_never_dispatches_prohibited_redirect_or_resource(local_site, local_browser, path):
    result = await verification.fetch_verified_html(local_site.url + path)
    assert result.failure == "blocked_url"
    assert all(request_path != "/secret" for request_path, _ in local_site.requests)


@pytest.mark.parametrize("path", ["/popup", "/websocket", "/worker"])
async def test_real_chromium_blocks_unguarded_secondary_network_targets(local_site, local_browser, path, monkeypatch):
    state = verification._Navigation()
    monkeypatch.setattr(verification, "_Navigation", lambda: state)
    result = await verification.fetch_verified_html(local_site.url + path)
    assert result.failure in {None, "blocked_url"}
    assert (state.headers or {}).get("content-security-policy") == "connect-src http: https:"
    assert all(path != "/secret" for path, _ in local_site.requests)


async def test_real_http_challenge_flows_through_existing_scraper(local_site, local_browser, monkeypatch):
    monkeypatch.setattr(fast_fetch, "assert_public_url", verification.assert_public_url)
    monkeypatch.setattr(scraper, "assert_public_url", verification.assert_public_url)
    monkeypatch.setattr(scraper, "build_brand_candidate", mock.AsyncMock(return_value=None))
    result = await scraper.scrape_url(local_site.url + "/verify", respect_robots=False)
    assert result.final_url == local_site.url + "/content"
    assert "learning" in result.source_content.raw_text.lower()
    assert result.source_content.nav_links


@pytest.mark.parametrize("path,expected", [("/compressed", "欢迎来到我们的幼儿园"), ("/latin", "Bienvenue à l'école")])
async def test_real_chromium_preserves_source_text_encoding(local_site, local_browser, path, expected):
    result = await verification.fetch_verified_html(local_site.url + path)
    assert result.failure is None
    assert expected in result.html
    assert "Selamat datang" in result.html
    assert 'src="/photo.jpg"' in result.html
