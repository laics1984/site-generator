# Website generation guidance

The eight website/copy briefs are applied through existing generation stages.
There is no new model stage, conflict review screen, or approval step. Existing
source facts, SEO, language, industry direction, imagery controls, navigation,
schemas and staged generation remain supported.

| Brief | Owner |
| --- | --- |
| Premium site builder | Shared copy rules in `prompts.py`; curated choices in `design_brain.py` |
| First impression | Hero guidance and the existing hero director; dedicated landing heroes keep a conversion action |
| Conversion landing page | Landing copy guidance and `landing_order.py` |
| Portfolio | Work-page guidance; stock stays illustrative, never client-work evidence |
| Service page | Service/pricing guidance; real prices, units, conditions and destinations |
| About story | About guidance; person-specific bios retain their source fidelity |
| Mobile optimizer | Concise copy, existing responsive/image/motion behavior, shared button/wrapping enforcement and static audit |
| Website copy system | Shared voice, purposeful sections and consistent actions; testimonial questions never become public reviews |

## Fixed priorities

Facts and valid output win over persuasion; accessibility/mobile usability and
performance win over decoration; brand/industry character and source meaning
remain intact. Missing proof is omitted. Uncertain histories, identities, claims,
prices and guarantees are never invented. Optional imagery can be omitted when
text/icons work better; required image slots retain their fallback queries.

## Landing-page ordering exception

Before copy generation, a deterministic rule may select problem → solution →
proof → CTA. URL, DOCX and pasted inputs keep the original `page_type=landing`
exception when all of the following hold:

- There are 3–12 independent, substantive source sections, with recognizable
  problem, solution and proof headings. Each has at least 40 characters of prose
  or card body content; headings alone are not enough. This checks available
  evidence, not whether a source claim was independently verified.
- Every group has an unambiguous journey role. Unknown sections, instructional
  steps, histories, people and document groups retain the original order.
- The selected scaffold maps one-to-one to these groups, with a hero and a
  closing CTA/contact. A generated contact form or an evidenced contact/booking
  destination must be available.
- The source order has an inversion of the journey stages. Already-ordered
  sections are retained. Same-role sections preserve their relative order.

The original group-to-block mapping moves with each group. Cards, qualifiers,
images/ref ordering, raw source text, page identity and navigation are unchanged.
Prompt projection and post-generation alignment receive the same selected order.

**Facebook Pages and PDF documents also qualify on homepages.** They do not need
an existing `section_candidates` tree or a recipe that already matches one:

- `landing_source.py` reads exact extracted headings and explicit text labels
  from the existing input. Facebook's Products and services / What customers say
  labels can support solution / proof. It never infers a missing problem from
  general body keywords. At least 3 and at most 12 substantive journey groups
  must support problem, solution and proof; the 40-character evidence floor and
  sequence/history safeguards remain.
- Explicit email, labelled phone and action URLs can support the closing CTA
  even when the PDF reader supplied no link metadata. A selected contact form
  also qualifies. Missing proof or a usable action retains the selected recipe.
- A PDF homepage may summarize facts from that document's discovered pages
  when its entry text alone is incomplete. The private grounding projection
  includes those facts; the original source and discovered pages remain intact.
  Other source types never use this document-wide fallback.
- Source evidence is bound to individual recipe occurrences before copy is
  written. Needed stage slots can be added; existing selected gallery, location,
  contact and other slots remain in their relative order before the closing CTA.
  Same-role groups remain stable. Card contents and qualifiers travel together.
- The homepage keeps `page_type=home`, its root URL and navigation identity.
  Facebook's final fact-authority pass still owns real reviews and contact data.
  Services/about pages, legal pages, record sets and translations retain their
  existing behavior. The extension applies only to Facebook and PDF inputs.

The planner receives bounded `conversion_journey` evidence in the existing call.
Its positional bindings override the old section order and remain correctly
scoped when a page is generated in section batches. This is private planning
state on `PageScaffold`, omitted from API/picker serialization and generated site
schemas. There is no frontend review, decision screen or extra model stage.
Unrecognized or incomplete evidence retains the selected recipe. This is a
conservative clarity heuristic, not a measured conversion-rate prediction.

## Calls, budgets and cache

`generation_guidance.py` selects only applicable directives from `prompts.py`.
Normal, source-text multipass and section multipass use the same prompt builder.
The legacy free-form path reuses the same text in its existing call.

Batch budgeting measures the actual serialized per-page input: guidance, source
text, cards, images and links. Shared prompt wording is consolidated to avoid
duplicated rules. Context limits can still require more batches; there is no
claim of unchanged latency or guaranteed 30-minute completion.

Existing validated-response cache keys include complete prompts, so these changes
naturally invalidate old content responses. No new cache, public cache policy,
endpoint, content-block field, database migration or frontend control is added.
Remaining-page generation applies the rules to pending pages and retains the
existing site's frozen theme and already-generated pages.

