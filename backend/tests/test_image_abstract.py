"""Featured images are never abstract (services/image_abstract.py).

A featured slot — split-hero picture, About image, card, gallery tile — shows
the reader something; a gradient, texture or wallpaper shows nothing, so it may
only sit behind copy. And a SOURCE background that is abstract must wear the
theme's colours, or the creative direction picks the hero instead.
"""

import asyncio
import unittest

from app.models.content_blocks import (
    AboutBlock,
    FeatureItem,
    FeaturesBlock,
    GalleryBlock,
    GalleryItem,
    HeroBlock,
    ImageMetadata,
    PagePlan,
    SourceContent,
)
from app.services.image_abstract import (
    abstract_text,
    is_abstract,
    is_placeholder_src,
    theme_fits,
)
from app.services.image_match import rank_candidates
from app.services.image_sampling import PhotoSample
from app.services.media import (
    ImageResolver,
    SourceBackgroundVerdict,
    _placeholder_photo,
    _unfit_for_featured_pin,
    monogram_avatar_url,
)
from app.services.pexels import PexelsClient, PhotoResult


class _NoStock:
    configured = False


class FakePexels:
    """Maps query -> list[PhotoResult], mirroring search_many."""

    configured = True

    def __init__(self, results):
        self.results = results
        self.queries = []

    async def search_many(self, query, *, orientation="landscape", size="large"):
        self.queries.append(query)
        return list(self.results.get(query, []))


def _stock(url, alt, description=""):
    return PhotoResult(
        url=url, alt=alt, photographer=None, photographer_url=None,
        source="pexels", avg_color="#808080", description=description,
    )


TEXTURE = dict(alt="Textured Blue Surface", width=1600, height=900)


def _texture(url="https://src.example/bg-layer.jpg", **kw):
    return ImageMetadata(url=url, **{**TEXTURE, **kw})


def _sample(hues, share=0.9):
    return PhotoSample(
        dominant_hex="#3366cc", luminance=0.2, focal_y=0.5,
        hues=tuple(hues), chromatic_share=share,
    )


# --- the predicate ------------------------------------------------------------------


class AbstractWordsTest(unittest.TestCase):
    def test_a_surface_is_abstract(self):
        for text in (
            "Textured Blue Surface",
            "abstract gradient mesh",
            "Bokeh lights at night",
            "colorful geometric pattern",
            "3D render of floating shapes",
            "blue-and-white-abstract-painting-1910236",  # a Pexels slug
            "Gradient of blue on a white background",  # the subject IS the gradient
        ):
            self.assertTrue(abstract_text(text), text)

    def test_a_subject_on_a_surface_is_not(self):
        for text in (
            "child painting on textured paper",
            "woman standing against a pink background",
            "product with a white backdrop",
            "teacher reading with children",
        ):
            self.assertFalse(abstract_text(text), text)

    def test_bare_background_is_never_a_cue(self):
        self.assertFalse(abstract_text("blue background"))
        self.assertFalse(abstract_text("hero background"))

    def test_the_filename_speaks_for_an_unlabelled_image(self):
        self.assertTrue(is_abstract(ImageMetadata(url="https://x.example/abstract-blue-bg.jpg")))
        self.assertFalse(is_abstract(ImageMetadata(url="https://x.example/our-classroom.jpg")))

    def test_a_graphic_is_abstract_only_when_its_words_agree(self):
        diagram = ImageMetadata(
            url="https://x.example/a.png", vision_kind="graphic",
            vision_caption="architecture diagram of the data pipeline",
        )
        backdrop = ImageMetadata(
            url="https://x.example/b.png", vision_kind="graphic",
            vision_caption="blue background design with curved lines",
        )
        self.assertFalse(is_abstract(diagram))
        self.assertTrue(is_abstract(backdrop))
        # The same words on a photograph are ordinary.
        self.assertFalse(is_abstract(backdrop.model_copy(update={"vision_kind": "photo"})))

    def test_provenance(self):
        self.assertTrue(is_abstract(photo=_placeholder_photo("x", "landscape", None)))
        wash = _stock("https://p/1.jpg", "sunset over the sea")
        self.assertFalse(is_abstract(photo=wash))
        self.assertTrue(is_abstract(photo=PhotoResult(**{**wash.__dict__, "is_abstract": True})))
        self.assertTrue(is_abstract(photo=_stock("https://p/2.jpg", "", "abstract blue texture")))


