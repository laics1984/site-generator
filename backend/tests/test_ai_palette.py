"""Creative decisions, numerical guarantees and color-only boundaries."""
import asyncio
import copy
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app.config import settings
from app.models.brand import BrandIdentity, CreativePalette
from app.models.builder_schema import BuilderElement, BuilderElementContent, GeneratedSite
from app.models.content_blocks import SourceContent
from app.services.color_system import contrast, creative_colors, derive, ensure_contrast, from_oklch, oklch
from app.services.design_direction import ConceptChoice, regenerate_palette, resolve_direction
from app.services.logo import extract_palette_from_image_bytes
from app.services.palette_updates import current_theme, design_inputs, palette_patch, patched_site
from app.services.section_content import enforce_text_contrast
from app.services.theme import apply_creative_palette, build_theme

PALETTES = [
    {"primary":"#facc15","secondary":"#6d8d6d","accent":"#e26858","surface":"#faf5eb"},
    {"primary":"#10253f","secondary":"#965f4c","accent":"#d6a94c","surface":"#f4f0e9"},
    {"primary":"#00ff80","secondary":"#ff0055","accent":"#4d42dc","surface":"#fafafa"},
    {"primary":"#c7b6a4","secondary":"#73786b","accent":"#a87765","surface":"#f7f4ee"},
    {"primary":"#9989d7","secondary":"#5b9a8a","accent":"#edb55b","surface":"#172131"},
]


def theme(palette=PALETTES[0], scheme="light"):
    return apply_creative_palette(build_theme(palette["primary"], color_scheme=scheme), CreativePalette(**palette))


def site(t=None):
    t = t or theme()
    return GeneratedSite(site_name="Little Acorns", pages=[], theme=t,
                         brand=BrandIdentity(name="Little Acorns", extracted_palette=[t.palette.primary]),
                         builder_styles=t.to_builder_styles(),
                         palette_brief={"business":"Little Acorns", "industry":"childcare", "audience":"parents",
                                        "brand_colors":[t.palette.primary]})


@pytest.mark.parametrize("palette", PALETTES)
@pytest.mark.parametrize("surface", ["#faf5eb", "#172131"])
def test_every_semantic_pair_and_state_meets_aa(palette, surface):
    colors = creative_colors({**palette, "surface": surface})
    d = derive(colors)
    for key, fg in d.items():
        if key.startswith("on-"):
            assert contrast(fg, d[key[3:]]) >= 4.5, key
    for role in ("background", "surface", "surface-inverse", "band-1", "band-2", "band-3", "band-4"):
        for brand in ("primary", "secondary", "accent"):
            assert contrast(d[f"{brand}-ink-on-{role}"], d[role]) >= 4.5
    assert contrast(d["border-strong"], d["background"]) >= 3
    assert contrast(d["text-muted"], d["background"]) >= 4.5
    assert colors["primary"] == palette["primary"]
    assert colors["surface"] == surface


def test_established_contrast_and_gamut_vectors():
    assert contrast("#000000", "#ffffff") == 21
    assert contrast("#777777", "#ffffff") < 4.5
    for color in ("#ff0000", "#00ff00", "#0000ff", "#ffffff", "#000000", "#c4775a"):
        assert from_oklch(*oklch(color)) == color
        for background in ("#ffffff", "#000000", "#888888", "#5e7688"):
            assert contrast(ensure_contrast(color, background), background) >= 4.5


@pytest.mark.parametrize("bad", [None, {}, {"primary":"#fff"}, {**PALETTES[0], "accent":"nonsense"}, "blue"])
def test_bad_ai_palette_does_not_discard_concept_or_accept_partial_colors(bad):
    choice = ConceptChoice.model_validate({"concept":1,"palette":bad})
    assert choice.palette is None
    assert choice.concept == 1


def test_short_hex_is_normalized_as_a_complete_palette():
    assert CreativePalette(**{key:"#abc" for key in PALETTES[0]}).primary == "#aabbcc"


def test_monochrome_logo_is_not_evidence_of_generic_blue():
    image = Image.new("RGB", (30,30), "black")
    data = io.BytesIO(); image.save(data, format="PNG")
    extracted = extract_palette_from_image_bytes(data.getvalue())
    assert extracted.palette == []
    assert extracted.seed_hex is None


