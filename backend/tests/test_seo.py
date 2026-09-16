"""Tests for SEO structured data generation and audit."""

import pathlib
import re
import unittest

from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.services.seo import (
    breadcrumb_slug_chain,
    build_structured_data,
    detect_duplicate_seo,
    detect_orphan_pages,
    extract_og_image,
)
from app.services.schema_builder import (
    _HEADING_BOX_RESET,
    _TITLE_NAMES,
    apply_heading_levels,
)
from app.models.content_blocks import PagePlan, clamp_seo_description, clamp_seo_title
from app.services.template_filler import load_catalog
from app.services.ux_audit import audit_seo


# -- helpers ------------------------------------------------------------------

def _el(name, typ="container", children=None, **styles):
    content = children if children is not None else []
    return BuilderElement(name=name, type=typ, styles=styles, content=content)


def _img(name, src="https://example.com/photo.jpg", alt="photo"):
    return BuilderElement(
        name=name, type="image",
        content=BuilderElementContent(src=src, alt=alt),
    )


def _text(name, inner, **styles):
    return BuilderElement(
        name=name, type="text", styles=styles,
        content=BuilderElementContent(innerText=inner),
    )


def _heading(name, inner, tag):
    el = _text(name, inner)
    el.htmlTag = tag
    return el


def _page(slug, *, title="Page", seo=None, elements=None, is_homepage=False):
    return GeneratedPage(
        slug=slug,
        title=title,
        is_homepage=is_homepage,
        body_schema=BodySchema(elements=elements or []),
        seo=seo or PageSeo(),
    )


def _site(pages, header=None, footer=None):
    return GeneratedSite(
        site_name="Test Site",
        pages=pages,
        header_schema=header,
        footer_schema=footer,
    )


def _rules(findings):
    return {f.rule for f in findings}


# -- extract_og_image ---------------------------------------------------------

class OgImageExtractionTest(unittest.TestCase):
    def test_background_hero(self):
        hero = _el(
            "Hero", typ="section",
            backgroundImage="linear-gradient(rgba(0,0,0,.5),rgba(0,0,0,.5)),url('https://img.example.com/hero.jpg')",
        )
        result = extract_og_image([hero])
        self.assertEqual(result, "https://img.example.com/hero.jpg")

    def test_split_hero_image(self):
        hero_img = _img("Hero Image", src="https://img.example.com/split.jpg")
        hero = _el("Hero", typ="section", children=[hero_img])
        result = extract_og_image([hero])
        self.assertEqual(result, "https://img.example.com/split.jpg")

    def test_fallback_to_first_image(self):
        section = _el("About", typ="section", children=[
            _img("About Photo", src="https://img.example.com/about.jpg"),
        ])
        result = extract_og_image([section])
        self.assertEqual(result, "https://img.example.com/about.jpg")

    def test_no_images_returns_none(self):
        section = _el("About", typ="section", children=[
            _text("H1", "Hello"),
        ])
        self.assertIsNone(extract_og_image([section]))

    def test_skips_data_uri(self):
        img = BuilderElement(
            name="Placeholder", type="image",
            content=BuilderElementContent(src="data:image/svg+xml;base64,abc", alt=""),
        )
        self.assertIsNone(extract_og_image([img]))


# -- breadcrumb_slug_chain ----------------------------------------------------

class BreadcrumbChainTest(unittest.TestCase):
    def test_homepage(self):
        chain = breadcrumb_slug_chain("", {})
        self.assertEqual(chain, [("", "Home")])

    def test_single_level(self):
        chain = breadcrumb_slug_chain("about", {"about": "About Us"})
        self.assertEqual(chain, [("", "Home"), ("about", "About Us")])

    def test_nested(self):
        titles = {"services": "Services", "services/web-design": "Web Design"}
        chain = breadcrumb_slug_chain("services/web-design", titles)
        self.assertEqual(chain, [
            ("", "Home"),
            ("services", "Services"),
            ("services/web-design", "Web Design"),
        ])

    def test_unknown_slug_humanized(self):
        chain = breadcrumb_slug_chain("our-team", {})
        self.assertEqual(chain[1], ("our-team", "Our Team"))


# -- build_structured_data ---------------------------------------------------

