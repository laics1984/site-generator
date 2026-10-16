# Shared industry categories

The authored source of category values, display labels and order is:

    webtree-cms-api/resources/data/industries.json

CMS config loads this catalog through benchmarks.industries. App\Support\Industry
remains the common accessor for validation, settings, agents and reporting.
Admin Suite Site settings already reads its choices from the CMS profile API;
Insights now reads the additive benchmark.industryLabel field from that same
catalog. Older cached responses without a label retain the previous slug-format
fallback until refreshed.

The generator uses generated Python and TypeScript vocabulary, following its
existing catalog-vendoring pattern. No runtime CMS connection or new AI request
is required. Page template labels and detection/scaffold/legacy prompt choices
come from the generated Python catalog; the frontend category type is generated
from the same source. Descriptions and industry-specific page recipes remain
generator-owned.

## Updating categories or labels

1. Edit the CMS JSON catalog. Keep stored slugs stable for existing sites.
2. From site-generator/backend, run:

       python3 scripts/sync_industries.py
       python3 scripts/sync_industries.py --check
       .venv/bin/python -m pytest -q tests/test_industry_taxonomy.py

3. Commit the catalog and both generated files together across their owning
   repositories. New categories also require generator page recipes and any
   applicable design behavior; the template-coverage test catches omissions.
4. Deploy the CMS and generator updates together. Use the existing CMS deployment
   config-cache rebuild process after changing the catalog. Generated vocabulary
   is Python code, so the generator's normal app reload/restart picks it up.

The sync check exits nonzero for stale generated files, invalid catalogs or a
missing CMS source. It also accepts --source PATH for release pipelines where
the CMS checkout is elsewhere. The cross-repository test skips its source-file
comparison when CMS is absent; run the explicit sync check with the CMS artifact
as a release gate. Local tests still enforce template labels, category coverage
and all AI prompt lists without a sibling CMS checkout.

The original 13 values and CMS labels are preserved. Generator display labels
now match the CMS exactly, including Retail & e-commerce and SaaS & software.
No migration, new dependency or new endpoint is required. The existing Insights
response gains an optional industryLabel; existing clients ignore it. Public
site responses and host/CDN caching are unchanged. No production data or config
cache was changed by this implementation.

## Validation

- Catalog sync check passed for both generated files.
- Generator taxonomy, mood and childcare tests: 67 passed.
- Full generator suite: 2,793 passed, with the same three pre-existing upload
  fixture/DNS failures documented during palette work.
- CMS industry, Insights endpoint and metrics rollup suites: 22 passed,
  103 assertions. These include acceptance of all 13 category values and the
  canonical SaaS label/null label in actual API responses.
- Generator frontend build and Admin Suite production build passed. Admin's
  build-only command was used because its full project typecheck has known
  baseline errors; no full Admin Suite typecheck or browser visual QA is claimed.

Files for this change are the CMS JSON catalog, benchmarks config, Industry
accessor documentation, MetricsQueryService and its industry/Insights tests;
the generator sync script, generated Python/TypeScript vocabulary,
content_blocks import, template labels, prompts, frontend type re-export,
taxonomy tests and this document; and Admin Suite's Insights model/panel.
Concurrent multilingual changes and prior palette work were preserved.