def test_color_patch_preserves_content_noncolor_styles_and_brand_baseline():
    original = site()
    original.builder_styles["typography"]["headingFont"] = "Custom font"
    original.builder_styles["buttons"]["radius"] = 7
    before = copy.deepcopy(original.model_dump())
    patch = palette_patch(original, CreativePalette(**PALETTES[1]))
    after = patched_site(original, patch)
    assert original.model_dump() == before
    assert after.pages is original.pages
    assert patch.builder_styles["typography"] == before["builder_styles"]["typography"]
    assert patch.builder_styles["buttons"]["radius"] == 7
    assert patch.builder_styles["brand"] == before["builder_styles"]["brand"]
    assert set(patch.model_dump()) == {"theme","builder_styles","primary_color","secondary_color"}


def test_manual_button_and_page_overrides_survive_palette_save():
    original = site()
    original.builder_styles["buttons"].update(background="#774422", text="#ffffff")
    original.builder_styles["page"]["background"] = "#ececec"
    patch = palette_patch(original, CreativePalette(**PALETTES[1]))
    assert patch.builder_styles["buttons"] == original.builder_styles["buttons"]
    assert patch.builder_styles["page"] == original.builder_styles["page"]


def test_legacy_output_cannot_be_recolored():
    original = site(); original.builder_styles.pop("colorSystemVersion")
    with pytest.raises(ValueError, match="older"):
        current_theme(original)


def test_neutral_text_below_aa_and_own_opacity_are_repaired():
    t = theme()
    node = BuilderElement(id="copy", name="Body", type="text", styles={"color":"#888888","fontSize":"16px","opacity":.5}, content=BuilderElementContent(innerText="Read this"))
    assert enforce_text_contrast([node], t) == 1
    assert contrast(node.styles["color"], t.page.background) >= 4.5
    assert "opacity" not in node.styles


class Model:
    def __init__(self, palette):
        self.palette = palette; self.calls = []
    async def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["schema"].model_validate({"concept":0,"hero_height":"full","palette":self.palette})


@pytest.mark.asyncio
async def test_existing_art_direction_call_creates_palette_from_business_evidence(monkeypatch):
    monkeypatch.setattr(settings, "ai_palette_enabled", True)
    monkeypatch.setattr(settings, "design_schemes_enabled", True)
    monkeypatch.setattr(settings, "design_language_enabled", True)
    model = Model(PALETTES[1])
    direction = await resolve_direction(brand_name="Calm Acorns", mood="friendly", industry="childcare",
        palette_design_mode="shared", logo_hex="#c4775a", brand_colors=["#c4775a", "#65766b"], audience="parents seeking calm learning",
        reference_colors=["#eee6d9"], image_colors=["#8a9973"], llm=model)
    assert len(model.calls) == 1
    prompt = model.calls[0]["user_prompt"]
    assert all(word in prompt for word in ("childcare", "parents seeking calm learning", "#8a9973", "#65766b"))
    assert direction.theme.palette.primary == "#c4775a"
    assert direction.theme.palette.accent == PALETTES[1]["accent"]
    assert direction.theme.color_system_version == 2


@pytest.mark.asyncio
async def test_regeneration_keeps_typography_layout_and_hero():
    original = theme(); original.hero_background_height = "banded"
    model = Model(PALETTES[1])
    next_theme = await regenerate_palette(original, {"business":"Acorns", "industry":"childcare"}, "Earthy and refined", model)
    assert next_theme.typography == original.typography
    assert next_theme.hero_background_height == "banded"
    assert next_theme.section_rotation == original.section_rotation
    assert next_theme.design_scheme == original.design_scheme
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_invalid_regeneration_keeps_original_palette():
    from app.services.llm import LlmError
    original = theme(); before = original.model_dump()
    with pytest.raises(LlmError):
        await regenerate_palette(original, {}, llm=Model({"primary":"#abc"}))
    assert original.model_dump() == before


