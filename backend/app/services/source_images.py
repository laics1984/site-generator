"""Removing the source's own photography from a SourceContent.

The one gate for "stock images only" generation. Every scraped photo reaches
the generated site through some field on `SourceContent`, so clearing those
fields once — here, at the point the source enters generation — starves every
downstream consumer at the same time: the resolver pool (`_image_pool_for`),
the per-page ranking preference (`_page_images_by_slug`), the image list the
LLM is shown (`source_router.promptable_images`, hence `image_refs` has nothing
to bind), and the deterministic photo-wall injector.

That is deliberately the whole mechanism. A flag on `ImageResolver` would only
cover the slots that go through the resolver, and five paths write a source URL
into the tree without ever calling it (see routers/generate.py's injectors and
section_content's `{"src": ...}` values). Cutting supply covers those for free,
and covers the next one somebody writes.

Scope: CONTENT imagery only. Three kinds of image are deliberately left alone
because each depicts one specific artifact rather than decorating a section —
there is no stock substitute for "this document" or "this blog post":

    * the brand logo, which never travels on SourceContent anyway (it arrives on
      the request as BrandIdentity — see services/logo_extraction.py),
    * `document_cards[].image_url`, the thumbnail of a specific PDF,
    * migrated blog/event post images, which services/content_collections.py
      re-fetches from the article page itself and never reads from here.

`profile_candidates` is left alone for a different and non-obvious reason — see
the note in `without_source_imagery`.
"""

from __future__ import annotations

from app.models.content_blocks import SectionCandidate, SourceContent


def _section_without_images(section: SectionCandidate) -> SectionCandidate:
    """A section candidate stripped of its card photography.

    Both fields feed `routers/generate._inject_image_walls`, which reads
    `section.image_urls` FIRST and only falls back to the image_metadata scan.
    Clearing `image_metadata` alone would leave the photo walls standing.
    `cards[].image_url` also drives the `has_image` hint in the planner prompt
    (services/source_router._section_prompt_entry).
    """
    return section.model_copy(
        update={
            "image_urls": [],
            "cards": [
                card.model_copy(update={"image_url": None}) for card in section.cards
            ],
        }
    )


def without_source_imagery(source: SourceContent) -> SourceContent:
    """A copy of `source` carrying none of the site's own photography.

    Recurses into `discovered_pages`; the original is never mutated (pydantic's
    `model_copy` is shallow, so every nested model that changes is rebuilt).

    Does NOT touch `profile_candidates[].photo_url`, and that is load-bearing
    rather than an oversight: `routers/generate._scraped_team_members` and
    `_directory_roster_members` both skip a candidate with no photo outright
    (`if not profile.photo_url: continue`). A photo-less candidate is not a
    photo-less member — it is not a member at all, so cutting the portrait here
    would empty the roster, leave no deterministic team block, and hand the page
    to `_drop_hollow_team_pages`, which deletes it. Portraits are cut one layer
    later, on the finished plan, by `routers/generate._drop_person_photos`.
    """
    return source.model_copy(
        update={
            "images": [],
            "image_metadata": [],
            "section_candidates": [
                _section_without_images(section)
                for section in source.section_candidates
            ],
            "discovered_pages": [
                without_source_imagery(page) for page in source.discovered_pages
            ],
        }
    )
