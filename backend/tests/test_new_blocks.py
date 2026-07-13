"""New catalog blocks + wiring: locations (map cards), stats band, clients strip,
mood-gated playful variants, and the first-class WhatsApp button pass."""

import asyncio
import unittest

from app.models.builder_schema import BuilderElement, BuilderElementContent
from app.models.content_blocks import (
    ClientItem,
    ClientsBlock,
    LocationItem,
    LocationsBlock,
    StatItem,
    StatsBlock,
)
from app.services.section_content import (
    block_to_section,
    maps_embed_url,
    mood_allows,
    select_template,
    style_whatsapp_links,
    whatsapp_href,
)
from app.services.template_filler import fill_template, get_template


async def _stub_image(query: str):
    return f"https://images.example/{query.replace(' ', '-')}.jpg", "#888888"


def _fill(template, content):
    return asyncio.run(
        fill_template(template, content, resolve_image=_stub_image)
    )


def _walk(el):
    yield el
    if isinstance(el.content, list):
        for child in el.content:
            yield from _walk(child)


class WhatsAppHrefTest(unittest.TestCase):
    def test_international_plus_format(self):
        self.assertEqual(whatsapp_href("+60 12-345 6789"), "https://wa.me/60123456789")

    def test_double_zero_prefix(self):
        self.assertEqual(whatsapp_href("0060123456789"), "https://wa.me/60123456789")

    def test_national_format_rejected(self):
        # No country code — wa.me would be broken, so no link (caller uses tel:).
        self.assertIsNone(whatsapp_href("012-345 6789"))
        self.assertIsNone(whatsapp_href(None))


class LocationsBlockTest(unittest.TestCase):
    def _block(self):
        return LocationsBlock(
            heading="Visit us",
            items=[
                LocationItem(
                    name="Kepong Campus",
                    address="12, Jalan Prima, 52100 Kepong, KL",
                    phone="03-6257 1234",
                    whatsapp="+60123456789",
                    hours="Mon-Fri 8am-6pm",
                ),
                LocationItem(name="KD Campus", address="8, Jalan Teknologi, PJ"),
            ],
        )

    def test_selects_map_cards_template(self):
        template, content = block_to_section(self._block(), mood="friendly")
        self.assertEqual(template["id"], "locations-map-cards")
        self.assertEqual(len(content["items"]), 2)

    def test_map_src_and_ctas(self):
        _template, content = block_to_section(self._block())
        first = content["items"][0]
        self.assertIn("output=embed", first["map"]["src"])
        self.assertIn("Kepong", first["map"]["src"])
        self.assertEqual(first["whatsapp_cta"]["href"], "https://wa.me/60123456789")
        self.assertEqual(first["phone_cta"]["href"], "tel:0362571234")
        # Second branch has no phone/whatsapp — optional slots stay None.
        self.assertIsNone(content["items"][1]["whatsapp_cta"])
        self.assertIsNone(content["items"][1]["phone_cta"])

    def test_fills_video_map_node_end_to_end(self):
        template, content = block_to_section(self._block())
        element = _fill(template, content)
        videos = [e for e in _walk(element) if e.type == "video"]
        self.assertEqual(len(videos), 2)
        self.assertIn("maps.google.com", videos[0].content.src)
        self.assertIn("output=embed", videos[0].content.src)

    def test_maps_embed_url_is_keyless_embed(self):
        url = maps_embed_url("Tadika Ceria", "Jalan 1, KL")
        self.assertTrue(url.startswith("https://maps.google.com/maps?q="))
        self.assertIn("Tadika+Ceria", url)
        self.assertIn("output=embed", url)


class StatsAndClientsCatalogTest(unittest.TestCase):
    def test_stats_block_uses_counter_band(self):
        block = StatsBlock(
            heading="Our numbers",
            items=[StatItem(value="20+", label="Years"), StatItem(value="500+", label="Families")],
        )
        template, content = block_to_section(block, mood="modern")
        self.assertEqual(template["id"], "stats-counter-band")
        element = _fill(template, content)
        texts = [e.content.innerText for e in _walk(element)
                 if isinstance(e.content, BuilderElementContent) and e.content.innerText]
        self.assertIn("20+", texts)
        self.assertIn("Families", texts)

    def test_clients_block_uses_wordmark_strip(self):
        block = ClientsBlock(
            heading="Trusted by",
            items=[ClientItem(name="Skylace"), ClientItem(name="Morris Allen")],
        )
        template, content = block_to_section(block, mood="luxury")
        self.assertEqual(template["id"], "clients-logo-strip")
        element = _fill(template, content)
        texts = [e.content.innerText for e in _walk(element)
                 if isinstance(e.content, BuilderElementContent) and e.content.innerText]
        self.assertIn("Skylace", texts)