def test_website_reference_colors_are_separate_from_actual_logo_evidence():
    source = SourceContent(source_kind="url",source_ref="https://example.test",raw_text="A calm nursery",reference_colors=["#ccaa88"])
    inputs = design_inputs(source, BrandIdentity(name="Acorns",extracted_palette=["#aabbcc"],logo_source="og-image"))
    assert inputs["brand_colors"] == []
    assert inputs["image_colors"] == ["#aabbcc"]
    assert inputs["reference_colors"] == ["#ccaa88"]


@pytest.mark.asyncio
async def test_saved_preview_palette_survives_resume_but_unpublished_edits_do_not_change_live_snapshot(tmp_path, monkeypatch):
    from app.services import db, generation_projects
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "palette.db")
    monkeypatch.setattr(db, "_BOOTSTRAPPED", False)
    original = site()
    project_id = await generation_projects.create_project({"selected_pages":[]}, original)
    original.generation_project_id = project_id
    patch = palette_patch(original, CreativePalette(**PALETTES[1]))
    await generation_projects.save_project_palette(project_id, patch, unpublished_only=True)
    stored = await generation_projects.get_project(project_id)
    assert stored["preview_site"]["builder_styles"]["colors"]["primary"] == PALETTES[1]["primary"]
    assert stored["request"]["palette_override"]["primary"] == PALETTES[1]["primary"]
    await generation_projects.mark_preview_published(project_id)
    different = palette_patch(original, CreativePalette(**PALETTES[2]))
    await generation_projects.save_project_palette(project_id, different, unpublished_only=True)
    stored = await generation_projects.get_project(project_id)
    assert stored["preview_site"]["builder_styles"]["colors"]["primary"] == PALETTES[1]["primary"]
    await generation_projects.save_project_palette(project_id, different)
    stored = await generation_projects.get_project(project_id)
    assert stored["preview_site"]["builder_styles"]["colors"]["primary"] == PALETTES[2]["primary"]


@pytest.mark.asyncio
async def test_live_palette_publisher_only_writes_styles_and_checks_expected_version():
    from app.services.cms_client import CmsApiError
    from app.services.palette_publish import publish_palette
    original = site()
    original.builder_styles["typography"]["headingFont"] = "Owner font"
    proposed = patched_site(original, palette_patch(original, CreativePalette(**PALETTES[1])))
    class Cms:
        def __init__(self): self.writes = []
        async def get_builder_styles(self): return {"data":{"builder_styles":original.builder_styles,"layout":{"versionId":"version-2"}}}
        async def update_builder_styles(self, styles, **kwargs):
            self.writes.append((styles,kwargs)); return {"data":{"layout":{"versionId":"version-3"}}}
    cms = Cms()
    with pytest.raises(CmsApiError, match="changed"):
        await publish_palette(cms,site=proposed,expected_version="version-1",project_target="default",entity_token="token")
    assert cms.writes == []
    result = await publish_palette(cms,site=proposed,expected_version="version-2",project_target="default",entity_token="token")
    assert result["success"] and result["cache_refresh_pending"]
    assert cms.writes[0][0]["typography"]["headingFont"] == "Owner font"
    assert cms.writes[0][0]["brand"] == original.builder_styles["brand"]
    assert cms.writes[0][1] == {"expected_layout_version_id":"version-2"}


