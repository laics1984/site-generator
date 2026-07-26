# Site Generator — Frontend

Vite + React + Tailwind UI for the webtree site generator. Talks to the FastAPI
backend at `http://localhost:8001` via a proxied `/api` prefix.

## Setup

```bash
cd frontend
npm install
npm run dev
```

UI runs on `http://localhost:5174`.

## The two stages

The whole app is one screen with two stages, driven by `site` + `formOpen` in
[`src/App.tsx`](src/App.tsx):

**Compose** — a centred wizard: Brand → Source → Pages. The source step is a
five-way state machine (input / scope choice / crawl progress / scrape preview /
page picker) rendered into one card.

**Preview** — once a site is generated the wizard collapses into
[`SiteSummaryBar`](src/components/SiteSummaryBar.tsx) and the rendered site takes
the screen. The wizard is *hidden, not cleared*, so:

- **Regenerate** re-runs the stored payload verbatim (the model varies run to run),
- **Adjust & regenerate** reopens the form on the page picker with selections intact,
- **← Back to preview** returns without generating anything.

`Publish` opens [`PublishDrawer`](src/components/PublishDrawer.tsx) — the push
into the webtree CMS, and the handoff into the admin suite.

## Directory map

| Path | What it is |
|---|---|
| `src/ui/` | Tool-chrome primitives (Button, Field, Card, Segmented, Banner, Drawer, Stepper, Spinner). Everything around the preview should come from here. |
| `src/components/` | The generator's own screens, styled with Tailwind + `src/ui`. |
| `src/preview/` | **Vendored** port of the `webtree-public` renderer. Read [its README](src/preview/README.md) before touching anything in it — it must not diverge from upstream. |
| `src/lib/` | Wire types, the API client, and preview-navigation helpers. |

## The preview

`PagePreview` renders the generated site inside a same-origin `<iframe>` via a
React portal, so the tool's Tailwind 3 and the renderer's Tailwind 4 + `wt-*`
layer never collide, and the tablet/mobile widths exercise real media queries
rather than simulating them. It fills the viewport, goes fullscreen, and
navigates: clicking a nav item whose href matches a generated slug switches the
previewed page.

Header, footer and menus come from `POST /api/preview/layout`, which the backend
builds with the same `build_layout_payload()` the CMS push uses — so the preview
and the published page start from one payload.
