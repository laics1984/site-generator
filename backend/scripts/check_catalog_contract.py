"""
Catalog contract guard. Validates every section template in the vendored
catalog so drift (e.g. a reintroduced grid leak) fails loudly.

For each template it checks:
  - fills with its own sampleContent without error
  - no element carries display:grid / gridTemplateColumns (builder owns the grid)
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
    content = node.get("content")
    if isinstance(content, list):
        for child in content:
            _used_slots(child, found)


def _invariants(el, errs: list[str]) -> None:
    styles = el.styles or {}
    if styles.get("display") == "grid":
        errs.append(f"display:grid on {el.name}")
    if "gridTemplateColumns" in styles:
        errs.append(f"gridTemplateColumns on {el.name}")
    if el.type not in KNOWN:
        errs.append(f"unknown type {el.type}")
    if isinstance(el.content, list):
        for child in el.content:
            _invariants(child, errs)


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
            if slot["id"] not in sample:
                errs.append(f"required slot '{slot['id']}' missing from sampleContent")

        try:
            el = await fill_template(
                template, sample, resolve_image=_stub_image, content_factories=factories
            )
            _invariants(el, errs)
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