@pytest.mark.asyncio
async def test_generated_sections_are_bound_and_palette_changes_preserve_their_schema(monkeypatch):
    from app.models.content_blocks import SitePlan, PagePlan, HeroBlock, FeaturesBlock, FeatureItem, CtaBlock
    from app.services.schema_builder import plan_to_site
    from app.services.section_content import _own_fill, _NO_FILL, _UNREADABLE, _hex_to_rgb, _to_hex, _parse_color, _is_large_text
    monkeypatch.setattr(settings, "design_brain_enabled", False)
    monkeypatch.setattr(settings, "design_engine_enabled", False)
    plan = SitePlan(site_name="Acorns", pages=[PagePlan(slug="",title="Home",is_homepage=True,page_type="home",blocks=[
        HeroBlock(headline="A calm place to grow",subheadline="Learning with care",primary_cta_label="Visit",primary_cta_href="/contact"),
        FeaturesBlock(heading="Our approach", items=[FeatureItem(title="Considered",description="Space to explore and learn.")]),
        CtaBlock(headline="Come and meet us",cta_label="Visit",cta_href="/contact"),
    ])])
    generated = await plan_to_site(plan, theme=theme())
    before = copy.deepcopy([p.model_dump() for p in generated.pages])
    updated = patched_site(generated, palette_patch(generated, CreativePalette(**PALETTES[1])))
    assert [p.model_dump() for p in updated.pages] == before
    assert "--builder-color-" in json.dumps(before)
    assert all("wt-color-system-v2" in (node.classes or "") for node in generated.pages[0].body_schema.elements)
    # Resolve the actual emitted variables with each edited palette, not only
    # the utility's nominal pairs. Flat surfaces are measurable without photos.
    def inspect(node, active_theme, below):
        fill = _own_fill(node, active_theme, below)
        if fill is _UNREADABLE: return
        if fill is not _NO_FILL: below = fill
        if node.type in ("text","link") and below is not None:
            foreground = _parse_color(node.styles.get("color"), active_theme)
            if foreground:
                assert contrast(_to_hex(foreground[0]),_to_hex(below)) >= (3 if _is_large_text(node.styles) else 4.5), (node.name,node.styles)
        if isinstance(node.content,list):
            for child in node.content: inspect(child,active_theme,below)
    for active in (generated,updated):
        t = current_theme(active)
        for node in active.pages[0].body_schema.elements: inspect(node,t,_hex_to_rgb(t.palette.background))


@pytest.mark.parametrize("fill", ["#ffffff", "#25D366"])
def test_a_solid_button_on_a_photo_hero_is_inked_for_the_button(fill):
    """uniquekindies: the hero's button was inked for the photo's scrim (white)
    on its own white fill. A data: icon on the button is not a photo either."""
    from app.services.palette_styles import bind_theme_styles, rendering_theme

    t = theme()
    icon = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E\")"
    button = BuilderElement(id="cta", name="Link", type="link",
                            styles={"backgroundColor": fill, "backgroundImage": icon, "color": "#ffffff"},
                            content=BuilderElementContent(innerText="Enrol now", href="#"))
    title = BuilderElement(id="h", name="Heading", type="text", styles={"color": "#ffffff"},
                           content=BuilderElementContent(innerText="Welcome"))
    hero = BuilderElement(id="hero", name="Hero - Background", type="container",
                          styles={"backgroundImage": "url(https://images.pexels.com/photos/1/a.jpg)",
                                  "backgroundColor": "var(--builder-color-secondary)"},
                          content=[title, button])
    bind_theme_styles([hero], t, rendering_theme(t))
    ink = button.styles["color"]
    from app.services.section_content import _parse_color, _to_hex
    resolved = _to_hex(_parse_color(ink, t)[0])
    assert contrast(resolved, fill) >= 4.5, (ink, fill)
    assert "photo-ink" in title.styles["color"]


def test_grain_is_not_a_photo_and_inherited_copy_opacity_is_removed():
    from app.services.palette_styles import bind_theme_styles, rendering_theme
    from app.services.section_content import _parse_color, _to_hex
    t = theme()
    label = BuilderElement(id="label", name="Body", type="text", styles={"color":"#ffffff"},
                           content=BuilderElementContent(innerText="Readable copy"))
    decoration = BuilderElement(id="decoration", name="Decoration", type="container", styles={"opacity":.3})
    section = BuilderElement(id="grain", name="Section", type="container", styles={
        "backgroundColor":t.palette.surface,"opacity":.4,
        "backgroundImage":"url(\"data:image/svg+xml,%3Csvg/%3E\")"}, content=[label, decoration])
    bind_theme_styles([section], t, rendering_theme(t))
    assert "opacity" not in section.styles
    assert decoration.styles["opacity"] == .3
    assert "photo-ink" not in label.styles["color"]
    assert contrast(_to_hex(_parse_color(label.styles["color"],t)[0]),t.palette.surface) >= 4.5


@pytest.mark.parametrize("fill", ["#ffffff", "#888888", "#25d366"])
def test_explicit_solid_fills_keep_readable_copy_even_on_a_dark_theme(fill):
    from app.services.palette_styles import bind_theme_styles, rendering_theme
    from app.services.section_content import _parse_color, _to_hex
    t = theme(PALETTES[4],"dark")
    label = BuilderElement(id="label",name="Body",type="text",styles={"color":"#ffffff"},content=BuilderElementContent(innerText="Read me"))
    node = BuilderElement(id="card",name="Card",type="container",styles={"backgroundColor":fill},content=[label])
    bind_theme_styles([node],t,rendering_theme(t))
    assert contrast(_to_hex(_parse_color(label.styles["color"],t)[0]),fill) >= 4.5


