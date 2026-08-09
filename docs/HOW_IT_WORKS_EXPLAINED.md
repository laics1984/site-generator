# How the Webtree Site Generator Actually Works

*A ground-up explanation. No Python knowledge assumed, no technical detail skipped.*

---

## How to read this document

Each section has three layers. Read only the first layer for the story; read all three when you
want to open the file and follow along.

> **Plain version** — what's happening, in normal words.
>
> **The detail** — the real mechanism, with numbers.
>
> **In the code** — the file and function, so you can go look.

---

# Part 0 — The vocabulary you need first

Skip this if you already know what HTML, a headless browser, and JSON are. Everything after this
part assumes you've read it.

## 0.1 What a web page actually is

When you visit a website, your browser downloads a text file. Not a picture, not a document — a
text file written in a language called **HTML**. It looks roughly like this:

```html
<h1>Sunrise Kindergarten</h1>
<p>We've cared for children in Petaling Jaya since 1998.</p>
<img src="/photos/classroom.jpg" alt="Children painting">
```

Three things to notice, because the whole scraper depends on them:

1. **Everything is a labelled box.** `<h1>` means "big heading". `<p>` means "paragraph".
   `<img>` means "picture". These labels are called **tags**.
2. **Boxes nest inside boxes.** A `<div>` (a generic box) can contain a `<p>` which contains a
   `<span>`. This nesting forms a tree — like a folder structure. The technical name for this tree
   is the **DOM** (Document Object Model). When you hear "walk up the DOM", it means "start at this
   picture and look at its parent box, then that box's parent, and so on".
3. **Tags carry extra information in attributes.** `src="/photos/classroom.jpg"` and
   `alt="Children painting"` are attributes. `alt` is a text description for screen readers — and
   it's one of the most valuable things a scraper can find, because it tells you what a photo is
   *of* without looking at the pixels.

**The catch:** HTML says *what* things are, not what they look like. A separate language, **CSS**,
says "make headings 48 pixels tall, put this photo as the background of that box". So a photo can
appear on a page in two completely different ways:

- as an `<img>` tag — a picture *in* the content
- as a CSS `background-image` on a box — a picture *behind* the content

That distinction matters enormously later. A background photo has text written over it. An `<img>`
photo doesn't. If the generator confuses the two, you get a page with your headline drawn over
someone's face, or a decorative texture cropped into a feature card.

## 0.2 What "scraping" means

Scraping = downloading that HTML text file and pulling structured information out of it.

The naive version is easy. The hard version — the one this codebase does — is hard because:

- **The HTML isn't organised for you.** There's no `<company-name>` tag. You have to infer that the
  first big heading is probably the company name, or that a box whose CSS class contains the word
  "team" probably holds staff profiles.
- **Modern sites build themselves after loading.** More on this next.
- **Every site is built differently.** WordPress, Wix, Squarespace and a hand-coded site all express
  "a team member card" with completely different HTML.

## 0.3 Static pages vs JavaScript pages — and why there are two fetchers

Some websites send you the finished HTML immediately. Download it, read it, done. These are called
**static** or **server-rendered** pages.

Other websites send you an almost-empty HTML file plus a program (written in **JavaScript**) that
runs *inside your browser* and builds the page. Download that HTML and you get roughly:

```html
<div id="root"></div>
```

Nothing. The content only exists after the JavaScript runs.

So the generator has two ways to fetch a page:

| Method | How | Speed | Sees JavaScript content? |
|---|---|---|---|
| **httpx** | Plain download, like `curl` | 250–600ms | No |
| **Playwright** | Launches a real Chrome browser with no visible window ("headless"), loads the page, waits for the JavaScript, then reads the result | 3–5 seconds | Yes |

Playwright is ~10× slower but always works. So the code tries httpx first and only falls back to
Playwright when the fast result looks empty. On a 20-page crawl that's the difference between ~12
seconds and ~70 seconds of fetching.

> **In the code:** `backend/app/services/fast_fetch.py` is the httpx path.
> `scraper._goto_and_render` (scraper.py:319) is the Playwright path.
> The test for "did the fast path work?" is: run a text extractor on the result and check it produced
> at least **500 characters**. Below that, it's a JavaScript shell — use Playwright.

## 0.4 What the LLM does here — and, more importantly, what it doesn't

An **LLM** (Large Language Model) is the thing behind ChatGPT. You give it text, it gives you text
back. This project runs one on your own hardware (an RTX 5060 Ti box) rather than sending your
clients' content to a cloud service.

Here is the single most important architectural decision in this codebase:

> **The LLM writes words. It never decides what anything looks like.**

The LLM's entire output is small structured chunks like this:

```json
{ "kind": "hero",
  "headline": "Where curious children become confident learners",
  "eyebrow": "Family-run since 1998",
  "image_query": "children painting in bright classroom" }
```

That's it. It says "there should be a hero section, here's the headline". It does **not** say what
colour, what font, what layout, how tall, or which photo. A separate, ordinary, deterministic
program decides all of that.

**Why this matters:** if you swap the model tomorrow — different vendor, different size, different
mood on a Tuesday — the words change but the site still looks exactly as designed. If the LLM were
producing layout, every model swap would be a visual regression. This is stated as a hard rule in
`CLAUDE.md` and it's enforced by the code structure, not by discipline.

## 0.5 The two "languages" of this app

You'll see two data shapes everywhere. Getting these straight makes the rest of the codebase legible.

**`ContentBlock` — the semantic language.** *What* a section says. Produced by the LLM.

```
HeroBlock(headline=..., eyebrow=..., image_query=..., layout="background")
TeamBlock(heading=..., members=[TeamMember(name=..., role=...), ...])
FaqBlock(heading=..., items=[FaqItem(question=..., answer=...), ...])
```

**`BuilderElement` — the visual language.** *How* it's drawn. Produced by the generator's own code,
and it is exactly the format the Webtree visual editor reads and writes.

```
BuilderElement(type="section", styles={backgroundColor: ..., padding: ...}, content=[
  BuilderElement(type="container", styles={...}, content=[
    BuilderElement(type="text", styles={fontSize: ...}, content={innerText: "Where curious..."})
  ])
])
```

One module, `schema_builder.py`, converts the first into the second. It is 4,700 lines long and it
owns 100% of the styling. Everything before it is content; everything after it is pixels.

> **In the code:** `backend/app/models/content_blocks.py` defines the semantic language.
> `backend/app/models/builder_schema.py` defines the visual one — and this file must stay a mirror of
> `../builder/src/lib/site-navigation.ts` in the editor repo, or the editor can't open what you generate.

## 0.6 Five Python words you'll bump into

You don't need to write Python, but these five appear constantly in the code and in this document.

**`async` / `await`.** Normally a program does one thing at a time: fetch a page (wait 3 seconds,
doing nothing), fetch the next page (wait 3 seconds)… `async` lets it start a fetch, and while
*waiting for the network*, go do something else. `await` marks the "I'm now waiting here, go do
other work" point. This is why the crawler can have several pages in flight at once with a single
program. It is **not** the same as using multiple processor cores — it only helps with waiting.