class MoodGatingTest(unittest.TestCase):
    def test_mood_allows_is_neutral_without_field(self):
        self.assertTrue(mood_allows({"id": "x"}, "luxury"))
        self.assertTrue(mood_allows({"id": "x"}, None))

    def test_gated_template_blocks_other_moods(self):
        gated = get_template("hero-playful-split")
        self.assertTrue(mood_allows(gated, "playful"))
        self.assertTrue(mood_allows(gated, "friendly"))
        self.assertFalse(mood_allows(gated, "luxury"))
        self.assertFalse(mood_allows(gated, None))

    def test_select_template_never_offers_playful_to_luxury(self):
        content = {
            "eyebrow": "E", "headline": "H", "body": "B",
            "primary_cta": {"innerText": "Go", "href": "#"},
            "image": {"query": "x", "alt": ""},
        }
        chosen = select_template(
            "hero", content, explicit_id="hero-playful-split", mood="luxury"
        )
        self.assertIsNotNone(chosen)
        self.assertNotEqual(chosen["id"], "hero-playful-split")

    def test_explicit_playful_pick_honoured_for_playful_mood(self):
        content = {
            "eyebrow": "E", "headline": "H", "body": "B",
            "primary_cta": {"innerText": "Go", "href": "#"},
            "image": {"query": "x", "alt": ""},
        }
        chosen = select_template(
            "hero", content, explicit_id="hero-playful-split", mood="playful"
        )
        self.assertEqual(chosen["id"], "hero-playful-split")


class WhatsAppButtonPassTest(unittest.TestCase):
    def _link(self, href, label="WhatsApp us"):
        return BuilderElement(
            id="l1", name="Link", type="link", styles={"color": "#000"},
            content=BuilderElementContent(innerText=label, href=href),
        )

    def test_wa_link_becomes_green_pill(self):
        el = self._link("https://wa.me/60123456789")
        styled = style_whatsapp_links([el])
        self.assertEqual(styled, 1)
        self.assertEqual(el.styles["backgroundColor"], "#25D366")
        self.assertEqual(el.styles["color"], "#ffffff")
        self.assertIn("data:image/svg+xml", el.styles["backgroundImage"])

    def test_non_wa_links_untouched(self):
        el = self._link("https://example.com/contact", "Contact")
        self.assertEqual(style_whatsapp_links([el]), 0)
        self.assertEqual(el.styles, {"color": "#000"})

    def test_icon_only_links_skipped(self):
        el = self._link("https://wa.me/60123456789", label="")
        self.assertEqual(style_whatsapp_links([el]), 0)

    def test_nested_links_found(self):
        wrapper = BuilderElement(
            id="c1", name="Row", type="container", styles={},
            content=[self._link("https://api.whatsapp.com/send?phone=60123456789")],
        )
        self.assertEqual(style_whatsapp_links([wrapper]), 1)


class ProcessStepsMoodPreferenceTest(unittest.TestCase):
    def _content(self):
        return {
            "heading": "How to enrol",
            "items": [
                {"number": "1", "title": "Say hello", "description": "d"},
                {"number": "2", "title": "Tour", "description": "d"},
            ],
        }

    def test_playful_mood_prefers_enrollment_steps(self):
        from app.services.section_content import mood_preferred_ids

        ids = mood_preferred_ids("playful", "process")
        self.assertEqual(ids[0], "process-enrollment-steps")

    def test_luxury_mood_cannot_use_enrollment_steps(self):
        chosen = select_template("process", self._content(), mood="luxury")
        self.assertEqual(chosen["id"], "process-steps")


if __name__ == "__main__":
    unittest.main()