def test_a_gradient_panel_inside_a_raw_band_is_inked_for_the_panel():
    """watr's CTA: the section paints a raw-hex band tint, the `cta-banner`
    panel inside it a raw-hex gradient that the pass retokenises to the dark
    surface-inverse. The band's fixed surface used to leak through the panel,
    so the copy took dark ink for the cream band — 1.03:1 on the panel."""
    import re
    from app.services.palette_styles import bind_theme_styles, rendering_theme
    t = apply_creative_palette(build_theme("#b91c1c", mood="friendly", industry="nonprofit"),
                               {"primary": "#b91c1c", "secondary": "#2b2620",
                                "accent": "#d97706", "surface": "#f5ece3"})
    rendered = rendering_theme(t)
    heading = BuilderElement(id="h", name="Heading", type="text",
                             styles={"color": "var(--builder-color-ink-light, #ffffff)"},
                             content=BuilderElementContent(innerText="Ready to succeed"))
    body = BuilderElement(id="b", name="Body", type="text", styles={"color": "#fef9f5"},
                          content=BuilderElementContent(innerText="Join us."))
    panel = BuilderElement(id="p", name="CTA Banner", type="container", content=[heading, body],
                           styles={"background": f"linear-gradient(135deg, {rendered.palette.secondary}, {t.palette.primary})"})
    section = BuilderElement(id="s", name="CTA - Banner", type="container", content=[panel],
                             styles={"backgroundColor": "#eedfd0"})
    bind_theme_styles([section], t, rendered)
    derived = derive(t.palette.model_dump())

    def live(value):  # what the renderer paints: the token's derived value
        m = re.search(r"var\(--builder-color-([a-z0-9-]+)", value)
        return derived[m.group(1)] if m else value

    panel_fill = live(panel.styles["background"])
    for node in (heading, body):
        assert contrast(live(node.styles["color"]), panel_fill) >= 4.5, node.styles["color"]


@pytest.mark.asyncio
async def test_model_failure_uses_one_complete_validated_fallback(monkeypatch):
    from app.services.llm import LlmError
    monkeypatch.setattr(settings,"ai_palette_enabled",True)
    monkeypatch.setattr(settings,"design_schemes_enabled",True)
    monkeypatch.setattr(settings,"design_language_enabled",True)
    class FailedModel:
        calls = 0
        async def chat_json(self,**kwargs):
            self.calls += 1
            raise LlmError("Model unavailable")
    model = FailedModel()
    result = await resolve_direction(brand_name="A new business",mood="friendly",industry="childcare",logo_hex=None,llm=model,palette_design_mode="shared")
    assert model.calls == 1
    assert result.theme.color_system_version == 2
    derived = derive(result.theme.palette.model_dump())
    assert contrast(derived["on-primary"],derived["primary"]) >= 4.5


@pytest.mark.asyncio
async def test_live_publisher_preserves_independent_button_and_page_customizations():
    from app.services.palette_publish import publish_palette
    original = site()
    live = copy.deepcopy(original.builder_styles)
    live["buttons"].update(background="#774422",text="#ffffff")
    live["page"]["background"] = "#ececec"
    proposed = patched_site(original,palette_patch(original,CreativePalette(**PALETTES[1])))
    class Cms:
        writes = []
        async def get_builder_styles(self):return {"data":{"builder_styles":live,"layout":{"versionId":"v1"}}}
        async def update_builder_styles(self,styles,**kwargs):
            self.writes.append(styles);return {"data":{"layout":{"versionId":"v2"}}}
    cms = Cms()
    await publish_palette(cms,site=proposed,expected_version="v1",project_target="default",entity_token="token")
    assert cms.writes[0]["buttons"] == live["buttons"]
    assert cms.writes[0]["page"] == live["page"]


