import unittest

from app.models.content_blocks import CtaBlock, HeroBlock
from app.services.section_content import block_to_section
from app.services.template_filler import get_template


def _find_node(node, name):
    if node.get("name") == name:
        return node
    content = node.get("content")
    if isinstance(content, list):
        for child in content:
            found = _find_node(child, name)
            if found is not None:
                return found
    return None


class SectionContentTemplateSelectionTest(unittest.TestCase):
    def test_hero_with_image_query_prefers_image_template_before_mood(self):
        block = HeroBlock(
            headline="Luxury care in Kuala Lumpur",
            image_query="Malaysian clinic team",
            layout="split",
        )

        template, _content = block_to_section(block, mood="luxury")

        self.assertIn(template["id"], {"hero-modern-split", "hero-background-bold", "hero-editorial"})
        self.assertTrue(any(slot["id"] == "image" for slot in template["slots"]))

    def test_full_bleed_hero_height_is_tokenized_with_full_screen_fallback(self):
        # Hero height is now driven by the --builder-hero-min-height token so the
        # builder can change it globally; the fallback keeps today's full-screen look.
        bold = get_template("hero-background-bold")
        self.assertEqual(
            bold["tree"]["styles"]["minHeight"],
            "var(--builder-hero-min-height, min(100dvh, 900px))",
        )

    def test_cta_with_background_query_prefers_photo_background_before_mood(self):
        block = CtaBlock(
            headline="Book your appointment",
            cta_label="Book now",
            cta_href="#contact",
            background_query="Malaysian clinic interior",
        )

        template, _content = block_to_section(block, mood="luxury")

        self.assertEqual(template["id"], "cta-background")

    def test_team_grid_profile_photo_uses_polished_portrait_treatment(self):
        template = get_template("team-grid")
        self.assertIsNotNone(template)

        photo = _find_node(template["tree"], "Member Photo")
        self.assertIsNotNone(photo)
        styles = photo["styles"]

        self.assertEqual(styles["width"], "150px")
        self.assertEqual(styles["height"], "150px")
        self.assertEqual(styles["borderRadius"], "9999px")
        self.assertEqual(styles["overflow"], "hidden")
        self.assertEqual(styles["aspectRatio"], "1 / 1")
        self.assertIn("boxShadow", styles)
        self.assertIn("border", styles)

    def test_team_grid_name_and_role_have_clear_profile_hierarchy(self):
        template = get_template("team-grid")
        self.assertIsNotNone(template)

        name = _find_node(template["tree"], "Member Name")
        role = _find_node(template["tree"], "Member Role")
        bio = _find_node(template["tree"], "Member Bio")
        self.assertIsNotNone(name)
        self.assertIsNotNone(role)
        self.assertIsNotNone(bio)
        self.assertTrue(
            any(slot["id"] == "bio" for slot in template["slots"][3]["item"])
        )

        self.assertEqual(name["styles"]["fontSize"], "20px")
        self.assertEqual(name["styles"]["lineHeight"], "1.25")
        self.assertEqual(role["styles"]["letterSpacing"], "0.08em")
        self.assertEqual(role["styles"]["textTransform"], "uppercase")
        self.assertEqual(bio["styles"]["lineHeight"], "1.65")

    def test_team_grid_bio_is_clamped(self):
        # Long bios are truncated so cards stay even: an inline line-clamp is the
        # static fallback; the `wt-clamp` class is the hook the frontend uses to
        # add a show-more toggle.
        template = get_template("team-grid")
        bio = _find_node(template["tree"], "Member Bio")
        self.assertIn("wt-clamp", bio.get("classes", ""))
        self.assertEqual(bio["styles"]["WebkitLineClamp"], "4")
        self.assertEqual(bio["styles"]["overflow"], "hidden")


class AboutZigzagTest(unittest.TestCase):
    """Consecutive about split sections alternate their image side: the pass
    reverses the 2Col children of every second split (structural — no builder
    template change needed)."""

    @staticmethod
    def _about_split_element():
        import asyncio

        from app.models.content_blocks import AboutBlock
        from app.services.section_content import block_to_section
        from app.services.template_filler import fill_template

        block = AboutBlock(
            heading="4 years old", body="Learning domains…",
            image_query="kindergarten classroom",
        )
        template, content = block_to_section(block, mood="friendly")
        assert template["id"] in {"about-image-split", "about-editorial-split"}, template["id"]

        async def resolve(_query):
            return "https://x/photo.jpg", None

        return asyncio.run(
            fill_template(template, content, resolve_image=resolve)
        )

    @staticmethod
    def _column_names(section):
        from app.services.section_content import _split_columns_row

        row = _split_columns_row(section)
        return [child.name for child in row.content]

    def test_every_second_about_split_flips_its_columns(self):
        from app.services.section_content import apply_about_zigzag

        sections = [self._about_split_element() for _ in range(3)]
        baseline = self._column_names(sections[0])

        apply_about_zigzag(sections)

        self.assertEqual(self._column_names(sections[0]), baseline)
        self.assertEqual(self._column_names(sections[1]), list(reversed(baseline)))
        self.assertEqual(self._column_names(sections[2]), baseline)

    def test_non_about_sections_are_ignored(self):
        from app.models.builder_schema import BuilderElement
        from app.services.section_content import apply_about_zigzag

        hero = BuilderElement(name="Hero - Modern Split", type="container", content=[])
        about = self._about_split_element()
        baseline = self._column_names(about)

        apply_about_zigzag([hero, about])  # hero doesn't count or flip

        self.assertEqual(self._column_names(about), baseline)