class _FakeFaqBlock:
    kind = "faq"
    items = []

    def __init__(self, items):
        self.items = items


class _FakeFaqItem:
    def __init__(self, q, a):
        self.question = q
        self.answer = a


class StructuredDataTest(unittest.TestCase):
    def test_homepage_gets_organization_and_website(self):
        result = build_structured_data(
            page_slug="",
            page_title="Home",
            page_description="Welcome",
            page_type="home",
            is_homepage=True,
            site_name="Acme",
            brand_name="Acme Inc",
            logo_url="https://acme.com/logo.png",
            industry_category="saas",
            contact={"email": "hello@acme.com", "phone": "+1234567890"},
            blocks=[],
            breadcrumb_slugs=[("", "Home")],
        )
        self.assertIsNotNone(result)
        types = [s["@type"] for s in result]
        self.assertIn("Organization", types)
        self.assertIn("WebSite", types)

    def test_local_business_for_restaurant(self):
        result = build_structured_data(
            page_slug="",
            page_title="Home",
            page_description=None,
            page_type="home",
            is_homepage=True,
            site_name="Cafe",
            brand_name="Cafe Latte",
            logo_url=None,
            industry_category="restaurant",
            contact={"address": "123 Main St"},
            blocks=[],
            breadcrumb_slugs=[("", "Home")],
        )
        types = [s["@type"] for s in result]
        self.assertIn("LocalBusiness", types)

    def test_subpage_gets_breadcrumb_list(self):
        result = build_structured_data(
            page_slug="services",
            page_title="Services",
            page_description="Our services",
            page_type="services",
            is_homepage=False,
            site_name="Acme",
            brand_name="Acme",
            logo_url=None,
            industry_category=None,
            contact=None,
            blocks=[],
            breadcrumb_slugs=[("", "Home"), ("services", "Services")],
        )
        types = [s["@type"] for s in result]
        self.assertIn("BreadcrumbList", types)
        bc = next(s for s in result if s["@type"] == "BreadcrumbList")
        self.assertEqual(len(bc["itemListElement"]), 2)

    def test_faq_block_produces_faqpage(self):
        faq = _FakeFaqBlock([
            _FakeFaqItem("What is X?", "X is a thing."),
            _FakeFaqItem("How much?", "$100"),
        ])
        result = build_structured_data(
            page_slug="faq",
            page_title="FAQ",
            page_description="Common questions",
            page_type="faq",
            is_homepage=False,
            site_name="Acme",
            brand_name="Acme",
            logo_url=None,
            industry_category=None,
            contact=None,
            blocks=[faq],
            breadcrumb_slugs=[("", "Home"), ("faq", "FAQ")],
        )
        types = [s["@type"] for s in result]
        self.assertIn("FAQPage", types)
        faq_schema = next(s for s in result if s["@type"] == "FAQPage")
        self.assertEqual(len(faq_schema["mainEntity"]), 2)

    def test_no_structured_data_returns_none(self):
        result = build_structured_data(
            page_slug="about",
            page_title="About",
            page_description="About us",
            page_type="about",
            is_homepage=False,
            site_name="Acme",
            brand_name="Acme",
            logo_url=None,
            industry_category=None,
            contact=None,
            blocks=[],
            breadcrumb_slugs=[("", "Home")],
        )
        self.assertIsNone(result)


# -- detect_duplicate_seo ----------------------------------------------------

class DuplicateSeoTest(unittest.TestCase):
    def test_duplicate_titles_detected(self):
        pages = [
            _page("a", seo=PageSeo(title="Same Title")),
            _page("b", seo=PageSeo(title="Same Title")),
        ]
        dupes = detect_duplicate_seo(_site(pages))
        self.assertTrue(any(f == "seo_title" for _, _, f in dupes))

    def test_unique_titles_clean(self):
        pages = [
            _page("a", seo=PageSeo(title="Title A")),
            _page("b", seo=PageSeo(title="Title B")),
        ]
        dupes = detect_duplicate_seo(_site(pages))
        self.assertEqual(dupes, [])


# -- detect_orphan_pages -----------------------------------------------------

