"""Framed images take the creative direction's shape — by catalog node name.

`SHAPED_IMAGE_NAMES` is a vocabulary over the section catalog; a renamed node
would silently switch the shape off (the h1 pass's title-name trap), so the
catalog is walked here."""

from __future__ import annotations

import json
from pathlib import Path

from app.models.builder_schema import BuilderElement, BuilderElementContent
from app.services.schema_builder import apply_image_shape
from app.services.style_tokens import IMAGE_SHAPES, SHAPED_IMAGE_NAMES

_CATALOG = Path(__file__).resolve().parents[1] / "app" / "templates" / "section_catalog.json"


def _image_names(node, in_repeat=False, out=None):
    out = out if out is not None else set()
    if isinstance(node, dict):
        in_repeat = in_repeat or "$repeat" in node
        if node.get("type") == "image" and not in_repeat:
            out.add(node.get("name"))
        for value in node.values():
            _image_names(value, in_repeat, out)
    elif isinstance(node, list):
        for value in node:
            _image_names(value, in_repeat, out)
    return out


def test_every_shaped_name_is_a_real_framed_image_in_the_catalog() -> None:
    sections = json.loads(_CATALOG.read_text())["sections"]
    names = set()
    for section in sections:
        if section.get("sectionType") in ("hero", "about"):
            names |= _image_names(section)
    assert SHAPED_IMAGE_NAMES <= names, SHAPED_IMAGE_NAMES - names


def test_shapes_are_border_radius_only() -> None:
    for shape, radius in IMAGE_SHAPES.items():
        assert radius is None or ("clip" not in radius and "url(" not in radius), shape


def _tree() -> list[BuilderElement]:
    hero_photo = BuilderElement(name="Hero Image", type="image", styles={"borderRadius": "28px"},
                                content=BuilderElementContent(src="https://x/h.jpg"))
    card_photo = BuilderElement(name="Card Image", type="image", styles={"borderRadius": "16px"},
                                content=BuilderElementContent(src="https://x/c.jpg"))
    return [BuilderElement(name="Hero - Split", type="container", content=[hero_photo, card_photo])]


def test_the_direction_shape_lands_on_framed_images_only() -> None:
    tree = _tree()
    apply_image_shape(tree, "arch")
    hero, card = tree[0].content
    assert hero.styles["borderRadius"] == IMAGE_SHAPES["arch"]
    assert card.styles["borderRadius"] == "16px"


def test_soft_rect_and_no_direction_keep_the_template_radius() -> None:
    for shape in ("soft-rect", None, "unknown"):
        tree = _tree()
        apply_image_shape(tree, shape)
        assert tree[0].content[0].styles["borderRadius"] == "28px"