**A "dict".** A lookup table: `{"restaurant": "playful", "saas": "modern"}`. Give it a key, get a
value. You'll see these used as decision tables all over the design engine.

**A "dataclass" / "pydantic model".** A named shape with named fields — like a form with labelled
boxes. `HeroBlock` above is one. **Pydantic** models additionally *validate*: if the LLM sends
`"brand_mood": "vibrant"` and only six moods are allowed, pydantic rejects it. The code then uses
that rejection to heal the value rather than crash (§5.1).

**A "regex" (regular expression).** A pattern for finding text. `\d+%` means "one or more digits
followed by a percent sign". Used to spot things like "95% of parents recommend us" in page text.

**"Deterministic".** Same input always produces exactly the same output. The opposite of the LLM,
which can answer differently each time. Most of this codebase is deliberately deterministic — that's
what makes it testable and what makes regenerating a site reproducible.

---

# Part 1 — The big picture

## The metaphor

This system is **not** an architect inventing a building. It's a **renovation crew**.

You point it at an existing shop. It surveys the shop carefully, keeps everything real about it
(the name, the staff, the prices, the photos on the walls), and rebuilds it in a modern shell with
better lighting and a cleaner layout. It is explicitly forbidden from inventing a second floor that
was never there.

That "never invent" rule shows up in the code as dozens of small guards, and it's the source of most
of the system's complexity.

## The assembly line

```
   YOU CLICK "FETCH SITE"
            │
   ┌────────▼─────────┐
   │ 1. PEEK          │  Read the site's own index (sitemap). 1-3 seconds.
   │                  │  "This site has 142 pages — want all of them or a quick 20?"
   └────────┬─────────┘
   ┌────────▼─────────┐
   │ 2. SURVEY        │  Visit up to 20 pages with a real browser.
   │    (the crawl)   │  Extract text, photos, staff, menus, PDFs — and MEASURE
   │                  │  every photo so we know its job on the page.
   └────────┬─────────┘
            │  ← you review and edit the extracted text here. NO AI HAS RUN YET.
   ┌────────▼─────────┐
   │ 3. PLAN THE      │  AI reads the content once: "this is a kindergarten,
   │    STRUCTURE     │  friendly mood". Then ordinary code decides which pages
   │                  │  exist and what sections each one needs. No AI in that part.
   └────────┬─────────┘
            │  ← you tick which pages you want
   ┌────────▼─────────┐
   │ 4. WRITE THE     │  AI rewrites the copy, page by page, grounded in that
   │    COPY          │  page's own scraped text. The expensive step.
   └────────┬─────────┘
   ┌────────▼─────────┐
   │ 5. FACT-CHECK    │  Ordinary code deletes anything the AI made up.
   └────────┬─────────┘
   ┌────────▼─────────┐
   │ 6. BUILD & STYLE │  Ordinary code turns content into a real page: picks
   │                  │  layouts, resolves photos, applies colours, checks contrast.
   └────────┬─────────┘
   ┌────────▼─────────┐
   │ 7. PUBLISH       │  Upload images, create pages, save, publish to the CMS.
   └──────────────────┘
```

**Where the AI is used — four calls total**, plus two small optional ones:

| # | What it decides | Why AI and not code |
|---|---|---|
| 1 | What business is this? (name, industry, mood) | Requires reading comprehension |
| 2 | Which colour palette and font pairing | Taste, from a curated shortlist |
| 3 | **All the page copy** | The actual writing job |
| 4 | Which layout variant per section | Taste, from the real catalogue |
| + | Is this photo of a person or a logo? | Vision — looking at pixels |
| + | Which of these two similar photos fits better? | Tiebreaker only, ~0–2 times per site |

Everything else — which pages exist, page hierarchy, section order, all colours, all spacing, all
photo ranking, all contrast fixes — is ordinary deterministic code.

---

# Part 2 — Phase by phase

## Phase 1 — The peek (sitemap probe)

> **Plain version.** Before spending a minute crawling, ask the website for its own table of
> contents. Most sites publish one. If it says "142 pages", we stop and ask you whether you want a
> quick sample or the whole thing, instead of silently grabbing 20 and pretending that's the site.

> **The detail.** Websites publish an index at `/sitemap.xml` — an XML file listing every page.
> The code first reads `/robots.txt`, because well-run sites declare their sitemap location there
> and that's more reliable than guessing. If that yields nothing, it tries `/sitemap.xml`,
> `/sitemap_index.xml`, `/sitemap.xml.gz` (a compressed one). Sitemaps can be indexes-of-indexes,
> so it recurses — but **only 2 levels deep and only 8 sub-sitemaps**, because some sites publish
> thousands and you only need a rough total. Body capped at 5 MB, results capped at 500 URLs.
>
> Total time: 1–3 seconds, all plain HTTP. No browser involved.
>
> **The clever bit:** every single failure path returns "no sitemap found" rather than raising an
> error. A broken sitemap, a timeout, malformed XML — all become "proceed with the default cap".
> A probe can never block a scrape. This pattern is called **fail-open** and you'll see it
> everywhere in this codebase.

> **In the code:** `backend/app/services/sitemap.py`, entry point `probe_sitemap`.
> Frontend decision at `App.tsx:183` — more than 20 URLs opens the scope modal.

## Phase 2 — Why the crawl is a "job" and not just a request

> **Plain version.** Crawling 20 pages takes 1–2 minutes. If the browser just sat there waiting for
> a single reply that long, you'd get no progress bar, no cancel button, and a decent chance that
> something in between (a proxy, a load balancer, the browser itself) gives up and kills the
> connection.
>
> So instead: the browser asks the server to *start* a crawl. The server immediately replies with a
> ticket number. The browser then asks "how's ticket #abc123 doing?" once per second and draws a
> progress bar from the answer.

> **The detail.** Three endpoints:
> ```
> POST /api/scrape/start     → {"job_id": "abc123"}     returns instantly
> GET  /api/scrape/jobs/abc123 → {status, progress, result}   polled every 1s, 10 min ceiling
> DELETE /api/scrape/jobs/abc123                              cleanup
> ```
> The job's state lives in a small SQLite database file (SQLite = a database that's just a file on
> disk, no server needed). The actual crawl runs in the background and writes progress into that row
> after every page.
>
> **The clever bit:** the background wrapper (`crawl_orchestrator.py`) is written so it can *never*
> throw an error upward. Background tasks that crash silently are a classic source of "the app just
> hangs forever". Every failure path here writes the error onto the job row instead, so the frontend
> can display it. It also passes the crawl a "should I stop?" function that gets checked between
> pages — that's how the Cancel button works.

> **In the code:** `backend/app/routers/scrape.py:184`, `backend/app/services/crawl_orchestrator.py`,
> `backend/app/services/crawl_jobs.py`. Frontend polling loop at `App.tsx:199-248`.

## Phase 3 — The survey (the crawl itself)

This is the biggest and most interesting part of the system. `scraper.py` is 2,964 lines.

### 3.1 The safety guard (SSRF)