class ThemeFitsTest(unittest.TestCase):
    BLUE_THEME = ("#2563eb", "#0f172a", "#38bdf8")
    ORANGE_THEME = ("#ea580c", "#1c1917", "#facc15")

    def test_an_on_hue_surface_fits(self):
        self.assertTrue(theme_fits([215.0] * 50 + [200.0] * 10, self.BLUE_THEME))

    def test_an_off_hue_surface_clashes(self):
        self.assertFalse(theme_fits([215.0] * 60, self.ORANGE_THEME))

    def test_a_neutral_surface_fits_any_theme(self):
        self.assertTrue(theme_fits([215.0] * 3, self.ORANGE_THEME, chromatic_share=0.05))
        self.assertTrue(theme_fits([], self.ORANGE_THEME))

    def test_a_near_black_ink_is_not_a_hue(self):
        # #0f172a reads 39% "saturated" in HLS; by chroma it carries no hue, so a
        # navy secondary does not license a blue texture on an orange site.
        self.assertFalse(theme_fits([222.0] * 60, ("#ea580c", "#0f172a", "#facc15")))


class PlaceholderSrcTest(unittest.TestCase):
    def test_it_recognises_the_placeholder_and_nothing_else(self):
        from app.services.icons import icon_data_url
        from app.services.style_tokens import grain_data_uri

        self.assertTrue(is_placeholder_src(_placeholder_photo("x", "square", None).url))
        self.assertFalse(is_placeholder_src(monogram_avatar_url("Ada Lovelace")))
        icon = icon_data_url("star")
        self.assertIsNotNone(icon)
        self.assertFalse(is_placeholder_src(icon))
        grain = grain_data_uri().removeprefix('url("').removesuffix('")')
        self.assertFalse(is_placeholder_src(grain))
        self.assertFalse(is_placeholder_src("https://img.example/photo.jpg"))


# --- every featured gate --------------------------------------------------------------


class FeaturedGatesTest(unittest.TestCase):
    """Each gate refuses an abstract for a featured slot and still lets a
    background slot take it."""

    def test_rank_candidates(self):
        texture = _texture(intent="hero")
        for usage in ("inline", "any"):
            self.assertIsNone(rank_candidates("blue surface", "about", [texture], slot_usage=usage).chosen)
        chosen = rank_candidates("blue surface", "hero", [texture], slot_usage="background").chosen
        self.assertIs(chosen, texture)

    def test_featured_pin(self):
        texture = _texture()
        self.assertTrue(_unfit_for_featured_pin(texture, "inline"))
        self.assertTrue(_unfit_for_featured_pin(texture, "any"))
        self.assertFalse(_unfit_for_featured_pin(texture, "background"))

    def test_image_ref_binding(self):
        from app.services.image_refs import _unfit_for_kind

        texture = _texture()
        self.assertEqual(_unfit_for_kind(texture, "features", layout=None), "abstract surface")
        self.assertIsNone(_unfit_for_kind(texture, "cta", layout=None))
        self.assertIsNone(_unfit_for_kind(texture, "hero", layout="background"))

    def test_size_fallback(self):
        # The page-local fallback bypasses ranking: the biggest file on a page is
        # often its backdrop, which must not become the About image.
        texture = _texture(url="https://src.example/huge.jpg", alt="", width=3000, height=2000,
                           caption="abstract wallpaper")
        photo = ImageMetadata(url="https://src.example/team.jpg", alt="", width=800, height=600)
        resolver = ImageResolver(scraped_metadata=[texture, photo], pexels=_NoStock(),
                                 use_llm_tiebreaker=False)
        picked = asyncio.run(resolver._take_best_scraped(
            "our story", "about", prefer=[texture, photo], slot_usage="inline"))
        self.assertEqual(picked.url, photo.url)
        role_bg = ImageMetadata(url="https://src.example/band.jpg", role="background",
                                width=3000, height=2000)
        resolver = ImageResolver(scraped_metadata=[role_bg, photo], pexels=_NoStock(),
                                 use_llm_tiebreaker=False)
        picked = asyncio.run(resolver._take_best_scraped(
            "our story", "about", prefer=[role_bg, photo], slot_usage="any"))
        self.assertEqual(picked.url, photo.url)

    def test_content_photos_galleries_and_walls(self):
        from app.routers.generate import _photo_walls_from_metadata
        from app.services.source_router import gallery_photos, is_content_photo

        texture = _texture(context_heading="Our Gallery")
        css_photo = ImageMetadata(url="https://src.example/hero.jpg", alt="our playground",
                                  source_usage="css_background", width=1600, height=900,
                                  context_heading="Our Gallery")
        photos = [
            ImageMetadata(url=f"https://src.example/p{i}.jpg", alt=f"class {i}",
                          width=800, height=600, context_heading="Our Gallery")
            for i in range(3)
        ]
        self.assertFalse(is_content_photo(texture))
        # A CSS background photo is still a content photo: ContentProfile counts
        # hero photos with this, and refusing it would open the site type-led.
        self.assertTrue(is_content_photo(css_photo))

        source = SourceContent(source_kind="url", source_ref="x", title="X", raw_text="",
                               image_metadata=[texture, css_photo, *photos])
        self.assertEqual([m.url for m in gallery_photos(source)], [m.url for m in photos])
        walls = _photo_walls_from_metadata(source, chrome=set())
        self.assertEqual(walls[0].image_urls, [m.url for m in photos])


class StockSearchTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_featured_slot_skips_abstract_stock(self):
        pexels = FakePexels({
            "our garden": [
                _stock("https://p/abs.jpg", "Abstract green texture"),
                _stock("https://p/garden.jpg", "children planting in a garden"),
            ],
        })
        resolver = ImageResolver(pexels=pexels, use_llm_tiebreaker=False)
        photo = await resolver.resolve("our garden", intent="about", slot_usage="inline")
        self.assertEqual(photo.url, "https://p/garden.jpg")

    async def test_a_background_slot_still_takes_it(self):
        pexels = FakePexels({"our garden": [_stock("https://p/abs.jpg", "Abstract green garden texture")]})
        resolver = ImageResolver(pexels=pexels, use_llm_tiebreaker=False)
        photo = await resolver.resolve("our garden", intent="cta_bg", slot_usage="background")
        self.assertEqual(photo.url, "https://p/abs.jpg")

    async def test_the_texture_fallback_is_never_searched_for_a_featured_slot(self):
        from app.services.media import _GENERIC_ATMOSPHERIC_FALLBACK

        pexels = FakePexels({})
        resolver = ImageResolver(pexels=pexels, use_llm_tiebreaker=False)
        await resolver.resolve("skyline", intent="cta_bg", slot_usage="any")
        self.assertNotIn(_GENERIC_ATMOSPHERIC_FALLBACK, pexels.queries)
        await resolver.resolve("skyline", intent="cta_bg", slot_usage="background")
        self.assertIn(_GENERIC_ATMOSPHERIC_FALLBACK, pexels.queries)

    async def test_an_undescribed_photo_loses_to_a_described_one(self):
        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"photos": [
                    {"src": {"large": "https://p/blank.jpg"}, "alt": "", "url": ""},
                    {"src": {"large": "https://p/bakery.jpg"}, "alt": "",
                     "url": "https://www.pexels.com/photo/baker-kneading-bread-dough-123/"},
                ]}

        class _Client:
            async def get(self, *a, **kw):
                return _Response()

        client = PexelsClient(api_key="test")
        results = await client.search_many("baker kneading bread dough unique-q", client=_Client())
        by_url = {r.url: r for r in results}
        # The empty alt no longer impersonates the query…
        self.assertEqual(by_url["https://p/blank.jpg"].alt, "")
        # …and the page slug describes the other one.
        self.assertEqual(by_url["https://p/bakery.jpg"].description, "baker kneading bread dough")

        resolver = ImageResolver(
            pexels=FakePexels({"baker kneading bread dough": results}), use_llm_tiebreaker=False
        )
        photo = await resolver.resolve("baker kneading bread dough", intent="about")
        self.assertEqual(photo.url, "https://p/bakery.jpg")


