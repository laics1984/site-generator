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


class ProfileBlockTest(unittest.TestCase):
    """A person's own page: portrait, identity, story, contact.

    The block exists because a roster grid rendering a single card got
    everything slightly wrong — thumbnail portrait, name restated under a hero
    that already said it, nowhere for the contact details a directory carries.
    """

    def _block(self, **overrides):
        from app.models.content_blocks import ProfileBlock, ProfileContact

        defaults = dict(
            name="Ashley Jinivon",
            role="Treasurer",
            credentials="MT-BC",
            bio="Ashley holds an equivalency degree.\nShe works with preterm infants.",
            photo_url="https://x/ashley.jpg",
            photo_alt="Ashley Jinivon",
            contacts=[
                ProfileContact(label="ashley@x.my", href="mailto:ashley@x.my")
            ],
        )
        defaults.update(overrides)
        return ProfileBlock(**defaults)

    def _fill(self, template_id, block):
        from app.services.section_content import _profile_content

        return asyncio.run(
            fill_template(
                get_template(template_id), _profile_content(block), resolve_image=_stub_image
            )
        )

    def test_every_variant_renders_the_whole_person(self):
        block = self._block()
        for template_id in ("profile-portrait-split", "profile-centered", "profile-banner"):
            with self.subTest(template=template_id):
                rendered = str(self._fill(template_id, block).model_dump())

                self.assertIn("https://x/ashley.jpg", rendered)
                self.assertIn("Ashley Jinivon", rendered)
                self.assertIn("preterm infants", rendered)
                self.assertIn("mailto:ashley@x.my", rendered)

    def test_a_person_without_a_portrait_gets_their_monogram(self):
        # Never a stock face: a stranger's portrait under a real name is a
        # misattribution. An empty photo slot would also make all three
        # variants infeasible, since every one declares `photo` required.
        from app.services.section_content import _profile_content

        content = _profile_content(self._block(photo_url=None))

        self.assertEqual(content["photo"], {"monogram": "Ashley Jinivon", "alt": "Ashley Jinivon"})
        rendered = str(self._fill("profile-portrait-split", self._block(photo_url=None)).model_dump())
        self.assertNotIn("images.example", rendered)

    def test_mood_picks_the_variant(self):
        """Through the real path: block_to_section applies the content
        preference and the mood gate together.

        These are the generator's actual BrandMood values. The test used to pass
        "bold"/"classic"/"elegant"/"minimal", which the catalog declared but the
        generator never produces — so it exercised the gating mechanism while
        profile-centered was unreachable on every real site."""
        block = self._block()
        picks = {
            mood: block_to_section(block, mood=mood)[0]["id"]
            for mood in ("modern", "playful", "friendly", "editorial", "luxury", "technical")
        }

        # Expressive moods take the brand-coloured banner…
        for mood in ("modern", "playful", "friendly"):
            self.assertEqual(picks[mood], "profile-banner", mood)
        # …restrained ones the formal, institutional centred layout.
        for mood in ("editorial", "luxury", "technical"):
            self.assertEqual(picks[mood], "profile-centered", mood)

    def test_the_split_catches_a_brand_with_no_mood(self):
        # The split declares no moods, so it is the ungated default.
        block = self._block()
        self.assertEqual(block_to_section(block, mood=None)[0]["id"], "profile-portrait-split")

    def test_contactless_profile_drops_the_link_row(self):
        rendered = str(self._fill("profile-centered", self._block(contacts=[])).model_dump())

        self.assertIn("Ashley Jinivon", rendered)
        self.assertNotIn("mailto:", rendered)

    def test_name_leads_and_the_designation_follows_it(self):
        # Every profile page is written this way, including the sources these
        # are built from: MMTA's own stylesheet is `.name` then `.designation`.
        from app.services.section_content import _profile_content

        for template_id in ("profile-portrait-split", "profile-centered", "profile-banner"):
            with self.subTest(template=template_id):
                rendered = self._fill(template_id, self._block()).model_dump()

                order = []

                def walk(node):
                    if isinstance(node, dict):
                        if node.get("name") in ("Name", "Role"):
                            order.append(node["name"])
                        for value in node.values():
                            walk(value)
                    elif isinstance(node, list):
                        for item in node:
                            walk(item)

                walk(rendered)
                self.assertEqual(order, ["Name", "Role"])

        # And the designation is styled as a title, not micro-type.
        content = _profile_content(self._block())
        self.assertEqual(content["role"], "Treasurer")

    def test_the_portrait_is_centred_in_every_variant(self):
        for template_id in ("profile-portrait-split", "profile-centered", "profile-banner"):
            with self.subTest(template=template_id):
                rendered = self._fill(template_id, self._block()).model_dump()

                def find(node, name):
                    if isinstance(node, dict):
                        if node.get("name") == name:
                            return node
                        for value in node.values():
                            hit = find(value, name)
                            if hit is not None:
                                return hit
                    elif isinstance(node, list):
                        for item in node:
                            hit = find(item, name)
                            if hit is not None:
                                return hit
                    return None

                portrait = find(rendered, "Portrait")
                if template_id == "profile-portrait-split":
                    # Centred inside its column rather than hugging its left edge.
                    cell = find(rendered, "Portrait Cell")
                    self.assertEqual(cell["styles"]["alignItems"], "center")
                    self.assertEqual(cell["styles"]["justifyContent"], "center")
                else:
                    self.assertEqual(portrait["styles"]["alignSelf"], "center")