> **Plain version.** You type a URL and the server fetches it. That sounds harmless — but the server
> lives *inside* your network. If someone typed `http://192.168.1.50/admin`, the server would happily
> fetch an internal page that the outside world can't reach, and show it to them. That attack has a
> name: **SSRF** (Server-Side Request Forgery).
>
> So before fetching anything, the code resolves the address and refuses if it points anywhere
> internal.

> **The detail.** `assert_public_url` checks: the scheme is http/https; the hostname isn't on a
> denylist (`host.docker.internal`, `metadata.google.internal` — cloud metadata services are a
> classic target); then it does a DNS lookup and rejects if **any** returned IP is private, loopback,
> link-local, reserved, multicast or unspecified.
>
> It runs at every fetch boundary — the router, the crawl entry, each rendered page, each image
> fetch, the sitemap probe. The fast-fetch path checks the URL both **before and after** following
> redirects, because a public URL can redirect to an internal one.
>
> **Known gap, documented in the file:** a redirect chain that never returns isn't caught. That's
> the accepted residual risk.

> **In the code:** `backend/app/services/url_guard.py`. Read the module docstring — it's a good
> summary of the trust model.

### 3.2 Being polite

> **Plain version.** If you hammer a website with 20 simultaneous requests, its firewall will
> conclude you're an attack and block you. So the crawler queues itself: at most 4 requests to one
> site at a time, at least 200 milliseconds between them, and if 5 requests fail in a row it gives up
> on that site entirely rather than making things worse.
>
> It also reads `robots.txt`, the file where site owners state what automated visitors may access,
> and honours it.

> **The detail.** `HostPoliteness` is a small object per hostname holding a semaphore (a counter that
> caps concurrency at 4), a "last request at" timestamp guarded by a lock (so two parallel workers
> can't both think they were the most recent request), and a consecutive-failure counter.
> `robots.txt`'s `Crawl-delay` directive overrides the 200ms floor if it's larger. Results are cached
> for 10 minutes per host.
>
> ⚠️ **Known bug:** once the 5-failure circuit opens, nothing ever closes it. The registry is shared
> across the whole server process and keyed by hostname, so one bad crawl blocks that site until you
> restart the backend.

> **In the code:** `backend/app/services/polite.py`. Robots handling at `scraper.py:133`.

### 3.3 Reading a page — the two-extractor merge

> **Plain version.** Getting the *text* off a page sounds trivial. It isn't, because a page is full
> of text you don't want: navigation menus, cookie banners, footer links.
>
> There's a well-known library, `trafilatura`, that's excellent at finding "the article" on a page.
> But marketing pages don't have an article. They have a hero, three feature boxes, some
> testimonials, a call to action — and trafilatura throws most of that away as "boilerplate",
> including all the headings.
>
> So the code runs **two** extractors and merges them, rather than picking a winner.

> **The detail.**
> - `_structural_text` walks every block-level tag in document order and dedupes. High recall,
>   includes headings, carries some menu noise.
> - `trafilatura.extract(favor_recall=True, include_tables=True)` — high precision, misses sections.
>
> The structural pass is the **spine** (it preserves document order and headings). Any trafilatura
> block not already present is appended. Neither side's content is lost. A flat `get_text()` is the
> last resort if both come back empty.
>
> If the entry page yields under **80 characters**, the scrape fails with a helpful message: it's
> probably a JavaScript loading state, a paywall, or something needing login.

> **In the code:** `scraper._extract_body_text` (scraper.py:2092).

### 3.4 The measuring tape — this is the cleverest part of the system

> **Plain version.** Here is the core problem. You have twenty photos on a page. Which one is the
> hero banner? Which are staff headshots in a grid? Which is a decorative divider? Which is the logo?
>
> The HTML often doesn't say. A logo, a headshot and a hero photo are all just `<img>` tags.
>
> But your *eyes* can tell instantly, because you can see how big each one is, where it sits, and
> whether it's one of six identical squares in a row.
>
> So: while the page is open in the real browser, the code runs a small program **inside the page**
> that measures every photo and writes the measurements back into the HTML as invisible notes. Then
> the ordinary text-based parser can read those notes and know what your eyes would have known.

> **The detail.** `_stamp_render_evidence` executes JavaScript in the live page. Every `<img>` gets
> an attribute `data-webtree-evidence` containing:
>
> ```json
> {"nw": 1920, "nh": 1080,        // the real file's dimensions
>  "x": 0, "y": 64,               // where it sits on the page
>  "w": 1366, "h": 720,           // how big it's actually drawn
>  "vw": 1366, "vh": 900,         // the viewport size, for comparison
>  "grid": 0}                     // how many similar-sized siblings it has
> ```
>
> Every box ≥200×120px with a CSS background image also gets its resolved URL plus a note including
> `text`: **how many characters are rendered on top of it**.
>
> `gridCount` is the trick that identifies staff grids. For a given photo, walk up to 4 ancestor
> boxes; at each level, count sibling cells that each hold exactly one image of *similar area*
> (between 0.4× and 2.5× this one). **3 or more similar siblings ⇒ this is one tile of a grid.**
>
> Then `image_evidence.classify_role` turns geometry into a role, cheapest disqualifier first:
>
> | Test | Verdict |
> |---|---|
> | drawn smaller than 96×72 | `decoration` (icon, badge, tracking pixel) |
> | aspect ratio ≥ 4.5 and under 140px tall | `decoration` (divider strip, logo marquee) |
> | it's a CSS background with ≥24 characters of text over it | `background` |
> | starts in the top 60% of the screen **and** covers ≥18% of it (or spans ≥80% width and ≥35% height) | `hero` |
> | 3+ grid siblings, roughly square (0.6–1.6), ≤420px wide | `portrait` |
> | 3+ grid siblings otherwise | `gallery` |
> | area ≥ 160×120 | `content` |
> | anything else | `decoration` |
>
> **Why this earns its complexity.** That `portrait` role travels all the way through the system.
> It's why the AI is forbidden from putting a headshot on a services card. It's why a committee
> member's face never becomes a full-screen hero background. Without it, the single most common way a
> generated page looks wrong would happen constantly.
>
> The httpx fast path leaves no measurements, so `parse_evidence` returns nothing and the scraper
> falls back to older heuristics based on tag order and filenames. Quality degrades; nothing breaks.

> **In the code:** `scraper._stamp_render_evidence` (scraper.py:233) writes the notes;
> `backend/app/services/image_evidence.py` reads them. The thresholds above are all named constants
> at the top of that file.

### 3.5 Finding the staff — structure, not keywords

> **Plain version.** To build a Team section you need names, roles, bios and photos. The page just
> shows a grid of cards. Nothing is labelled "this is a person's name".
>
> The code works backwards from the photo: find a portrait-shaped picture, then look around it for
> text that looks like a person's name.

> **The detail.** For each `<img>`:
> 1. Skip icons, logos, anything under 80px, anything not portrait-shaped (aspect 0.5–1.6).
> 2. **Walk up** the DOM looking for a container whose CSS class or ID contains `team`, `member`,
>    `profile`, `staff`, `committee`, `board`, `trustee`… Stop at `nav`, `footer`, `header`, `aside`,
>    `form` — and at Divi-style page-builder footers, which are plain `<div>`s with `footer` in the class.
> 3. **If that finds nothing, look sideways.** Some page builders put the photo in one column and the
>    text in the *sibling* column, which an upward walk can never see. The code comment notes that
>    omitting this once cost an entire team grid its photos.
> 4. Test the candidate name against layered denylists:
>    - exact matches: "our team", "meet the team", "good food", "latest events"
>    - **opening words a real name never has**: `our`, `the`, `meet`, `why`, `getting`, `building`,
>      `good`, `latest`, `upcoming`… (this catches the long tail: "Our Community Programmes",
>      "Meet Your Dentists", "Building Futures")
>    - every token must be capitalised — **except** name particles: `bin`, `binti`, `van`, `der`,
>      `de`, `al`… so "Siti binti Rahman" and "Jan van der Berg" survive
> 5. Role must be ≤8 words and must not open with a pronoun (`He`, `She`, `They`) — "He leads our
>    outreach team" is a bio sentence, not a job title.
> 6. Grab the card's own email/phone/social links from its anchors.
> 7. Record **where this person's own page lives** (the card's link). This becomes the site's
>    roster→detail index and is used later to build the page hierarchy correctly.