## Concrete rendering behavior

Catalog and fallback styled buttons get mobile wrapping, a 100% width cap and
at least a 44px minimum height when their authored height is numerically known.
Larger targets and tablet rules are preserved. Ordinary inline navigation links
keep their layout. Text gains a wrapping fallback for long unbroken content.
The static audit checks declared mobile target sizes, fixed widths and font sizes;
it is advisory and never introduces a review gate.

Contact destinations use request/enquiry labels. A guessed booking/payment path
that resolves to contact cannot keep a direct booking/payment promise. Evidenced
external links remain intact. The footer CTA banner can reuse the homepage's
closing copy when it addresses the same contact destination. Existing curated
effects, reduced-motion handling, media optimization and image priorities remain
owned by their current renderers/resolvers.

Tests cover source-preserving ordering/fallback, repeated section alignment,
guidance in all existing call paths, token budgeting, mobile overrides, hero and
footer action behavior, plus existing preview/extension and industry safeguards.
These checks do not establish conversion gains or real-browser loading metrics.

## Changed files

- `backend/app/services/prompts.py`: shared and page-specific copy instructions;
  preserve existing factual, language, SEO, imagery and output requirements.
- `backend/app/services/generation_guidance.py` (new): select relevant directives.
- `backend/app/services/planner.py`: existing-call integration and measured input
  budgets; optional reuse of prepared source routing.
- `backend/app/services/landing_order.py` (new): automatic landing-order exception.
- `backend/app/services/landing_source.py` (new): Facebook/PDF text evidence,
  document-wide homepage grounding and explicit action destinations.
- `backend/app/models/industry.py`: private, typed evidence-to-section bindings;
  public scaffold fields remain unchanged.
- `backend/app/services/page_inference.py`: expose the existing group-to-kind
  mapping for reuse without a second implementation.
- `backend/app/services/source_router.py`: real source links and shared bounded
  section-evidence serialization; `source_images.py` updates its documentation
  reference to that shared serializer.
- `backend/app/routers/generate.py`: share selected scaffolds/source projection
  between copy generation and grounding.
- `backend/app/services/design_brain.py`: reinforce premium, content-fit choices.
- `backend/app/services/schema_builder.py`: mobile usability, landing hero actions,
  honest contact labels and footer-copy reuse.
- `backend/app/services/section_content.py`: dedicated conversion-hero policy.
- `backend/app/services/header_footer.py`: optional sourced CTA headline.
- `backend/app/services/ux_audit.py`: advisory mobile checks.
- `backend/tests/test_generation_guidance.py` (new).
- `backend/tests/test_landing_order.py` (new).
- `backend/tests/test_source_conversion_journey.py` (new): actual Facebook/PDF
  ingestion, source-type boundaries, protected/incomplete flows, source fidelity
  and repeated section batches.
- `backend/tests/test_mobile_generation_rules.py` (new).
- `docs/website-generation-guidance.md` (new): behavior, boundaries and validation.

## Validation recorded during implementation

- Focused generation, landing ordering, mobile, batching, concurrency, grounding,
  preview/extension, stock and childcare checks: 87 passed.
- Full backend suite: 2,324 passed, 5 failed. All five failures reproduced in an
  isolated replay of the pre-edit service files: preview/public style-copy parity,
  CMS target field expectations, existing Facebook pixel-cache classification,
  and two DNS-dependent document-upload checks.
- Frontend production build and `git diff --check`: passed.
- Shared scaffold prompt head: 11,698 → 10,668 characters (about 9% shorter);
  selected page directives add a bounded per-page input cost.
- No live model-output, CMS publication or browser-performance claim is made.
  This update affects subsequent generation; existing published sites are not
  rewritten by editing the generator.

## Facebook/PDF extension validation

- Final focused checks: 165 passed. They include the actual Facebook mapper,
  an in-memory PDF through the real parser and document splitter, repeated-kind
  alignment and section batches, source-kind boundaries, incomplete/protected
  inputs, source immutability and bounded action extraction. Wrapped PDF body
  lines cannot become fabricated section headings; contact metadata cannot pad
  out otherwise thin proof.
- Full backend run: 2,354 passed, 5 failed. The same five failures were reproduced
  against isolated pre-extension files, with the style drift check reading the
  same actual preview/public files. The final additional boundary/resource
  regressions are included in the 165-check focused run.
- `git diff --check` passed. The shared scaffold prompt head remains unchanged
  from the initial implementation; extra journey guidance/evidence is included
  only for qualifying Facebook/PDF pages and is covered by existing input-budget
  measurement.
- No HTTP endpoint, public scaffold/site field, migration, public cache policy
  or frontend control changed. Existing prompt-cache keys include the scoped
  evidence/guidance. No live model generation, Facebook fetch or CMS publication
  was performed for this extension.
