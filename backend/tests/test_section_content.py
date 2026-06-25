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
