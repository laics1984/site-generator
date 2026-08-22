"""
Catalog contract guard. Validates every section template in the vendored
catalog so drift (e.g. a reintroduced grid leak) fails loudly.

For each template it checks:
  - fills with its own sampleContent without error
  - no element carries display:grid / gridTemplateColumns (builder owns the grid),
    EXCEPT a `$bento` fan-out container — the one sanctioned exception, since a
    bento's mixed-size tiles cannot be expressed with 2Col/3Col
  - every element type is a known EditorBtns
  - every $slot / $styleSlot used in the tree is declared in `slots`
  - every required (non-optional) slot has sample content

Run:  python3 scripts/check_catalog_contract.py    (exit 1 on any failure)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.template_filler import fill_template, load_catalog

KNOWN = {"text", "container", "section", "2Col", "3Col", "image", "video", "link",
         "menu", "contactForm", "paymentForm", "__body", "__header", "__footer"}


async def _stub_image(query: str):
    return f"https://images.example/{query.replace(' ', '-')}.jpg", "#5a5a5a"


def _declared_slots(template: dict) -> set[str]:
    out: set[str] = set()

    def walk(slots: list) -> None:
        for slot in slots:
            out.add(slot["id"])
            if slot.get("item"):
                walk(slot["item"])

    walk(template.get("slots", []))
    return out


def _used_slots(node: dict, found: set[str]) -> None:
    if node.get("$slot"):
        found.add(node["$slot"])
    if node.get("$styleSlot"):
        found.add(node["$styleSlot"]["slot"])
    if node.get("$repeat"):
        found.add(node["$repeat"])
    if node.get("$bento"):
        found.add(node["$bento"])
    content = node.get("content")
    if isinstance(content, list):
        for child in content:
            _used_slots(child, found)


def _bento_names(node: dict, found: set[str]) -> None:
    """Names of `$bento` fan-out containers in a template tree.

    The grid ban is enforced on the MATERIALIZED element tree, where the
    `$bento` directive has already been consumed, so the exemption is carried
    across by node name (which `_base_fields` copies through verbatim).
    """
    if node.get("$bento"):
        found.add(node["name"])
    content = node.get("content")
    if isinstance(content, list):
        for child in content:
            _bento_names(child, found)


def _invariants(el, errs: list[str], bento: set[str]) -> None:
    styles = el.styles or {}
    # A bento container owns its grid: mixed-size tiles (`_bento_spans`) cannot
    # be expressed with the builder's 2Col/3Col primitives. Every other element
    # must leave the grid to the builder.
    if el.name not in bento:
        if styles.get("display") == "grid":
            errs.append(f"display:grid on {el.name}")
        if "gridTemplateColumns" in styles:
            errs.append(f"gridTemplateColumns on {el.name}")
    if el.type not in KNOWN:
        errs.append(f"unknown type {el.type}")
    if isinstance(el.content, list):
        for child in el.content:
            _invariants(child, errs, bento)


# Slot kinds that carry no scalar value, so `sampleContent` cannot describe them.
_UNSAMPLEABLE_SLOT_KINDS = frozenset({"subtree", "flag"})


async def main() -> int:
    catalog = load_catalog()
    factories = {"contactFormDefault": lambda: {}}
    failures = 0
    print(f"{'template':28} status")
    print("-" * 60)
    for template in catalog["sections"]:
        errs: list[str] = []

        declared = _declared_slots(template)
        used: set[str] = set()
        _used_slots(template["tree"], used)
        undeclared = used - declared
        if undeclared:
            errs.append(f"slots used but not declared: {sorted(undeclared)}")

        sample = template.get("sampleContent", {})
        for slot in template.get("slots", []):
            if slot.get("optional"):
                continue
            # A `subtree` slot is a whole BuilderElement tree the caller injects
            # (header_footer._logo_mark builds the brand lockup), and a `flag` is
            # a $if condition, not content. Neither is a scalar sampleContent
            # value, so demanding one flagged all five chrome headers for a
            # `logo` they cannot possibly sample — the rule was written for
            # text/link/image/list slots and never qualified.
            if slot.get("kind") in _UNSAMPLEABLE_SLOT_KINDS:
                continue
            if slot["id"] not in sample:
                errs.append(f"required slot '{slot['id']}' missing from sampleContent")

        try:
            el = await fill_template(
                template, sample, resolve_image=_stub_image, content_factories=factories
            )
            bento: set[str] = set()
            _bento_names(template["tree"], bento)
            _invariants(el, errs, bento)
        except Exception as exc:  # noqa: BLE001
            errs.append(f"fill error: {exc}")

        if errs:
            failures += 1
        print(f"{template['id']:28} {'ok' if not errs else '; '.join(errs)}")

    print("-" * 60)
    print(f"{len(catalog['sections'])} templates, "
          f"{'CONTRACT OK' if not failures else str(failures) + ' FAILURES'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