> **In the code:** `scraper._extract_profile_candidates` (scraper.py:1616) and its ~15 helpers
> above it. The denylists are constants at scraper.py:481-545.

### 3.6 The crawl queue — three lanes, not one

> **Plain version.** You have budget for 20 pages. Which 20?
>
> A naive crawler takes links in the order it finds them. That's bad, for two specific reasons this
> codebase hit in practice:
>
> - **Bilingual sites.** A Malaysian site with `/about` and `/bm/about` would spend half its budget
>   crawling translations of pages it already has.
> - **Committee pages.** A committee page links to nine member profiles. Those nine pages are exactly
>   what a Team section needs — but they appear low in the HTML, so nav and footer links crowd them out.
>
> So there are three lanes.

> **The detail.**
>
> | Lane | Contents | Drained |
> |---|---|---|
> | `priority` | links from profile/roster cards | **first** |
> | `frontier` | normal discovered links | second |
> | `deferred` | translated mirrors of pages already known | only when nothing else is queued *or in flight* |
>
> A "mirror" is detected by evidence, not guesswork: `/bm/about` only counts as a translation if
> `/about` is also in the crawl. A page like `/it/support` on a site with no `/support` stays an
> ordinary page (`it` could be Italian — or it could be "IT department").
>
> The lane is stored *with* each queued item, so it also controls the **order of the results**, not
> just fetch order. Downstream code reads earlier pages as closer to the entry, and a translation of
> `/about` must never outrank `/about` just because the header listed it first.
>
> Also here: URLs are normalised (fragment stripped, trailing slash, lowercased host); non-page file
> types are excluded (`.pdf`, `.zip`, images, fonts, `.css`); junk paths are skipped (`/wp-admin`,
> `/cart`, `/login`, `/tag/`, `/author/`, `/page/`); the queue itself is capped at `max_pages × 3`
> so a link-farm page can't exhaust memory.
>
> Workers pull from the queue as they free up (a "sliding window"), rather than working in fixed
> batches of 3 — with batches, one slow Playwright fallback stalled two finished slots every round.
>
> Whatever's still queued when the budget runs out is returned as `unvisited_urls`. That's what
> powers the **"Crawl N more"** button, which resumes without re-rendering the entry page.

> **In the code:** `scraper._crawl_extra_pages` (scraper.py:2435). The comment block at lines
> 2503-2520 explains the three lanes in the authors' own words.

### 3.7 Cleaning up after the crawl

> **Plain version.** Every page of a site repeats the same menu strip. When you extract text page by
> page, you can't tell whether a row of links is a navigation menu or genuine content — until you've
> seen all the pages and noticed it's identical on every one.
>
> So after the crawl finishes, the code goes back over all the text and deletes anything that turned
> out to be repeated furniture.

> **The detail.** Each link cluster gets a fingerprint (`cluster_href_key` = a hash of its sorted
> link addresses). A fingerprint seen on 2 or more pages is template chrome. `strip_chrome_lines`
> then removes text lines that **exactly** match one of those link labels — whole-line matches only,
> so a paragraph that happens to mention "Services" survives.
>
> A related trick: `find_linkbar_cluster` looks for the *opposite* — a cluster that appears on **only**
> the entry page, has 2–6 links, and carries a leading label like "Current Releases:". That's an
> announcement strap, and it's genuine content. It gets stripped from the text too, but only so it
> can be **re-inserted as a proper linkbar section** later. Breadcrumbs are explicitly excluded
> (a strap almost never links back to the homepage).

> **In the code:** `backend/app/services/nav_extraction.py` — `find_repeated_cluster_keys`,
> `strip_chrome_lines`, `find_linkbar_cluster`, `strip_linkbar_lines`.

## Phase 4 — Your review

> **Plain version.** You now see the extracted text and can edit it. Nothing AI-related has happened
> yet. This split exists deliberately — the AI work is the expensive part, and you should see what
> it's going to be fed before you pay for it.

> **The detail.** One consequence worth knowing: the brand-detection cache is keyed on a fingerprint
> of the full text. **Editing the text here automatically invalidates that cache**, so your edits are
> genuinely used rather than silently ignored in favour of a cached earlier reading.

> **In the code:** `App.tsx:388`, `planner._source_fingerprint` (planner.py:161).

## Phase 5 — Planning the structure

Two very different things happen here.

### 5.1 "What is this business?" — AI call #1

> **Plain version.** The AI reads the content once and answers six questions: what's the business
> called, what's the tagline, what does it do in one sentence, what mood is the brand, what industry
> is it, and what's its brand colour.

> **The detail.** It runs on the *reasoning* model (GLM), not the content model, and the text is
> capped — large PDFs were causing timeouts on this first, cold call.
>
> **The clever bit: the answer is never trusted blindly.** If the model invents a mood ("vibrant")
> that isn't one of the six allowed, a validator quietly coerces it back to a valid value. If it
> can't be salvaged, the mood is derived from the industry instead of falling back to a blanket
> "modern". *A wrong adjective must never fail the whole detection.* This defensive-validator pattern
> repeats everywhere the LLM's output enters the system.
>
> Cached for 5 minutes, and passed forward explicitly from the page picker to the generate call so
> it's never paid for twice.

> **In the code:** `planner.detect_brand` / `detect_brand_cached` (planner.py:134-197). The prompt is
> `DETECT_BRAND_PROMPT` in `prompts.py:278`.

### 5.2 "Which pages should exist?" — no AI at all

> **Plain version.** This surprises people. Deciding the site's page structure is done entirely by
> ordinary code reading URLs, page titles and the original site's own menu. It's one of the largest
> files in the project (1,319 lines) and it never asks the AI anything.
>
> Why? Because the evidence is already there. The site owner already decided what pages they have and
> how they nest — it's in their menu. Asking an AI to guess would be *worse* than reading it.

