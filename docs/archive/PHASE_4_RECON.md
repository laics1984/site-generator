# Phase 4 Recon — Webtree CMS Push

Confirmed by reading the live source in `webtree-cms-api/` (Laravel). Use this
as the authoritative spec when building `services/cms_client.py` and the push
orchestrator.

---

## Auth

Two-path JWT auth via `cms.auth` middleware
([`CmsAuthMiddleware.php`](../webtree-cms-api/app/Http/Middleware/CmsAuthMiddleware.php)).
We use the **Bearer JWT** path:

```http
Authorization: Bearer <jwt>
```

JWT is obtained via:
```http
POST /api/auth/login
{ "email": "...", "password": "..." }
→ { "access_token": "...", "token_type": "bearer", "expires_in": 3600 }
```

Route group: `routes/api_admin.php:235` — `Route::post('login', 'AuthController@login')`.

The `entity_api_token` is the per-entity scoping key — included in URL paths
for page endpoints and as a `entity` form field for media uploads.

---

## Page endpoints

All under `prefix: entities/{entityApiToken}/pages` with `cms.auth` middleware.

| Method | Path | Purpose | Required body keys |
|---|---|---|---|
| `GET`    | `/` | List pages | — |
| `POST`   | `/` | Create page | `title`, optional: `description`, `slug`†, `isHomepage`, `templateFor`, `seo.{title,description,noindex}` |

† **Slug is flat kebab-case only.** The CMS regex is `^[a-z0-9]+(?:-[a-z0-9]+)*$` (max 160 chars). The CMS only strips a leading `/` before validating — internal slashes, spaces, underscores, and trailing/double hyphens all 422. The generator emits hierarchical slugs like `services/web-design` from crawled URL paths, so the push orchestrator runs a normalization pass (`_normalize_site_slugs` in `push_orchestrator.py`) that flattens slugs, remaps `parent_slug` + `page_tree`, and rewrites baked nav `href`s before any page is created.
| `GET`    | `/{pageId}` | Read metadata | — |
| `PATCH`  | `/{pageId}` | Update metadata | (subset of metadata) |
| `GET`    | `/{pageId}/builder?mode=draft` | **Read full builder payload (gets concurrency tokens)** | — |
| `PUT`    | `/{pageId}/draft` | Save body schema | `baseDraftVersion`, `bodySchema` |
| `PUT`    | `/{pageId}/layout` | Save header/footer/menus | `expectedLayoutVersionId`, `headerSchema`, `footerSchema`, `menus` |
| `POST`   | `/{pageId}/publish` | Publish | `expectedDraftVersion`, `expectedLayoutVersionId` |
| `DELETE` | `/{pageId}` | Archive (soft delete) | — |
| `DELETE` | `/{pageId}/force` | Hard delete archived page | — |
| `POST`   | `/{pageId}/restore` | Unarchive | — |

### Prohibited fields per endpoint

The Laravel `Request` classes use Laravel's `prohibited` rule to lock down
what can be sent. **Sending these will 422.**

- `CreatePageRequest`: prohibits `bodySchema`, `headerSchema`, `footerSchema`, `menus`, `builderStyles`, `status`, `publishedRevisionId`
- `SavePageDraftRequest`: prohibits `title`, `description`, `slug`, `status`, `seo`, `headerSchema`, `footerSchema`, `menus`, `builderStyles`, `publishedRevisionId`
- `SaveLayoutRequest`: prohibits `baseDraftVersion`, `title`, `description`, `slug`, `status`, `seo`, `bodySchema`, `builderStyles`, `publishedRevisionId`
- `PublishPageRequest`: prohibits everything except `expectedDraftVersion` + `expectedLayoutVersionId`

**`builderStyles` is rejected on every page endpoint** — see next section.

---

## BuilderStyles — entity-scoped, separate endpoint

Stored on the **Entity** model, not the page (verified via
`app/Models/Entity.php:37`). Has a dedicated controller:

| Method | Path | Auth | Body |
|---|---|---|---|
| `GET` | `/builder/styles` | `builder.auth` (builder session cookie) | — |
| `PUT` | `/builder/styles` | `builder.auth` | `{ builder_styles: { colors, typography, buttons, page } }` |

⚠️ **The `builder.auth` middleware uses a session cookie, not the JWT.** The
push solves this with the launch-code bridge (`mint_builder_session` in
`cms_client.py`):

1. `POST /api/builder/launch` (JWT, body `{entity_api_token}`) →
   `{"launch_url": "<builder>/?code=<token>", "expires_in": 60}`. **The code
   is URL-encoded into `launch_url`'s query string** — parse it out with
   `urllib.parse.parse_qs`, do NOT expect a top-level `code` field.
2. `POST /api/builder/redeem` (no auth, body `{code}`) → sets a `Set-Cookie`
   header carrying the builder session. Capture with `dict(resp.cookies)`.