class OrphanPageTest(unittest.TestCase):
    def test_linked_page_not_orphan(self):
        link = BuilderElement(
            name="Nav link", type="link",
            content=BuilderElementContent(innerText="About", href="/about"),
        )
        pages = [
            _page("", is_homepage=True, elements=[link]),
            _page("about"),
        ]
        orphans = detect_orphan_pages(_site(pages))
        self.assertNotIn("about", orphans)

    def test_unlinked_page_is_orphan(self):
        pages = [
            _page("", is_homepage=True),
            _page("hidden"),
        ]
        orphans = detect_orphan_pages(_site(pages))
        self.assertIn("hidden", orphans)


# -- audit_seo ---------------------------------------------------------------

class SeoAuditTest(unittest.TestCase):
    def test_short_title_flagged(self):
        pages = [_page("", seo=PageSeo(title="Hi"))]
        findings = audit_seo(_site(pages))
        self.assertIn("seo-title-length", _rules(findings))

    def test_long_title_flagged(self):
        pages = [_page("", seo=PageSeo(title="A" * 70))]
        findings = audit_seo(_site(pages))
        self.assertIn("seo-title-length", _rules(findings))

    def test_good_title_clean(self):
        pages = [_page("", seo=PageSeo(title="A" * 55))]
        findings = audit_seo(_site(pages))
        self.assertNotIn("seo-title-length", _rules(findings))

    def test_duplicate_titles_flagged(self):
        pages = [
            _page("a", seo=PageSeo(title="Same Title Here For Both")),
            _page("b", seo=PageSeo(title="Same Title Here For Both")),
        ]
        findings = audit_seo(_site(pages))
        self.assertIn("seo-title-unique", _rules(findings))

    def test_missing_og_image_flagged(self):
        pages = [_page("", seo=PageSeo(title="Test", ogImage=None))]
        findings = audit_seo(_site(pages))
        self.assertIn("og-image-missing", _rules(findings))

    def test_og_image_present_clean(self):
        pages = [_page("", seo=PageSeo(title="Test", ogImage="https://x.com/img.jpg"))]
        findings = audit_seo(_site(pages))
        self.assertNotIn("og-image-missing", _rules(findings))

    def test_cta_missing_flagged(self):
        section = _el("About", children=[_text("H1", "About Us")])
        pages = [_page("", elements=[section])]
        findings = audit_seo(_site(pages))
        self.assertIn("cta-missing", _rules(findings))

    def test_cta_present_clean(self):
        cta = _el("CTA", typ="section", children=[_text("H2", "Get Started")])
        pages = [_page("", elements=[cta])]
        findings = audit_seo(_site(pages))
        self.assertNotIn("cta-missing", _rules(findings))

    def test_heading_hierarchy_no_h1_flagged(self):
        section = _el("Content", children=[_heading("Heading", "Subtitle", "h2")])
        pages = [_page("about", elements=[section])]
        findings = audit_seo(_site(pages))
        self.assertIn("heading-hierarchy", _rules(findings))

    def test_heading_hierarchy_one_h1_clean(self):
        section = _el("Content", children=[_heading("Heading", "Title", "h1")])
        pages = [_page("about", elements=[section])]
        findings = audit_seo(_site(pages))
        self.assertNotIn("heading-hierarchy", _rules(findings))

    def test_heading_hierarchy_two_h1s_flagged(self):
        # The audit read element NAMES, so it could see neither a real h1 nor a
        # second one: every page reported "no H1 found" whatever it shipped.
        section = _el("Content", children=[_heading("Heading", "Title", "h1"),
                                           _heading("Heading", "Also title", "H1")])
        pages = [_page("about", elements=[section])]
        findings = audit_seo(_site(pages))
        self.assertIn("heading-hierarchy", _rules(findings))

    def test_orphan_page_flagged(self):
        pages = [
            _page("", is_homepage=True),
            _page("orphan-page"),
        ]
        findings = audit_seo(_site(pages))
        self.assertIn("orphan-page", _rules(findings))


