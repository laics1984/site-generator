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

## Phase 1 capability

- Mode switcher (URL / Document) — paste-fallback inputs while scraper and doc parser
  ship in Phases 2 and 3
- Ollama health indicator in the header
- Live generation against `POST /api/generate/from-source`
- Multi-page result browser with per-page section breakdown and SEO panel

## Phase roadmap

- **2** URL scraping inputs + auto-extracted preview
- **3** PDF / DOCX upload with parsed-content preview
- **4** "Push to webtree CMS" flow (API token + entity picker)
- **5** Iframe rendering of generated pages (true visual preview) and per-section regenerate