3. `PUT /api/builder/styles` with that cookie jar → entity-scoped theme update.

The code is single-use and short-lived (60s) — mint per push.

Validation rules from `BuilderStyleController.php:60+`:
- `builder_styles.colors.{primary,secondary,accent,text,background,surface}`: hex strings (regex `^#?[0-9A-Fa-f]{3,8}$`)
- `builder_styles.typography.{headingFont,bodyFont}`: string, max 255
- `builder_styles.buttons.{background,text}`: hex; `buttons.radius`: int 0-48
- `builder_styles.page.{widthMode,maxWidth,background}`: string/int

These match our `ThemeTokens.to_builder_styles()` output exactly.

---

## Header / footer wrapper shapes

From `builder/src/lib/site-navigation.ts:80-156`:

```ts
type RegionPresetMeta = {
  id: string | null      // null if not based on a preset
  label?: string
  version?: number
}

type HeaderBehavior = {
  position: 'static' | 'sticky'
  overlay: boolean
}

type HeaderSlots = {
  primaryMenuId: string | null
  utilityMenuId: string | null
}

type FooterSlots = {
  footerMenuId: string | null
  legalMenuId:  string | null
  socialMenuId: string | null
}
```

Therefore the payloads for `PUT /pages/{id}/layout` should be:

```jsonc
{
  "expectedLayoutVersionId": "<uuid from GET /builder>",
  "headerSchema": {
    "elements": [/* BuilderElement[] — typically one root __header element */],
    "behavior": { "position": "sticky", "overlay": false },
    "preset":   { "id": null },
    "slots":    { "primaryMenuId": "menu-primary", "utilityMenuId": null }
  },
  "footerSchema": {
    "elements": [/* BuilderElement[] — typically one root __footer element */],
    "preset":   { "id": null },
    "slots":    { "footerMenuId": "menu-footer", "legalMenuId": "menu-legal", "socialMenuId": null }
  },
  "menus": [ /* SiteMenu[] */ ]
}
```

Defaults from `defaultHeaderBehavior`, `defaultHeaderSlots`, `defaultFooterSlots`
(same file) — safe to copy verbatim if we don't have specific menu IDs.

### Menu shape — required by `/layout`

Constants from `site-navigation.ts:136`:

```ts
DEFAULT_PRIMARY_MENU_ID = 'menu-primary'
DEFAULT_UTILITY_MENU_ID = 'menu-utility'
DEFAULT_FOOTER_MENU_ID  = 'menu-footer'
DEFAULT_LEGAL_MENU_ID   = 'menu-legal'
DEFAULT_SOCIAL_MENU_ID  = 'menu-social'
```

We'll emit a `menus` array containing entries for whichever IDs we wired into
the header/footer slots:

```jsonc
[
  { "id": "menu-primary", "name": "Primary", "purpose": "primary",
    "items": [{ "id":"...","label":"Home","href":"/","visible":true }, ...] },
  { "id": "menu-footer",  "name": "Footer",  "purpose": "footer",  "items": [...] },
  { "id": "menu-legal",   "name": "Legal",   "purpose": "legal",
    "items": [{ "id":"...","label":"Privacy Policy","href":"/privacy"}, ...] }
]
```

Our `services/header_footer.py` currently inlines all links directly in the
BuilderElement tree rather than referencing menus. **For Phase 4 we'll need to
populate `menus` from `page_tree` and let header/footer reference them via
slot IDs.** (Or keep inlining and pass empty menus — risk: builder UI may
overwrite the inline header with the preset.)

---

## Media upload