class HeadingLevelTest(unittest.TestCase):
    """`apply_heading_levels`: one <h1> per page, on the right node."""

    def _section(self, name, *, title_name="Heading", title="Section title"):
        return _el(name, children=[_text("Eyebrow", "Eyebrow"),
                                   _text(title_name, title)])

    def test_first_section_title_is_the_only_h1(self):
        sections = [self._section("Hero", title="Bread baked the slow way"),
                    self._section("Features", title="What we do"),
                    self._section("CTA", title="Ready to get started?")]
        apply_heading_levels(sections)
        tags = [t.htmlTag for s in sections for t in s.content if t.name == "Heading"]
        self.assertEqual(tags, ["h1", "h2", "h2"])

    def test_profile_name_is_a_title(self):
        # A profile section titles itself with the person's name; before it
        # counted, the page's h1 was whatever section came next (the CTA).
        sections = [self._section("Profile", title_name="Name", title="Jane Doe"),
                    self._section("CTA", title="Ready to get started?")]
        apply_heading_levels(sections)
        self.assertEqual(sections[0].content[1].htmlTag, "h1")
        self.assertEqual(sections[1].content[1].htmlTag, "h2")

    def test_split_headline_puts_the_whole_title_in_the_h1(self):
        # Two text elements, one heading: the <h1> is the group, so the accent
        # line is inside it. Tagging the lead alone published half a title.
        group = _el("Heading group", children=[_text("Heading", "Bread baked"),
                                               _text("Heading accent", "the slow way")])
        hero = _el("Hero", children=[_text("Eyebrow", "Since 1998"), group])
        apply_heading_levels([hero])
        self.assertEqual(group.htmlTag, "h1")
        self.assertEqual([line.htmlTag for line in group.content], ["span", "span"])
        # The tag must not bring the UA heading box with it.
        self.assertEqual(group.styles["margin"], "0")
        self.assertEqual(group.styles["fontSize"], "inherit")
        # Nor may inline spans collapse the two lines onto one.
        self.assertEqual([line.styles["display"] for line in group.content],
                         ["block", "block"])

    def test_split_headline_lines_keep_their_own_display(self):
        lead = _text("Heading", "Bread baked")
        lead.styles = {"display": "inline-block"}
        group = _el("Heading group", children=[lead, _text("Heading accent", "slow")])
        apply_heading_levels([group])
        self.assertEqual(lead.styles["display"], "inline-block")

    def test_titleless_section_is_skipped_not_substituted(self):
        sections = [_el("Logo wall", children=[_img("Logo")]),
                    self._section("About", title="Our story")]
        apply_heading_levels(sections)
        self.assertEqual(sections[1].content[1].htmlTag, "h1")


class HeadingVocabularyTest(unittest.TestCase):
    """`_TITLE_NAMES` against the vendored catalog: a name set is a silent off
    switch the moment a section titles itself with a name nobody added."""

    # The one body section that genuinely has no title: an editorial testimonial
    # is an eyebrow and a pull quote, and promoting a quote to a heading would
    # be worse than the page's h1 landing on the next section.
    TITLELESS = {"testimonials-editorial"}
    # Slots a section uses to declare "this node is my title".
    TITLE_SLOTS = {"heading", "headline", "title", "name"}

    @staticmethod
    def _first_titled(node, in_repeat=False):
        """(node, in_repeat) for the first node `_TITLE_NAMES` would pick."""
        if node.get("type") == "text" and node.get("name") in _TITLE_NAMES:
            return node, in_repeat
        content = node.get("content")
        if isinstance(content, list):
            repeat = in_repeat or bool(node.get("$repeat"))
            for child in content:
                if isinstance(child, dict):
                    found = HeadingVocabularyTest._first_titled(child, repeat)
                    if found:
                        return found
        return None

    def _body_sections(self):
        return [s for s in load_catalog()["sections"]
                if s["sectionType"] not in ("header", "footer")]

    def test_every_body_section_can_present_its_title(self):
        missing = [s["id"] for s in self._body_sections()
                   if self._first_titled(s["tree"]) is None
                   and s["id"] not in self.TITLELESS]
        self.assertEqual(missing, [], f"sections that can host no <h1>: {missing}")

    def test_the_node_picked_is_the_section_title(self):
        wrong = []
        for section in self._body_sections():
            found = self._first_titled(section["tree"])
            if found is None:
                continue
            node, in_repeat = found
            if in_repeat or node.get("$slot") not in self.TITLE_SLOTS:
                wrong.append((section["id"], node.get("name"), node.get("$slot")))
        self.assertEqual(wrong, [], f"not a section title: {wrong}")

    def test_titleless_allowlist_stays_honest(self):
        stale = [sid for sid in self.TITLELESS
                 if self._first_titled(
                     next(s for s in self._body_sections() if s["id"] == sid)["tree"]
                 ) is not None]
        self.assertEqual(stale, [], f"no longer titleless: {stale}")