> **The detail.** Several layers:
>
> **Page type from the URL.** A table of hints: a slug containing `contact`/`get-in-touch` → contact
> page; `team`/`committee`/`board`/`directory` → team page; `blog`/`news`/`insights` → blog. Unmatched
> top-level slugs default to `services`; unmatched sub-pages default to `landing`.
>
> ⚠️ This matching is by substring and `work` is tested before `process`, so `/how-we-work` is
> classified as a portfolio page. Genuine bug, listed at the end of this document.
>
> **Sections from the page type.** A home page gets hero → features → testimonials → cta. A team page
> gets hero → team → cta. A blog page gets only a hero, because the actual article list is inserted
> later as a dynamic CMS element rather than AI-written content.
>
> **Then four content-driven overrides:**
>
> 1. **Directory detection.** A page with **6 or more** scraped profile cards is a people directory,
>    whatever its URL says. Sites genuinely do park "Find a Music Therapist" at `/contact` — so
>    `contact` is one of the types that gets re-classified as `team`. `about` deliberately isn't
>    (a team grid inside a real About narrative is normal, and the About recipe already has a team section).
>
> 2. **Story pages.** Count how many of the page's *headings* have a content photo sitting under them.
>    **3 or more** ⇒ this page narrates section by section (a kindergarten's "School Life" page), so
>    throw away the fixed rhythm and use `hero + one image-and-text section per source section + cta`,
>    each bound to its real photo. This is the difference between preserving a page and flattening it
>    into generic card grids.
>
> 3. **Photo weaving.** Every eligible page gets **at least one** image-and-text split as a baseline —
>    standard landing-page practice is that walls of text need breaking up. Image-rich pages get two.
>
> 4. **Content signals.** Regex patterns scan the page's own text. "founded in", "since 1998",
>    "milestones" → add a timeline section. "award-winning", "certified", "accredited" → awards.
>    `\d+%`, "over 200", "years of experience" → stats. "our branches", "visit us at" → locations.
>    Each is gated by page type (a restaurant's About only gets an awards section if the text actually
>    claims an award), capped at 2 extras, and the whole page is capped at 9 sections.
>
> **Hierarchy from three sources, in priority order:**
>
> - **Roster links (strongest).** A page that a committee grid links to belongs under that committee —
>   not under a `/profile` section invented from its URL. This requires the roster to have **2 or more**
>   cards, because a *single* card on a person's own page links **back** to the roster, and reading
>   that backwards would file the whole committee under "Ashley Jinivon".
> - **Menu dropdowns.** If the header nests "Web Design" under "Services", so does the generated site —
>   even when the URLs are flat (`/web-design`, not `/services/web-design`).
> - **Repeated in-body menu strips.** Only counted as real hierarchy when the strip repeats across
>   pages, is **not** just a copy of the header menu, and is **self-referencing** (a page carrying the
>   strip is itself one of its targets). That combination is the signature of a template's section
>   menu. If the parent is ambiguous, the code refuses to guess.
>
> **Template-title disambiguation.** Some templates stamp one `<title>` on a whole family of pages —
> MMTA's nine committee-member pages are all titled "About MMTA". A title carried by 2+ pages names the
> *template*, not the page, so those pages are renamed: if the page has exactly one profile card, use
> that person's name; otherwise use the first heading it doesn't share with its siblings.

> **In the code:** `backend/app/services/page_inference.py`, entry point `infer_page_scaffolds`
> (page_inference.py:832). Thresholds are named constants: `DIRECTORY_MIN_PROFILES = 6`,
> `ROSTER_MIN_PROFILES = 2`, `_STORY_MIN_SECTIONS = 3`, `_MAX_PAGE_SECTIONS = 9`.

## Phase 6 — Writing the copy (AI call #3, the expensive one)

### 6.1 The instruction sheet

> **Plain version.** The prompt is essentially the product specification, written in English for the
> model. It's worth reading even if you never touch the code — `backend/app/services/prompts.py`.

> **The detail.** Its most important section is headed *"FIDELITY RULES — these override every other
> instruction"*, and it lists what may never be invented: testimonial quotes, reviewer names,
> statistics, prices, awards, certifications, team members, addresses, phone numbers, opening hours,
> FAQ specifics, menu items, founding dates.
>
> Then the rule that makes the whole system honest:
>
> > *If the source contains NO facts to ground a requested section, OMIT that section entirely — a
> > shorter honest page beats a padded one.*
>
> Four sections are exempt (hero, about, contact, cta) because they assert no facts.
>
> Other notable instructions:
> - **Photo binding.** The page's own photos are offered as a numbered list with their measured roles.
>   Two hard bans, both derived from Phase 3.4: never bind a `background`-role photo to a content
>   section (it's a backdrop, not a picture of the subject), and never bind a `portrait` to anything
>   but a team member or a testimonial author — described in the prompt as *"the single most common
>   way a generated page looks wrong"*.
> - **Photo search phrases** default to *people doing the thing*, with an explicit exception for when
>   the subject genuinely is a thing (a dish, a product, a room, a portfolio piece). Bare mood words
>   like "modern" or "abstract" are banned.
> - **Negative words are banned outright** in any photo query — "stressed", "empty", "alone", "sad",
>   "bored". A downbeat stock photo is a brand risk regardless of industry.
> - Write in the source's language.
>
> **The clever bit:** the instruction sheet is assembled *per batch* from only the section types that
> batch needs. If a batch has no pricing section, the pricing schema line is never sent. That's ~35%
> fewer tokens per call, which directly reduces the chance of the reply being cut off.

### 6.2 Grounding each page in its own source

> **Plain version.** The original bug this fixed: the AI was writing the /services page from the
> homepage's text, because the homepage's text was the only thing being sent.
>
> Now each planned page is matched to the specific crawled page whose content should feed it.

> **The detail.** Matching, in priority order: exact URL match → trailing segment match
> (`services/web-design` → `/web-design`) → keyword match on path/title/headings → fall back to the
> entry page (so a value **always** exists).
>
> Special case: FAQ content is frequently split across `/faq` **and** `/support`, so *every*
> keyword-matching page is merged into the FAQ page's source rather than just the first.

### 6.3 Fitting the work into the model's memory

> **Plain version.** A language model can only hold so much text at once. Too much and the reply gets
> cut off mid-sentence, wasting the whole call. So pages are packed into batches that fit.

> **The detail.** Four ceilings, any of which seals the current batch:
> 1. input budget — 48% of the context window
> 2. output budget — 52% of it, estimated at ~230 tokens per section
> 3. **section density** — max 6 sections per batch. Not a memory limit, a *quality* limit: past ~10
>    sections the model starts thinning every block out.
> 4. absolute page cap — max 4 pages
>
> The fixed overhead is **measured**, not guessed: the code builds the real prompt once with an empty
> page list and counts its characters. An unusually long brand summary would otherwise silently
> overflow the budget. The instruction sheet is re-measured for each candidate batch, because adding
> a page with a new section type *grows the prompt*.
>
> Pages too big for one call are split — either by text (a long page becomes several chunks) or by
> sections (a 9-section page becomes several small calls). Section groups are each **anchored by the
> hero**, repeated deliberately: the merge keeps only the first copy, but every group needs the page's
> thesis in front of it or its body sections drift off-topic.
>
> Pages are processed parents-first, and each parent's hero headline is passed down to its children so
> a sub-page echoes its parent's voice instead of restating what the company does.
>
> **Merging chunks back together** is careful: list sections (FAQ, services, team) *union* their items
> across chunks so a capped list is drawn from the whole page, not just its first chunk. Single
> sections (hero, about) take chunk 0, which is the top of the page. Duplicate detection is per type —
> and timelines use **all** their key fields, because two milestones can genuinely share the title
> "Expansion" in different years. Timelines are sorted chronologically *before* the cap truncates,
> otherwise a long history keeps whichever chunk happened to arrive first.

> **In the code:** `backend/app/services/planner.py` — `_build_batches` (399),
> `plan_site_with_scaffolds` (961), `_merge_page_plans` (752). Source routing is
> `backend/app/services/source_router.py`.

### 6.4 When the model misbehaves

> **Plain version.** Local models are less reliable than cloud ones. Four layers of recovery.

> **The detail.**
> 1. **Empty reply** (cold model load, or a turn spent entirely on internal reasoning) → retry once.
> 2. **Reply cut off by length** → **double the output budget and retry**, up to 131,072 tokens.
>    Deliberately *not* treated as a formatting error — the "please fix this" prompt is longer than the
>    original and would just truncate again.
> 3. **Reply doesn't match the required shape** → one repair attempt, with the specific validation
>    errors fed back to the model.
> 4. **One page is broken beyond repair** → drop just that page so the rest parses. It gets rebuilt
>    from its blueprint anyway in the next phase.

> **In the code:** `backend/app/services/llm.py:99-223`.

## Phase 7 — The fact-check

> **Plain version.** The prompt *told* the model not to invent things. This phase *verifies* it.

> **The detail.** `align_page_to_scaffold` walks the required section list in order. For each one:
> take the model's matching block if there is one, else inject a placeholder **only** if it's a
> fact-free structural type (hero/about/cta/contact), else **omit it**. Extra blocks the model
> invented are dropped. A page that ends up completely empty keeps a hero built from its real title.
>
> Then the grounding check. For every testimonial, award, client, statistic and milestone, the item's
> text is searched for in that page's actual scraped text — exact match, or (for longer strings) a
> long contiguous fuzzy match ≥60% of its length. Tolerant of whitespace and smart-quote noise;
> **intolerant of paraphrase**, because the prompt says quotes must be preserved verbatim. Items with
> no match are deleted. If nothing survives, the whole section goes.
>
> This catches what a placeholder denylist can't: a fluent, plausible, entirely fabricated
> testimonial from a person who doesn't exist.
>
> **The clever bit — the team check is deliberately asymmetric.** A bad *name* means the "member"
> isn't a person at all → delete the entry. A bad *role* or *bio* is untrustworthy text attached to a
> real person → **blank that field** and let the card render with what it can stand behind. Both
> fields are optional in the template, so the card still looks right.

> **In the code:** `backend/app/services/scaffold_enforcement.py`. `is_grounded_in_source` at line 298.

## Phase 8 — Building the actual page

Everything from here is deterministic. This is `schema_builder.py`.

### 8.1 The design decisions

> **Plain version.** Before drawing anything, the system decides the site's overall look: header
> style, footer style, palette, fonts. Some of that is AI taste, some is a lookup table.

> **The detail.** Header and footer "archetypes" (classic bar, glass-blur, minimal line, centred
> stack, floating pill) come from a **fit table**, not an AI. Four tiers:
> explicit override → industry pin (childcare and nonprofit have researched briefs) → mood fit list →
> seeded rotation → **diversity nudge**.
>
> The reasoning is stated in the file: chrome archetypes are a small closed vocabulary where a fit
> table beats a 7–9B model's judgement. `"classic"` sits in every list — *"never wrong, merely never
> interesting"* — so it anchors the rotation.
>
> **The diversity engine** deserves explaining. Seeded rotation alone makes each *brand* consistent —
> the same client always gets the same header. But an agency generating ten sites in an afternoon
> would see the same header on all ten whenever the fit tables agree: "different colours on the same
> template". So every site's choices are logged to SQLite, and the picker steers away from what this
> site used last time (a *regeneration* should explore) and what the last few sites used (a *batch*
> shouldn't converge). Crucially it only ever chooses among candidates the fit table already
> approved — **if everything's been used recently, the original pick stands. Fit beats novelty.**

### 8.2 Two warm-ups in parallel

> **Plain version.** Two slow things that don't depend on each other are started at the same time:
> pre-fetching stock photos, and asking the AI to pick layout variants.

> **The detail.** `prewarm_stock` expands every image slot on the site into its full search-term
> chain, de-duplicates, and fetches them all concurrently into a shared cache — so the serial render
> loop later hits a warm cache instead of a network round-trip per photo. It's explicitly
> *output-identical*: selection and ordering still happen in the render loop exactly as before.
>
> Meanwhile, AI call #4 picks a layout variant for every section of the whole site in **one** call,
> keyed by (page number, section number). Heroes are excluded — they have their own art director.
> Only sections with 2+ eligible variants are even offered.
>
> **The safety net:** if that call fails or is disabled, it returns an empty result. Every lookup then
> yields nothing and selection falls back to the deterministic mood-ordered choice. Generation
> proceeds exactly as it did before the feature existed.

### 8.3 Picking a layout for each section

> **Plain version.** There's a catalogue of pre-built section layouts — like an IKEA catalogue. Each
> entry declares what it needs ("I need a heading, a list of at least 3 items, and an image"). A
> layout is only usable if the content can actually fill it.

> **The detail.** `select_template` in order:
> 1. Filter to layouts allowed for this brand mood (a template can declare `moods: [playful]`, so
>    playful kindergarten styling never lands on a law firm).
> 2. **Feasibility filter** — every non-optional slot must be fillable. Lists need items and must meet
>    any declared minimum (a bento grid needs enough tiles to read as a bento). Images need a search
>    query or a URL. Text must be non-blank.
> 3. The AI's pick from 8.2 — **only if it's in the feasible set**. A hallucinated or unfit id is
>    silently discarded.
> 4. Otherwise the mood's preferred layout order.
> 5. Otherwise the first candidate.
>
> `pool = feasible or candidates` — if literally nothing is feasible, the section degrades to a
> best-effort layout rather than disappearing.
>
> Then `fill_template` walks the chosen catalogue entry and pours content into it. The catalogue
> supports directives like `$repeat` (clone this card once per item), `$bento` (clone with varied
> grid sizes), `$slot` (put this text here), `$styleSlot` (put this photo in this CSS property).
>
> Two small touches worth knowing:
> - `$gridFit` prefers 3 columns but **drops to 2 when the item count leaves one orphan** (4 items →
>   2×2, 7 → 2+2+2+1). A single card beside a big gap reads as broken.
> - A container whose slots all filled to nothing is **pruned**, cascading up through empty wrappers —
>   otherwise an empty AI-produced item renders as a stray placeholder box.

### 8.4 Choosing photos

> **Plain version.** For each photo slot: use the photo the AI pinned, else the best-matching scraped
> photo, else a stock photo from Pexels, else an on-brand gradient. It never fails.

> **The detail.**
>
> **Ranking a scraped photo** against the requested subject gives it a score out of 1.0:
> - up to 0.6 for word overlap between the request and the photo's alt text, its URL path (CDNs often
>   use readable filenames like `/coffee-beans-roasting.jpg`), its vision-generated caption, and the
>   heading it sat under on the source page
> - up to 0.2 if the photo's role matches the slot's purpose
> - up to 0.2 for size (negative below 200px)
>
> Bands: **≥0.62** confident → use it. **0.30–0.62** ambiguous. **<0.30** → fall through to stock.
> The AI photo judge only fires in the ambiguous band **and** only when 2+ candidates are within 0.10
> of each other — a genuine tie. That keeps it to roughly 0–2 calls per site.
>
> **The pinned-photo screen** is subtle and worth understanding. The AI binds photos *by topic*, from
> a text description — it cannot see the picture. So a promotional banner captioned "our restaurant"
> is exactly the kind of pin that arrives here. Before honouring a pin for a full-screen background,
> the code checks it's big enough, isn't a headshot, is the right shape, and — via OCR — **doesn't
> already have words printed on it**. Two sets of headlines fighting for the same space can't be
> fixed by any amount of dimming, because the problem is the words, not the contrast.
>
> **Stock search** walks a chain of increasingly broad queries. Each batch of ~15 results is: filtered
> for negative-sounding alt text, ranked for relevance, then walked past any photo that OCR says is a
> picture *of text* (budget: 4 OCR checks per slot — rejecting a candidate costs one step down the
> same batch, not another network round-trip). A result is only accepted if it's genuinely relevant to
> either the slot's request *or* the broader chain term that was searched. The chain's final entry is
> deliberately broad and always accepted, so a slot never falls all the way to a gradient.

### 8.5 Colours, contrast and rhythm

> **Plain version.** Fifteen passes run over the assembled page, in a specific order, each fixing one
> visual problem. The order isn't arbitrary — each pass depends on what the previous one did.

> **The detail.** The core one is the **luminance rhythm**: alternate light and dark bands down the
> page so sections read as distinct. But it's not a naive alternation:
>
> - A section carrying its own featured photo is **anchored**: its band is forced to the *opposite* of
>   the photo's own brightness, so the photo pops against its container.
> - Flexible sections alternate *around* those fixed anchors.
> - A flexible section always flips from its left neighbour, so it can never collide. Only two
>   adjacent *anchored* sections can land on the same band — and when that happens, the boundary is
>   flagged and rendered with a slight brightness step plus a hairline border, so the seam stays visible.
> - A section whose content sits in a single inset card is forced **light** — and forced at the *input*
>   stage rather than repainted afterwards, so its neighbours flip around it honestly instead of
>   colliding with it unannounced.
>
> The ordering constraints in the rest of the chain, each documented in a code comment:
> - texture capping runs *after* modernisation creates the textures
> - panel polish runs *after* contrast enforcement turns colour variables into real hex values, because
>   it needs to judge how dark the fill actually is
> - shaped dividers run late, so a seam is drawn against each section's *final* colour
> - photo edge-fades are re-pointed last: a hero's bottom fade was calculated against the theme
>   background before the page existed, so once the real neighbour is known it's either re-aimed or dropped
>
> Then a global safety net: catalogue sections hard-code a single dark text colour, which vanishes on a
> dark band. `enforce_text_contrast` re-targets those to the band's correct foreground, across every
> page plus the header and footer.
>
> And a structural pass: the first section's title becomes the page's single `<h1>`, later ones become
> `<h2>`. Exactly one `<h1>` per page is an SEO requirement.

### 8.6 The audit

> **Plain version.** A checklist runs over the finished site — missing alt text, contrast below
> accessibility standards, tiny fonts, duplicate SEO titles, pages nothing links to. It writes its
> findings to the log and **never blocks anything**.

> **The detail.** The rule is explicit in `CLAUDE.md`: *"The audit is advisory: it logs and never
> blocks generation. If you make it blocking you will fail real sites — raise the generator's output
> quality instead."* Currently the findings only reach the server log, not your screen.

## Phase 9 — Publishing

> **Plain version.** Ten steps, each reported back so the UI can show a progress table: log in,
> optionally create a new site container, check it's empty, tidy up the page addresses, upload all the
> images, create the pages, save the header/footer/menus, save each page's content, apply the theme,
> publish.

> **The detail.** Two decisions worth knowing:
>
> **Image uploads are non-fatal individually.** If one image can't be re-hosted (dead source URL), it's
> **stripped from the schema** rather than left pointing at the original site — so a published site
> never renders a broken image that phones home to the client's old host.
>
> **Address handling depends on whether the site is new.** A brand-new site keeps its hierarchical
> addresses (`/profile/ashley`), so a migrated site publishes at the URLs it already ranks for in
> Google. Pushing over an *existing* site flattens them instead — because renaming already-published
> pages is exactly the SEO damage this is designed to prevent. The check runs *after* the "is this
> site empty?" guard, because only that guard's answer can distinguish the two cases.

> **In the code:** `backend/app/services/push_orchestrator.py`, `_run_push` at line 264.

---

# Part 3 — The five principles behind everything

Once you see these, most of the code stops looking arbitrary.

### 1. Fail open, never fail closed

Every optional thing returns an empty result on failure rather than raising an error. Sitemap probe,
design language, design brain, diversity history, vision judge, the audit. The generator's job is to
produce a site; a broken accessory must degrade the result, never prevent it.

*Where to see it:* every `except` block in `design_brain.py`, `diversity.py`, `sitemap.py`.

### 2. Claim content before the model sees it

Anything that's going to be rendered as a *structure* is deleted from the raw text first — PDF
download cards, repeated menu strips, announcement bars. Otherwise the model reads the same words and
narrates them into a second, disconnected paragraph, and the page says everything twice.

*Where to see it:* `_strip_document_card_lines`, `strip_chrome_lines`, `strip_linkbar_lines`.

### 3. Evidence beats keywords

Almost every classification is made from measured or structural evidence rather than word-matching.
A page is a directory because it has 6 profile *cards*, not because its URL says "team". A photo is a
headshot because it's one of 6 square siblings in a grid, not because its filename says "portrait".
A translated page is a translation because its untranslated counterpart also exists.

*Where to see it:* `image_evidence.py`, `_looks_like_directory_page`, `translation_pairing`.

### 4. The AI proposes, deterministic code disposes

Every AI output passes through a gate that can silently reject it: an invented mood is healed to a
valid one, an unfit layout choice is discarded, an ungrounded testimonial is deleted, a mis-bound
photo is screened. The AI can improve the result; it can never break it.

*Where to see it:* `heal_mood`, `select_template`, `is_grounded_in_source`, the pinned-photo screen.

### 5. Order matters, and the reasons are written down

`plan_to_site`'s fifteen styling passes each carry a comment explaining why they sit where they do.
This is also the system's biggest fragility — the constraints live in prose rather than in code, so
nothing enforces them.

---

# Part 4 — Glossary

| Term | Meaning here |
|---|---|
| **HTML** | The text format a web page is written in |
| **DOM** | The tree of nested boxes a browser builds from HTML |
| **CSS** | The language that says what things look like |
| **Tag / element** | One box in the HTML: `<p>`, `<img>`, `<div>` |
| **Attribute** | Extra info on a tag: `src=`, `alt=`, `class=` |
| **Headless browser** | A real Chrome with no visible window, driven by code |
| **Playwright** | The library that drives that browser |
| **httpx** | A plain HTTP downloader — fast, no JavaScript |
| **trafilatura** | A library that finds "the article" on a page |
| **BeautifulSoup / lxml** | Libraries that let code walk the DOM |
| **Crawl / BFS** | Following links to discover pages, nearest-first |
| **Frontier** | The queue of URLs discovered but not yet visited |
| **robots.txt** | A file where sites declare what bots may access |
| **SSRF** | An attack where you trick a server into fetching internal addresses |
| **JSON** | A text format for structured data — how the AI replies |
| **Pydantic model** | A named data shape that validates itself |
| **Deterministic** | Same input → same output, every time |
| **Idempotent** | Running it twice gives the same result as once |
| **Token** | Roughly ¾ of a word; how AI text length is measured |
| **Context window** | How much text the model can hold at once |
| **num_ctx / LLM_CTX** | The setting for that window size |
| **SQLite** | A database that's just a file — no server |
| **Semaphore** | A counter that caps how many things run at once |
| **Circuit breaker** | Stop trying after N consecutive failures |
| **Fail-open** | On error, degrade rather than block |
| **Scaffold** | The blueprint for one page: type, slug, section list |
| **ContentBlock** | One section's *content* (AI output) |
| **BuilderElement** | One node of the *visual* tree (final output) |
| **Catalog** | The library of pre-built section layouts |
| **Archetype** | A header or footer style family |
| **Mood** | One of six brand personalities: modern, luxury, friendly, technical, editorial, playful |
| **Band** | A section's light-or-dark assignment |
| **Pexels** | The stock photo service used as fallback |
| **OCR** | Reading text out of an image's pixels |
| **Pinned photo** | A specific scraped photo the AI bound to a section |

---

# Part 5 — Reading the code yourself

## Start here, in this order

1. **`CLAUDE.md`** (repo root) — the rules and the hard contracts. 5 minutes.
2. **`backend/app/services/prompts.py`** — the AI's instruction sheet. It's plain English and it's
   effectively the product spec. 10 minutes, no Python needed.
3. **`backend/app/models/content_blocks.py`** — the vocabulary of what a section can be.
4. **`backend/app/routers/generate.py:1152`** — `generate_with_pages`. Read only the comments; they
   narrate the whole pipeline in order.

## How to trace anything

Every module has a docstring at the top — the block in triple quotes explaining what it's for and
why it exists. In this codebase those are unusually good, and they often explain the *bug that
caused the code to be written*. When you want to know why something is the way it is, read the
docstring and the inline comments before the code.

To find where something happens:

```bash
# Search the whole backend for a term
grep -rn "portrait" backend/app/services/

# See what a file contains without reading it all
grep -n "^def \|^class \|^async def " backend/app/services/media.py
```

## The map

| I want to change… | Look in |
|---|---|
| How pages are fetched or text extracted | `services/scraper.py`, `services/fast_fetch.py` |
| How photos are classified | `services/image_evidence.py` |
| How staff are detected | `services/scraper.py:1616` |
| Which pages get created | `services/page_inference.py` |
| What the AI is told | `services/prompts.py` |
| How work is split into AI calls | `services/planner.py` |
| What gets deleted as fabricated | `services/scaffold_enforcement.py` |
| Colours and fonts | `services/theme.py` |
| Header/footer style choice | `services/design_director.py` |
| Hero art direction | `services/hero_director.py` |
| **All layout and styling** | `services/schema_builder.py` |
| Photo selection | `services/media.py`, `services/image_match.py` |
| Publishing to the CMS | `services/push_orchestrator.py` |
| Any setting or threshold | `app/config.py` — **every** knob lives here |

## Running the tests

There are 62 test files. They are the best documentation of intended behaviour, and each one is
readable as a list of "this should happen" statements.

```bash
cd backend && .venv/bin/python -m pytest tests
```

---

# Part 6 — Known issues

These are real, and each has an obvious fix.

**1. `/how-we-work` is classified as a portfolio page.** The URL-to-type table tests `work` before
`process` and matches by substring, so `how-we-work`, `framework`, `teamwork` and `network` all
become portfolio pages. `helpful-resources` becomes an FAQ page via the `help` hint.
*Fix:* match whole words, or move multi-word hints ahead of single-word ones.
*File:* `page_inference.py:41`

**2. The politeness circuit breaker never resets.** After 5 consecutive failures the code stops
crawling that site — and nothing ever un-stops it. The state is shared across the whole server and
keyed by hostname, so one bad crawl blocks that site until the backend restarts.
*Fix:* reset after a cooldown period.
*File:* `polite.py:93`

**3. The sitemap's page list is thrown away.** Phase 1 fetches up to 500 addresses straight from the
site's own index, and only the *count* is used. The crawler then rediscovers everything from scratch
by following links.
*Fix:* seed the crawl queue from the sitemap. This would find pages the link graph hides.

**4. Links are capped at 50 per page before filtering.** On a page with a mega-menu and a footer
sitemap, those 50 are often all navigation, so genuine content links never enter the queue.
*Fix:* filter first, then cap.
*File:* `scraper.py:2170`

**5. One failed AI batch aborts the whole generation.** A timeout on batch 4 of 6 discards five
successful batches. Since a page can already be rebuilt from its blueprint, a failed batch should
degrade to defaults with a warning.
*File:* `generate.py:1291` — probably the highest-value fix on this list.

**6. All caches break with multiple server workers.** Five separate in-memory caches plus the
background crawl task all assume a single process. Jobs survive a restart in the database, but their
tasks don't — rows stay stuck at "running" forever with nothing to clean them up.

**7. Text-length estimates are wrong for Chinese and Japanese.** The batching maths assumes 4
characters per token, which holds for English but is roughly 3× off for CJK — so a Chinese page can
silently overflow the model's memory and waste a retry. This matters given the multilingual Malaysian
sites the tool targets.
*File:* `planner.py:388`

**8. Both hero-variety systems are switched off.** With the current defaults every page of every site
opens with the same full-bleed hero, centre-composed. Both defaults have well-reasoned comments, so
this is a decision rather than an accident — but the consequence is that ~150 lines of per-mood hero
art direction, plus its tests, can never run.
*File:* `config.py:232` and `:244`, `hero_director.py`

**9. The quality audit is invisible.** It runs on every generation and writes one line to the server
log. Surfacing it in the preview would turn a developer log into an operator's checklist.

**10. The synchronous scrape endpoint is dead but still holds the cache.** `/api/scrape/preview` was
the original implementation; the job-based one replaced it. The frontend function that calls it has
zero callers. Its 30-minute cache — described as absorbing double-clicks and back-button traffic — is
therefore unreachable, and the path the UI *does* use has no de-duplication at all. Clicking "Fetch
site" twice on the same URL re-crawls the entire site.
*Fix:* delete the endpoint and move the cache onto the job path.