[`MediaController.php:32`](../webtree-cms-api/app/Http/Controllers/MediaController.php#L32)

```http
POST /api/file/add
Authorization: Bearer <jwt>
Content-Type: multipart/form-data

file:   <binary>                  // jpg|jpeg|png|pdf|doc|docx|xls|xlsx|odt|ods, max 20 MB
entity: <entity_api_token>
```

**Response**:
```json
{ "t": "p", "i": "<asset-url>" }      // success → use "i" as the new src
{ "t": "f", "errors": "<message>" }   // failure
```

URL format: `{ASSET_URL}/storage/artifect/obj/{first4OfToken}/{token}/{year}/{month}/{filename}`.

⚠️ **Limitations**:
- Only `jpg|jpeg|png` for images — `webp`, `gif`, `avif`, `svg` are rejected
- Our PDF-extracted images are already PNG (we convert via PyMuPDF) ✓
- Pexels typically serves JPEG ✓
- Data URLs from DOCX may need format normalisation (we already store with proper MIME tags)
- 20 MB cap

**Push orchestrator pseudocode**:
```python
for every BuilderElement.content.src in schema:
    if src.startswith("data:image/"):
        decode → POST /file/add → rewrite src to response["i"]
    elif src is a Pexels CDN URL:
        fetch bytes → POST /file/add → rewrite src to response["i"]
    elif src is already on webtree (ASSET_URL match):
        skip
    else:
        leave as-is (external https url)
```

---

## Concurrency tokens — `GET /pages/{id}/builder?mode=draft`

Returns a `PageBuilderPayload` (see `builder/src/lib/page-management.ts:93`):

```ts
{
  mode: "draft",
  page: { id, draftVersion, ... },
  revision: { id, revisionNo, ... },
  layout: { id, scope, versionId, versionNo, ... },
  bodySchema: { elements: [...] },
  headerSchema: BuilderTemplateHeader,
  footerSchema: BuilderTemplateFooter,
  menus: SiteMenu[],
  builderStyles: BuilderStyles,
  concurrency: { ... }
}
```

For our push flow, we need:
- `page.draftVersion` → for `baseDraftVersion` on `/draft` and `expectedDraftVersion` on `/publish`
- `layout.versionId` → for `expectedLayoutVersionId` on `/layout` and `/publish`

**`POST /pages` also returns these** in its response so we don't need a separate
`GET /builder` for newly-created pages — verified via `transformCreatedPage()`
in `PageManagementService.php:114`.

---

## Recommended push flow

```
0. Normalize slugs (in-memory): flatten any "a/b" → "a-b", remap parent_slug,
   page_tree nodes, and baked href attrs in header/footer/body. Homepage → "".
1. Auth: POST /api/auth/login → JWT
2. (Optional) Create new entity: POST /api/entities (JWT, body {entity_name,
   entity_url?, builder_styles?}) → mints entity_api_token + provisions the
   companion website layout in one transaction. Used when the push UI is in
   "Create new entity" mode instead of taking a pre-existing token.
3. Empty-entity guard: GET /entities/{token}/pages — abort unless 0 pages
   or force_overwrite=true.
4. Media upload: walk schemas, find unique data:/external image srcs,
   POST /api/file/add → rewrite each src to response["i"] (CDN URL).
5. For each generated page:
     POST /entities/{token}/pages              → capture {pageId, draftVersion}
     [first iteration only:] GET /entities/{token}/pages/{pageId}/builder
       → capture layout.versionId
6. Save shared layout on the first page (one update covers all pages):
     PUT /entities/{token}/pages/{firstPageId}/layout
        { expectedLayoutVersionId, headerSchema, footerSchema, menus }
     → capture new layout.versionId for subsequent publish calls
7. For each page:
     PUT /entities/{token}/pages/{pageId}/draft
        { baseDraftVersion, bodySchema }
     → capture new draftVersion
8. BuilderStyles via launch-code bridge (entity-scoped, builder.auth):
     POST /api/builder/launch (JWT) → parse code from launch_url query string
     POST /api/builder/redeem (no auth, body {code}) → capture Set-Cookie jar
     PUT  /api/builder/styles (cookies) { builder_styles: {...} }
9. (Optional, only if publish=true) For each page:
     POST /entities/{token}/pages/{pageId}/publish
        { expectedDraftVersion, expectedLayoutVersionId }
```

### Idempotency anchors
- Per-page step records: `pageId`, latest `draftVersion`, latest `layoutVersionId`
- On retry: re-read versions via `GET /builder`, resume from the failing step
- Media uploads should be idempotent by content hash (skip if same hash already uploaded for this entity in last hour — local cache)

---

## Resolved in implementation

1. ✅ **`builderStyles` push** — solved via the launch-code bridge (above), not punted as originally planned.
2. ✅ **Hierarchical slugs vs. flat CMS slugs** — solved by `_normalize_site_slugs` running as push step 0.
3. ✅ **Greenfield-only constraint** — enforced by the empty-entity guard with a `force_overwrite` escape hatch.
4. ✅ **Entity creation from the generator** — added `POST /api/entities` on the CMS side (mints token + provisions website layout in one transaction). Generator calls it when push UI is in "Create new entity" mode.

## Still open

1. ❓ Does pushing a `menus` array with new IDs replace the entity's menus, or merge? — only matters on re-push to an entity with existing menus. Safe: fetch existing menus first, merge. Greenfield pushes are unaffected.
2. ❓ `templateFor: 'article'|'event'|'articleListing'` for CMS template pages — out of scope; we only push regular pages today.
3. ❓ webp / avif / gif / svg media — `MediaController` accepts jpg/png only. These images currently get skipped from upload and their `src` stays as the external URL. Fix path: Pillow conversion pre-upload (jpeg fallback).
4. ❓ Idempotent re-push — today each push uploads media fresh and (in "create new entity" mode) creates a new entity even on retry. A failed push leaves an orphan empty entity.

All four original gaps closed. Ready to build Phase 4.