class HeadingMirrorTest(unittest.TestCase):
    """`builder/src/lib/heading-levels.ts` is a hand-written mirror of the pass
    above: the generator levels a page once, the builder re-levels it as the
    user edits. A vocabulary that drifts is a silent off switch on one side
    only — the editor would tag a title the generator doesn't, or leave one it
    does. Skipped rather than failed when the sibling repo is absent, the same
    idiom as `test_self_ink` and the section-catalog parity check."""

    @property
    def _source(self) -> str:
        here = pathlib.Path(__file__).resolve()
        if len(here.parents) <= 3:  # running from a container copy, no siblings
            self.skipTest("builder/ not checked out beside this repo")
        mirror = here.parents[3] / "builder" / "src" / "lib" / "heading-levels.ts"
        if not mirror.exists():
            self.skipTest(f"{mirror} not checked out beside this repo")
        return mirror.read_text()

    def test_title_names_match(self):
        block = re.search(
            r"TITLE_NAMES:\s*ReadonlySet<string>\s*=\s*new Set\(\[(.*?)\]\)",
            self._source,
            re.S,
        )
        self.assertIsNotNone(block, "TITLE_NAMES not found in the mirror")
        self.assertEqual(set(re.findall(r"'([^']+)'", block.group(1))), set(_TITLE_NAMES))

    def test_group_names_are_derived_not_restated(self):
        # A second literal list would be a third place to forget.
        self.assertRegex(
            self._source,
            r"TITLE_GROUP_NAMES[^=]*=\s*new Set\(\s*\[\.\.\.TITLE_NAMES\]"
            r"\.map\(\(name\) => `\$\{name\} group`\)",
        )

    def test_heading_box_reset_matches(self):
        block = re.search(r"HEADING_BOX_RESET[^=]*=\s*\{(.*?)\}", self._source, re.S)
        self.assertIsNotNone(block, "HEADING_BOX_RESET not found in the mirror")
        declared = dict(re.findall(r"(\w+):\s*'([^']+)'", block.group(1)))
        self.assertEqual(declared, _HEADING_BOX_RESET)


class SeoDescriptionBudgetTest(unittest.TestCase):
    """`clamp_seo_description`: no page ships a snippet the SERP would cut.

    The LLM is asked for 140-160 chars and mostly complies; the pages where it
    does not were reaching the CMS at 170+ and lighting up its SEO audit
    ("snippets get cut around 160") on almost every page of a fresh site.
    """

    LONG = (
        "Artisan sourdough baked overnight in a stone oven, delivered to "
        "Melbourne cafes every morning, plus baking classes, event catering "
        "and a weekend market stall in Fitzroy North"
    )

    def setUp(self):
        from app.config import settings

        self.budget = settings.seo_description_max_length
        self.floor = settings.seo_description_min_length

    def test_a_description_within_budget_is_untouched(self):
        text = "Stone-milled sourdough, baked overnight and delivered before six."
        self.assertEqual(clamp_seo_description(text), text)

    def test_it_collapses_whitespace(self):
        self.assertEqual(clamp_seo_description("  Baked\n  fresh\tdaily. "), "Baked fresh daily.")

    def test_it_trims_a_long_description_to_the_budget(self):
        self.assertGreater(len(self.LONG), self.budget)
        trimmed = clamp_seo_description(self.LONG)

        self.assertLessEqual(len(trimmed), self.budget)
        self.assertGreaterEqual(len(trimmed), self.floor)
        self.assertTrue(self.LONG.startswith(trimmed.rstrip(",;:")))

    def test_it_never_cuts_mid_word_or_leaves_dangling_punctuation(self):
        trimmed = clamp_seo_description(self.LONG)

        self.assertNotEqual(trimmed[-1], ",")
        self.assertFalse(trimmed.endswith("..."))
        self.assertFalse(trimmed.endswith("…"))
        # The cut landed on a word the source also ends there.
        self.assertIn(trimmed.split()[-1], self.LONG.split())

    def test_it_prefers_ending_on_a_full_stop(self):
        text = (
            "Stone-milled sourdough baked overnight in a wood oven and delivered "
            "to cafes across the inner north before six every morning. "
            "We also run weekend baking classes and cater events."
        )
        trimmed = clamp_seo_description(text)

        self.assertTrue(trimmed.endswith("morning."), trimmed)
        self.assertLessEqual(len(trimmed), self.budget)

    def test_a_sentence_end_too_early_is_ignored_for_a_word_cut(self):
        # A full stop at char 12 would leave a uselessly short description, so
        # the word-boundary cut wins instead.
        text = "Baked daily. " + "sourdough loaves and pastries " * 8
        trimmed = clamp_seo_description(text)

        self.assertGreaterEqual(len(trimmed), self.floor)
        self.assertLessEqual(len(trimmed), self.budget)

    def test_the_page_plan_applies_the_budget(self):
        plan = PagePlan(
            page_type="landing",
            slug="about",
            title="About",
            blocks=[],
            seo_title="About us",
            seo_description=self.LONG,
        )

        self.assertLessEqual(len(plan.seo_description), self.budget)
        self.assertEqual(plan.seo_description, clamp_seo_description(self.LONG))