@pytest.mark.asyncio
async def test_failed_live_write_never_updates_the_extension_snapshot(monkeypatch):
    from app.services import generation_projects
    from app.services.palette_publish import publish_palette
    from app.services.cms_client import CmsApiError
    original = site(); original.generation_project_id = "saved-preview"
    class Cms:
        async def get_builder_styles(self):return {"data":{"builder_styles":original.builder_styles,"layout":{"versionId":"v1"}}}
        async def list_entities(self):return [{"entity_api_token":"token","entity_id":"entity"}]
        async def update_builder_styles(self,*args,**kwargs):raise CmsApiError(409,"Concurrent style save")
    writes = []
    async def get_project(project_id):return {"cms_target":"default","entity_id":"entity"}
    async def save(*args,**kwargs):writes.append(args)
    monkeypatch.setattr(generation_projects,"get_project",get_project)
    monkeypatch.setattr(generation_projects,"save_project_palette",save)
    with pytest.raises(CmsApiError):
        await publish_palette(Cms(),site=original,expected_version="v1",project_target="default",entity_token="token")
    assert not writes


@pytest.mark.asyncio
async def test_snapshot_failure_is_distinct_from_successful_live_publication(monkeypatch):
    from app.services import generation_projects
    from app.services.palette_publish import publish_palette
    original = site(); original.generation_project_id = "saved-preview"
    class Cms:
        async def get_builder_styles(self):return {"data":{"builder_styles":original.builder_styles,"layout":{"versionId":"v1"}}}
        async def list_entities(self):return [{"entity_api_token":"token","entity_id":"entity"}]
        async def update_builder_styles(self,*args,**kwargs):return {"data":{"layout":{"versionId":"v2"}}}
    async def get_project(project_id):return {"cms_target":"default","entity_id":"entity"}
    async def save(*args,**kwargs):raise OSError("Disk unavailable")
    monkeypatch.setattr(generation_projects,"get_project",get_project)
    monkeypatch.setattr(generation_projects,"save_project_palette",save)
    result = await publish_palette(Cms(),site=original,expected_version="v1",project_target="default",entity_token="token")
    assert result["success"] and not result["project_updated"]
    assert "live palette was saved" in result["warning"]


@pytest.mark.asyncio
async def test_palette_http_endpoints_never_return_replacement_content(monkeypatch):
    import httpx
    from fastapi import FastAPI
    from app.routers import preview, brand
    app = FastAPI(); app.include_router(preview.router); app.include_router(brand.router)
    original = site()
    async def propose(t,brief,direction):return theme(PALETTES[1])
    monkeypatch.setattr(brand,"regenerate_palette",propose)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        for path,payload in [("/api/preview/palette",{"palette":PALETTES[1]}),("/api/brand/regenerate-palette",{"design_direction":"Calm and refined"})]:
            response = await client.post(path,json={"site":original.model_dump(mode="json"),**payload})
            assert response.status_code == 200
            assert set(response.json()) == {"theme","builder_styles","primary_color","secondary_color"}
        invalid = await client.post("/api/preview/palette",json={"site":original.model_dump(mode="json"),"palette":{"primary":"#abc"}})
        assert invalid.status_code == 422
        legacy = original.model_dump(mode="json");legacy["builder_styles"].pop("colorSystemVersion")
        response = await client.post("/api/preview/palette",json={"site":legacy,"palette":PALETTES[1]})
        assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("public_colors,state", [(PALETTES[0],"verified"),(PALETTES[1],"pending")])
async def test_public_verification_observes_cached_html_without_credentials_or_cache_bypass(public_colors,state):
    import httpx
    from app.services.palette_verification import verify_public_palette
    class Cms:
        async def list_entities(self):return [{"entity_api_token":"token","public_url":"https://colortest0001.example.test/"}]
    def handler(request):
        assert request.url == "https://colortest0001.example.test/"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        assert "cache-control" not in request.headers
        styles = ";".join(f"--builder-color-{key}:{value}" for key,value in public_colors.items())
        return httpx.Response(200,text=f'<div class="wt-site" style="{styles}"><section class="wt-color-system-v2">Copy</section></div>')
    result = await verify_public_palette(Cms(),"token",CreativePalette(**PALETTES[0]),transport=httpx.MockTransport(handler))
    assert result["state"] == state