class FeaturedSlotShowsNoImageTest(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_returns_none_for_a_featured_slot_with_nothing_real(self):
        resolver = ImageResolver(pexels=_NoStock(), use_llm_tiebreaker=False)
        for usage in ("inline", "any"):
            self.assertIsNone(await resolver.resolve("our story", intent="about", slot_usage=usage))
        backdrop = await resolver.resolve("our story", intent="cta_bg", slot_usage="background")
        self.assertEqual(backdrop.source, "placeholder")

    async def test_the_catalog_frame_collapses(self):
        from app.services.schema_builder import RenderContext, block_to_element
        from app.services.style_tokens import make_style_tokens
        from app.services.theme import build_theme

        theme = build_theme("#2563eb", "modern")
        ctx = RenderContext(
            theme=theme,
            resolver=ImageResolver(pexels=_NoStock(), use_llm_tiebreaker=False),
            styles=make_style_tokens(theme),
            current_page_slug="about",
            variety_seed="brand",
        )
        block = AboutBlock(heading="Our story", body="We bake every morning.",
                           image_query="baker kneading dough")
        el = await block_to_element(block, ctx, is_homepage=False,
                                    explicit_template_id="about-image-split")

        def walk(node):
            yield node
            if isinstance(node.content, list):
                for child in node.content:
                    yield from walk(child)

        from app.services.design_fingerprint import template_id_of

        self.assertEqual(template_id_of(el), "about-image-split")
        images = [n for n in walk(el) if n.type == "image"]
        self.assertEqual(images, [], "a featured slot with no photo must not frame a gradient")
        self.assertTrue(any(n.type == "text" for n in walk(el)))


class PlannerQueriesTest(unittest.TestCase):
    def test_an_abstract_featured_query_is_replaced_by_a_subject(self):
        from app.models.industry import PageScaffold
        from app.services.scaffold_enforcement import align_page_to_scaffold

        page = PagePlan(
            title="About us", slug="about", page_type="about",
            blocks=[
                HeroBlock(headline="About us", image_query="soft abstract pastel gradient backdrop"),
                AboutBlock(heading="Our story", body="We started in 2001.",
                           image_query="abstract texture"),
                FeaturesBlock(heading="Why us", items=[
                    FeatureItem(title="Small classes", description="Ten per room.",
                                image_query="abstract geometric shapes"),
                    FeatureItem(title="Garden", description="We grow food.",
                                image_query="children planting vegetables"),
                ]),
                GalleryBlock(heading="Life", items=[
                    GalleryItem(title="Sports day", image_query="colourful bokeh"),
                ]),
            ],
        )
        scaffold = PageScaffold(slug="about", title="About us", page_type="about",
                                sections=["hero", "about", "features", "gallery"])
        aligned = align_page_to_scaffold(page, scaffold, brand_name="Acme")
        hero, about, features, gallery = aligned.blocks
        self.assertFalse(abstract_text(hero.image_query))
        self.assertTrue(hero.image_query)
        self.assertFalse(abstract_text(about.image_query))
        self.assertIsNone(features.items[0].image_query)  # searches its title instead
        self.assertEqual(features.items[1].image_query, "children planting vegetables")
        self.assertEqual(gallery.items[0].image_query, "Sports day")

    def test_the_default_hero_names_a_subject_not_the_brand_alone(self):
        from app.services.scaffold_enforcement import _default_block

        page = PagePlan(title="Pricing", slug="pricing", page_type="pricing", blocks=[])
        hero = _default_block("hero", page=page, brand_name="Acme")
        self.assertNotIn("brand photo", hero.image_query)


# --- the source background --------------------------------------------------------------


class SourceBackgroundVetTest(unittest.IsolatedAsyncioTestCase):
    """The source's strongest CSS background leads the homepage only if it may."""

    ORANGE = dict(primary_hex="#ea580c", secondary_hex="#1c1917", accent_hex="#facc15")
    BLUE = dict(primary_hex="#2563eb", secondary_hex="#0f172a", accent_hex="#38bdf8")

    def _resolver(self, pool, theme, samples):
        resolver = ImageResolver(scraped_metadata=pool, pexels=_NoStock(),
                                 use_llm_tiebreaker=False, **theme)
        # Pixel sampling is off for the suite (conftest); seed what it would read.
        resolver._samples.update(samples)
        return resolver

    async def test_a_clashing_texture_is_dropped_and_the_next_one_judged(self):
        texture = _texture(source_usage="css_background", width=2400, height=1400)
        photo = ImageMetadata(url="https://src.example/playground.jpg", alt="our playground",
                              source_usage="css_background", width=1600, height=900)
        resolver = self._resolver([texture, photo], self.ORANGE,
                                  {texture.url: _sample([215.0] * 60)})

        verdict = await resolver.vet_source_background()

        self.assertEqual(verdict.kept, photo)
        self.assertEqual(verdict.verdict, "photo")
        self.assertEqual(verdict.dropped, 1)
        # Withdrawn from every slot, the CTA's background included.
        cta = await resolver.resolve("blue surface", intent="cta_bg", slot_usage="background",
                                     pinned_url=texture.url)
        self.assertNotEqual(cta.url, texture.url)

    async def test_only_clashing_abstracts_means_no_source_background(self):
        texture = _texture(source_usage="css_background")
        resolver = self._resolver([texture], self.ORANGE, {texture.url: _sample([215.0] * 60)})
        verdict = await resolver.vet_source_background()
        self.assertIsNone(verdict.kept)
        self.assertEqual(verdict.dropped, 1)

    async def test_an_on_theme_texture_is_kept(self):
        texture = _texture(source_usage="css_background")
        resolver = self._resolver([texture], self.BLUE, {texture.url: _sample([215.0] * 60)})
        verdict = await resolver.vet_source_background()
        self.assertEqual(verdict.kept, texture)
        self.assertEqual(verdict.verdict, "on-theme abstract")

    async def test_a_photograph_is_never_colour_judged(self):
        photo = ImageMetadata(url="https://src.example/shopfront.jpg", alt="our shopfront",
                              source_usage="css_background", width=1600, height=900)
        resolver = self._resolver([photo], self.ORANGE, {photo.url: _sample([215.0] * 60)})
        verdict = await resolver.vet_source_background()
        self.assertEqual((verdict.kept, verdict.verdict), (photo, "photo"))

    async def test_an_unmeasured_abstract_is_kept_as_before(self):
        texture = _texture(source_usage="css_background")
        resolver = self._resolver([texture], self.ORANGE, {})
        verdict = await resolver.vet_source_background()
        self.assertEqual((verdict.kept, verdict.verdict), (texture, "unmeasured abstract"))

    async def test_a_background_slot_repicks_past_the_clash_it_won(self):
        # The hero ranker intent-pins the PAGE's CSS background, which need not
        # be the strongest one the vet judged — so the pick is judged too.
        texture = _texture(source_usage="css_background", intent="hero")
        photo = ImageMetadata(url="https://src.example/hall.jpg", alt="school hall",
                              source_usage="css_background", intent="hero",
                              width=1600, height=900)
        resolver = self._resolver([texture, photo], self.ORANGE,
                                  {texture.url: _sample([215.0] * 60)})
        hero = await resolver.resolve("school", intent="hero", slot_usage="background",
                                      prefer=[texture, photo])
        self.assertEqual(hero.url, photo.url)

    def test_the_decision_names_no_url(self):
        from app.services.schema_builder import _source_background_decision

        texture = _texture(source_usage="css_background")
        dropped = _source_background_decision(SourceBackgroundVerdict(None, None, 1))
        kept = _source_background_decision(SourceBackgroundVerdict(texture, "on-theme abstract"))
        self.assertEqual(dropped.area, "hero-source-background")
        self.assertEqual(dropped.choice, "dropped clashing abstract")
        self.assertEqual(kept.choice, "kept on-theme abstract")
        for decision in (dropped, kept):
            self.assertNotIn("http", decision.rationale)


class EvaluatorTest(unittest.TestCase):
    def test_a_framed_placeholder_is_reported(self):
        from app.models.builder_schema import (
            BodySchema,
            BuilderElement,
            BuilderElementContent,
            GeneratedPage,
            GeneratedSite,
            PageSeo,
        )
        from app.services.design_quality import _check_abstract_imagery, _context

        placeholder = _placeholder_photo("x", "square", None).url
        image = BuilderElement(
            name="About Image", type="image",
            content=BuilderElementContent(src=placeholder, alt="our team"),
        )
        tile = BuilderElement(
            name="Bento Tile", type="container",
            styles={"backgroundImage": f'linear-gradient(#0000, #0008), url("{placeholder}")'},
            content=[],
        )
        hero = BuilderElement(  # a hero's own band may be a surface
            name="Hero - Background", type="container",
            styles={"backgroundImage": f'url("{placeholder}")'}, content=[],
        )
        about = BuilderElement(name="About", type="container", content=[image, tile])
        site = GeneratedSite(
            site_name="X",
            pages=[GeneratedPage(
                slug="", title="Home", is_homepage=True,
                body_schema=BodySchema(elements=[hero, about]),
                seo=PageSeo(title="T", description="d"),
            )],
        )
        findings = list(_check_abstract_imagery(_context(site)))
        self.assertEqual({f.code for f in findings}, {"abstract-featured-image"})
        self.assertEqual(sorted(f.node for f in findings), ["About Image", "Bento Tile"])


if __name__ == "__main__":
    unittest.main()