class SeoTitleBudgetTest(unittest.TestCase):
    """`clamp_seo_title`: no page ships a title Google would truncate.

    60 chars is ~600px of rendered title — where Google cuts — and the number
    the CMS audit flags against. The generator used to tolerate 65, so a title
    could pass its own audit and fail the dashboard's.
    """

    def setUp(self):
        from app.config import settings

        self.budget = settings.seo_title_max_length

    def test_a_title_within_budget_is_untouched(self):
        title = "Sourdough Baked Overnight in Fitzroy North"
        self.assertEqual(clamp_seo_title(title), title)

    def test_it_trims_a_long_title_on_a_word_boundary(self):
        title = "Stone Milled Sourdough Baked Overnight and Delivered Before Six Every Morning"
        trimmed = clamp_seo_title(title)

        self.assertLessEqual(len(trimmed), self.budget)
        self.assertIn(trimmed.split()[-1], title.split())
        self.assertTrue(title.startswith(trimmed))

    def test_a_brand_suffix_that_no_longer_fits_is_dropped_whole(self):
        title = "Stone Milled Sourdough Baked Overnight and Delivered | Loaves & Fishes"
        trimmed = clamp_seo_title(title)

        self.assertLessEqual(len(trimmed), self.budget)
        self.assertFalse(trimmed.rstrip().endswith("|"), trimmed)
        self.assertNotIn("Loaves", trimmed)

    def test_it_keeps_a_word_that_fits_exactly(self):
        # The budget landing on a space must not cost the word before it.
        title = "Family Dentistry in Petaling Jaya | Checkups, Braces, Whitening"
        trimmed = clamp_seo_title(title)

        self.assertLessEqual(len(trimmed), self.budget)
        self.assertTrue(trimmed.endswith("Braces"), trimmed)

    def test_it_never_leaves_a_dangling_separator(self):
        title = "Stone Milled Sourdough Baked Overnight in Fitzroy North | Loaves"
        trimmed = clamp_seo_title(title)

        self.assertFalse(trimmed.rstrip().endswith("|"), trimmed)
        self.assertFalse(trimmed.rstrip().endswith("-"), trimmed)

    def test_the_page_plan_applies_the_budget(self):
        long_title = "Stone Milled Sourdough Baked Overnight and Delivered Before Six Daily"
        plan = PagePlan(
            page_type="landing",
            slug="about",
            title="About",
            blocks=[],
            seo_title=long_title,
            seo_description="Baked overnight in a stone oven and delivered to cafes across the inner north before six every morning.",
        )

        self.assertEqual(plan.seo_title, clamp_seo_title(long_title))
        self.assertLessEqual(len(plan.seo_title), self.budget)