@pytest.mark.asyncio
async def test_public_verification_cannot_be_pointed_at_an_unlisted_site():
    from app.services.palette_verification import verify_public_palette
    class Cms:
        async def list_entities(self):return [{"entity_api_token":"owned","public_url":"https://owned.example.test/"}]
    result = await verify_public_palette(Cms(),"not-owned",CreativePalette(**PALETTES[0]))
    assert result["state"] == "unavailable"


@pytest.mark.parametrize("band", ["background","surface-inverse"])
def test_small_accent_chips_validate_their_own_tint_after_recoloring(band):
    from app.services.palette_styles import bind_theme_styles, rendering_theme
    from app.services.section_content import _own_fill, _parse_color, _to_hex, _hex_to_rgb
    t=theme(PALETTES[1])
    r,g,b=(int(t.palette.accent[i:i+2],16) for i in (1,3,5))
    chip=BuilderElement(id="chip",name="Eyebrow",type="text",styles={
        "backgroundColor":f"rgba({r}, {g}, {b}, .13)","color":t.palette.accent,"fontSize":"13px"},content=BuilderElementContent(innerText="About"))
    root=BuilderElement(id="section",name="Section",type="container",styles={"backgroundColor":f"var(--builder-color-{band})"},content=[chip])
    bind_theme_styles([root],t,rendering_theme(t))
    for edited in (t,theme(PALETTES[4],"dark")):
        below=_hex_to_rgb(derive(edited.palette.model_dump())[band])
        actual=_own_fill(chip,edited,below)
        ink=_parse_color(chip.styles["color"],edited)
        assert contrast(_to_hex(ink[0]),_to_hex(actual))>=4.5


@pytest.mark.parametrize("painted,expected_role", [("#303a43","photo-ink"),("#ecece5","photo-ink-dark")])
def test_photo_foregrounds_and_scrims_stay_stable_without_rewriting_image_data(painted,expected_role):
    from app.services.palette_styles import bind_theme_styles, rendering_theme
    t=theme(); rendered=rendering_theme(t)
    grain='url("data:image/svg+xml,%3Csvg fill=%22%23ffffff%22/%3E")'
    photograph="url('https://images.example.test/photo.jpg#ffffff')"
    r,g,b=(int(rendered.palette.secondary[i:i+2],16) for i in (1,3,5))
    label=BuilderElement(id="copy",name="Body",type="text",styles={"color":"#ffffff"},content=BuilderElementContent(innerText="A measured photograph"))
    hero=BuilderElement(id="photo",name="Hero",type="container",styles={"backgroundImage":f'{grain}, linear-gradient(rgba({r},{g},{b},.6), transparent), {photograph}'},content=[label])
    hero.paint_surface(painted)
    bind_theme_styles([hero],t,rendered)
    assert grain in hero.styles["backgroundImage"]
    assert photograph in hero.styles["backgroundImage"]
    assert "--builder-color-photo-scrim" in hero.styles["backgroundImage"]
    assert expected_role in label.styles["color"]
    for edited in (t,theme(PALETTES[1])):
        d=derive(edited.palette.model_dump())
        assert contrast(d[expected_role],painted)>=4.5


@pytest.mark.asyncio
async def test_stale_preview_palette_save_cannot_overwrite_current_noncolor_settings(tmp_path,monkeypatch):
    from app.services import db,generation_projects
    monkeypatch.setattr(db,"DB_PATH",tmp_path/'stale-palette.db')
    monkeypatch.setattr(db,"_BOOTSTRAPPED",False)
    stale=site()
    current=site()
    current.theme=current_theme(current).model_copy(update={'typography':current_theme(current).typography.model_copy(update={'heading_font':'Owner heading font'})})
    current.builder_styles['typography']['headingFont']='Owner heading font'
    current.builder_styles['buttons']['radius']=23
    project=await generation_projects.create_project({},current)
    patch=palette_patch(stale,CreativePalette(**PALETTES[1]))
    await generation_projects.save_project_palette(project,patch,unpublished_only=True)
    stored=(await generation_projects.get_project(project))['preview_site']
    assert stored['theme']['typography']['heading_font']=='Owner heading font'
    assert stored['builder_styles']['typography']['headingFont']=='Owner heading font'
    assert stored['builder_styles']['buttons']['radius']==23
    assert stored['builder_styles']['colors']['primary']==PALETTES[1]['primary']