class BoundImageFeasibilityTest(unittest.TestCase):
    """A block whose photo was ref-bound (image_url, no image_query) must still
    select an image-bearing template."""

    def test_about_with_only_bound_url_selects_split_template(self):
        from app.models.content_blocks import AboutBlock
        from app.services.section_content import block_to_section

        block = AboutBlock(
            heading="Sports Day", body="…", image_url="https://x/sports.jpg"
        )
        template, content = block_to_section(block, mood="friendly")
        self.assertIn(template["id"], {"about-image-split", "about-editorial-split"})
        self.assertIsNotNone(content["image"])

    def test_cta_with_bound_url_selects_background_template(self):
        from app.services.section_content import block_to_section

        block = CtaBlock(headline="Enroll now", image_url="https://x/kids.jpg")
        template, _content = block_to_section(block, mood="friendly")
        self.assertEqual(template["id"], "cta-background")


class ImageCardTemplatesTest(unittest.TestCase):
    """Per-card photos for services/features: when every item carries an image
    value (ref-bound scraped photo or stock query), selection prefers the
    photo-topped card grid added to the shared catalog."""

    @staticmethod
    def _services_block(with_images=True, bound_first=False):
        from app.models.content_blocks import ServiceItem, ServicesBlock

        items = []
        for i, name in enumerate(("Kindergarten", "Nursery", "Childcare")):
            kwargs = {"title": name, "description": f"{name} description."}
            if with_images:
                kwargs["image_query"] = f"{name.lower()} classroom"
                if bound_first and i == 0:
                    kwargs["image_url"] = "https://x/kindy.jpg"
                    kwargs["image_alt"] = "Kindergarten room"
            items.append(ServiceItem(**kwargs))
        return ServicesBlock(heading="Our Services", items=items)

    def test_services_with_item_images_select_image_cards(self):
        from app.services.section_content import block_to_section

        template, content = block_to_section(self._services_block(), mood="friendly")
        self.assertEqual(template["id"], "services-image-cards")
        self.assertTrue(all(i["image"] for i in content["items"]))

    def test_bound_item_photo_fills_src_directly(self):
        from app.services.section_content import block_to_section

        _template, content = block_to_section(
            self._services_block(bound_first=True), mood="friendly"
        )
        first = content["items"][0]["image"]
        self.assertEqual(first["src"], "https://x/kindy.jpg")
        self.assertEqual(first["alt"], "Kindergarten room")
        # Unbound items stay query-shaped for the resolver.
        self.assertIn("query", content["items"][1]["image"])

    def test_query_less_items_backfill_from_titles_and_still_get_image_cards(self):
        # The LLM forgot every image_query → each card falls back to its title
        # as the stock search, so the photo-topped grid still wins (site
        # policy: cards always lead with an image, stock when necessary).
        from app.services.section_content import block_to_section

        template, content = block_to_section(
            self._services_block(with_images=False), mood="friendly"
        )
        self.assertEqual(template["id"], "services-image-cards")
        self.assertEqual(content["items"][0]["image"]["query"], "Kindergarten")

    def test_image_cards_beat_an_explicit_text_grid_pick(self):
        # The design-brain may explicitly pick the text-only grid; the
        # photo-card policy overrides it when every card carries an image.
        from app.services.section_content import block_to_section

        template, _content = block_to_section(
            self._services_block(), mood="friendly",
            explicit_id="services-offer-grid",
        )
        self.assertEqual(template["id"], "services-image-cards")

    def test_features_with_item_images_select_image_cards(self):
        from app.models.content_blocks import FeatureItem, FeaturesBlock
        from app.services.section_content import block_to_section

        block = FeaturesBlock(
            heading="Centres",
            items=[
                FeatureItem(
                    title=t, description="d", image_query=f"{t.lower()} activity"
                )
                for t in ("Science", "ICT", "Art")
            ],
        )
        template, _content = block_to_section(block, mood="friendly")
        self.assertEqual(template["id"], "features-image-cards")

    def test_image_cards_template_fills_end_to_end(self):
        import asyncio

        from app.services.section_content import block_to_section
        from app.services.template_filler import fill_template

        template, content = block_to_section(
            self._services_block(bound_first=True), mood="friendly"
        )

        async def resolve(query):
            return f"https://stock.example/{query.replace(' ', '-')}.jpg", None

        element = asyncio.run(fill_template(template, content, resolve_image=resolve))

        def image_srcs(node, out):
            c = node.content
            if isinstance(c, list):
                for ch in c:
                    image_srcs(ch, out)
            elif node.type == "image" and getattr(c, "src", None):
                out.append(c.src)
            return out

        srcs = image_srcs(element, [])
        self.assertEqual(len(srcs), 3)
        self.assertIn("https://x/kindy.jpg", srcs)  # bound photo used verbatim
        self.assertTrue(any("stock.example" in s for s in srcs))  # queries resolved
