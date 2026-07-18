# Security

This document records the trust model of the Webtree Site Generator, the risks
found during the hardening pass, the fixes applied, and the residual items.

## Trust model

The generator is a **single-user, local-first developer tool**. It runs on one
machine (typically alongside a local Ollama/MLX model) and, in Docker, reaches
the host over `host.docker.internal`. It has **no authentication by design** —
every endpoint is open. This is acceptable *only* while the backend is bound to
localhost / a trusted LAN. **Do not expose port 8001 to the public internet.**
If you ever need to, put an authenticating reverse proxy in front of it and
review the CMS-credential endpoints below first.

## Risks found & fixes applied

### 1. Live API key tracked in git — FIXED (rotation required)
`.env` was committed (added before the `.gitignore` rule, so the rule was inert)
and contained a **live `PEXELS_API_KEY`**.
- **Fix:** `git rm --cached .env` — the file is now untracked and genuinely
  ignored; it stays on disk for local use only.
- **Action still required (you):** the key remains in prior git history, so
  **rotate it** at <https://www.pexels.com/api/>. History was intentionally
  *not* rewritten (a rewrite changes every commit hash and needs a force-push).

### 2. SSRF in the scrape/fetch layer — FIXED
The scrape endpoints fetched any user-supplied URL after only a `http(s)://`
prefix check, following redirects, inside a container that can reach the CMS,
cloud metadata (`169.254.169.254`), and RFC1918 hosts.
- **Fix:** `services/url_guard.py::assert_public_url` resolves each URL's host to
  its IP(s) and refuses loopback / private / link-local / reserved / multicast
  addresses and the Docker host aliases. It is enforced at every fetch boundary:
  the `/api/scrape/*` routers (clean `400`), the `scrape_url` / `extend_crawl`
  engines, both fetch choke points (`fast_fetch.try_fast_fetch` and
  `scraper._goto_and_render`), the sitemap probe, the logo fetch, and scraped
  image downloads (`image_vision`).
- **Escape hatch:** `SCRAPE_ALLOW_PRIVATE_HOSTS=true` re-enables localhost/LAN
  targets for local development only.
- **Residual:** a blind SSRF via a single mid-redirect hop that never returns
  content isn't fully prevented; each *rendered* URL is re-validated, so a
  redirect landing on an internal host is refused when that host is fetched.

### 3. Over-permissive CORS — FIXED
`allow_methods=["*"]` / `allow_headers=["*"]` with `allow_credentials=True` was
narrowed to the methods/headers the API actually uses (`GET/POST/DELETE`,
`Content-Type`). Origins remain the configured localhost list.

### 4. Credentials in client repr — FIXED
`CmsClient.jwt` and the builder-session cookie jar are now `repr=False`, so an
accidental `repr()` or exception dump can't leak the bearer token.

### 5. Non-hermetic tests hitting the live key — FIXED
Because the real Pexels key was present, the test suite made **live Pexels API
calls** (`get_pexels_client()` is an `@lru_cache` singleton that captured the
key). `tests/conftest.py` now nulls the key and clears that cache around every
test, so the suite is deterministic and offline.

## Verified

- `POST /api/scrape/{probe,start}` with `169.254.169.254`, `127.0.0.1`, and
  `host.docker.internal` all return `400`; public hosts still scrape.
- `repr(CmsClient(jwt=...))` contains no token.
- Full suite (659 tests) green and offline.

## Notable properties already in place (kept)

- Secrets are **not logged**: startup logging omits `REASONING_API_KEY`;
  `/health/llm` and `/health/pexels` deliberately exclude the key.
- Upload handling caps size (20 MB) and sanitises the `Content-Disposition`
  filename (no header injection).
- Crawl bounds use Pydantic `Field(ge=, le=)` — no unbounded crawls.

## Remaining recommendations (not done — future, if the deployment model changes)

- **Authentication** on all endpoints (or enforced localhost binding) before any
  non-local exposure — the CMS `test-connection` / `push` routes accept
  email+password in the request body and proxy them to the CMS.
- **History scrub** of the leaked key if the repo is ever published
  (`git filter-repo` / BFG) — deferred per the current single-user scope.
- Rate limiting / request quotas on the LLM- and Playwright-backed endpoints.