@pytest.mark.asyncio
async def test_separate_calls_keep_earlier_design_prompt_and_pin_noncolor_decisions(monkeypatch):
    from app.services.design_direction import DESIGN_PROMPT, PALETTE_PROMPT
    monkeypatch.setattr(settings, "ai_palette_enabled", True)
    monkeypatch.setattr(settings, "design_schemes_enabled", True)
    monkeypatch.setattr(settings, "design_language_enabled", True)
    model = Model(PALETTES[1])
    direction = await resolve_direction(brand_name="Calm Acorns", mood="friendly", industry="childcare",
        logo_hex="#c4775a", palette_design_mode="separate", audience="Parents", llm=model)
    assert len(model.calls) == 2
    assert model.calls[0]["system_prompt"] == DESIGN_PROMPT
    assert "Observed brand colors" not in model.calls[0]["user_prompt"]
    assert model.calls[1]["system_prompt"] == PALETTE_PROMPT
    assert "Parents" in model.calls[1]["user_prompt"]
    assert direction.theme.hero_background_height == "full"
    selected = direction.concepts[0].theme
    assert direction.theme.typography == selected.typography
    assert direction.theme.design_scheme == selected.design_scheme
    assert direction.theme.palette.accent == PALETTES[1]["accent"]


@pytest.mark.asyncio
async def test_manual_override_skips_separate_palette_call(monkeypatch):
    monkeypatch.setattr(settings, "ai_palette_enabled", True)
    monkeypatch.setattr(settings, "design_schemes_enabled", True)
    monkeypatch.setattr(settings, "design_language_enabled", True)
    model = Model(PALETTES[1])
    await resolve_direction(brand_name="Acorns", mood="friendly", industry="childcare", logo_hex=None,
        palette_design_mode="separate", generate_palette=False, llm=model)
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_separate_palette_failure_retains_complete_fallback(monkeypatch):
    monkeypatch.setattr(settings, "ai_palette_enabled", True)
    monkeypatch.setattr(settings, "design_schemes_enabled", True)
    monkeypatch.setattr(settings, "design_language_enabled", True)
    result = await resolve_direction(brand_name="Acorns", mood="friendly", industry="childcare", logo_hex=None,
        palette_design_mode="separate", llm=Model({"primary":"#abc"}))
    assert result.theme.palette.accent == result.concepts[0].theme.palette.accent
    assert result.theme.color_system_version == 2

@pytest.mark.asyncio
async def test_palette_only_response_cannot_change_design_or_hero(monkeypatch):
    monkeypatch.setattr(settings, "ai_palette_enabled", True)
    monkeypatch.setattr(settings, "design_schemes_enabled", True)
    monkeypatch.setattr(settings, "design_language_enabled", True)
    class ConflictingModel:
        calls = 0
        async def chat_json(self, **kwargs):
            self.calls += 1
            return kwargs['schema'].model_validate({
                'concept': 0 if self.calls == 1 else 2,
                'hero_height': 'banded' if self.calls == 1 else 'full',
                'palette': PALETTES[1],
            })
    result = await resolve_direction(brand_name="Acorns", mood="friendly", industry="childcare", logo_hex=None,
        palette_design_mode="separate", llm=ConflictingModel())
    assert result.theme.hero_background_height == 'banded'
    assert result.theme.design_scheme == result.concepts[0].theme.design_scheme


@pytest.mark.asyncio
async def test_palette_only_response_cannot_flip_light_dark_design():
    from app.services.llm import LlmError
    original = theme(PALETTES[0]); before = original.model_dump()
    with pytest.raises(LlmError, match="light/dark"):
        await regenerate_palette(original, {}, llm=Model(PALETTES[4]))
    assert original.model_dump() == before


@pytest.mark.parametrize("mode", ["shared", "separate"])
def test_generation_request_persists_mode(mode):
    from app.routers.generate import GenerateRequest
    request = GenerateRequest(source=SourceContent(source_kind="url", source_ref="https://example.test", raw_text="A business"),
                              palette_design_mode=mode)
    assert GenerateRequest.model_validate_json(request.model_dump_json()).palette_design_mode == mode
