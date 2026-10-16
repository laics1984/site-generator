"""Same-page bilingual presentation (docs/multilingual-plan.md §3, mode B).

The owner chose to show both languages together — "Programmes | 课程" — so the
page keeps them together: under each string the primary language shows, the
same string in the second language, as its own line tagged `lang`.

The translations arrive as a string map on the page
(`PagePlan.inline_translation`, built by `translations.add_inline_translations`
from the same clone-and-translate a separate-language page uses). Matching on
the built tree's own strings, rather than building the page twice and walking
both, is what makes this one pass over one tree: no second render, no second
image resolution, and nothing to drift. A string a mapper changed on the way
(a stripped list number) simply finds no translation and stays monolingual —
a missing line, never a wrong one.

Rules:
- **Copy only.** Text elements gain a line; links and buttons keep one label,
  since one control takes one action.
- **A split headline is one string.** Its lead and accent lines join (with a
  space) to what the planner wrote, so the GROUP is matched and the line goes
  after the group, never between its halves.
- **The second line is never a heading.** It carries no `htmlTag` (one `<h1>`
  per page, in the page's language) and a name outside every title vocabulary;
  it copies the original's styles, so both languages read at equal weight —
  how the source presents them — and every ink pass that runs after this one
  inks it for its surface like any other copy.
"""

from __future__ import annotations

from app.models.builder_schema import BuilderElement, BuilderElementContent
from app.models.content_blocks import InlineTranslation

# Outside `schema_builder._TITLE_NAMES`, the renderer's pinned-name sets and
# every catalog name, on purpose: nothing keyed on a node name may treat the
# second-language line as the thing it translates.
SECOND_LANGUAGE_NAME = "Second language"


def _text_of(element: BuilderElement) -> str | None:
    content = element.content
    if element.type == "text" and isinstance(content, BuilderElementContent):
        return (content.innerText or "").strip() or None
    return None


def _joined_lines(element: BuilderElement) -> tuple[str, BuilderElement] | None:
    """(the whole string, its first line) for a container of text lines only —
    a split headline's group."""
    content = element.content
    if element.type != "container" or not isinstance(content, list) or len(content) < 2:
        return None
    texts = [_text_of(child) for child in content]
    if any(text is None for text in texts):
        return None
    return " ".join(texts), content[0]  # type: ignore[arg-type]


# Quotation marks a mapper wraps around a string (a testimonial's “…”): the
# planner's string is inside them, and the second line wears the same marks.
_QUOTES = "\"'“”‘’「」『』«»"


def _lookup(text: str, mapping: dict[str, str]) -> str | None:
    if text in mapping:
        return mapping[text]
    inner = text.strip(_QUOTES).strip()
    if inner != text and inner in mapping:
        lead = text[: len(text) - len(text.lstrip(_QUOTES))]
        tail = text[len(text.rstrip(_QUOTES)):]
        return f"{lead}{mapping[inner]}{tail}"
    return None


def _second_line(original: BuilderElement, text: str, lang: str) -> BuilderElement:
    return BuilderElement(
        name=SECOND_LANGUAGE_NAME,
        type="text",
        styles=dict(original.styles),
        classes=original.classes,
        responsiveStyles=(
            original.responsiveStyles.model_copy(deep=True) if original.responsiveStyles else None
        ),
        content=BuilderElementContent(innerText=text),
        lang=lang,
    )


def _walk(children: list[BuilderElement], mapping: dict[str, str], lang: str) -> int:
    added = 0
    out: list[BuilderElement] = []
    for child in children:
        out.append(child)
        own = _text_of(child)
        joined = _joined_lines(child)
        own_line = _lookup(own, mapping) if own is not None else None
        joined_line = _lookup(joined[0], mapping) if joined is not None else None
        if own_line is not None:
            out.append(_second_line(child, own_line, lang))
            added += 1
        elif joined is not None and joined_line is not None:
            out.append(_second_line(joined[1], joined_line, lang))
            added += 1
        elif isinstance(child.content, list):
            added += _walk(child.content, mapping, lang)
    children[:] = out
    return added


def apply_inline_translation(
    elements: list[BuilderElement], inline: InlineTranslation | None
) -> int:
    """Add the second-language line after every string `inline` translates.

    Mutates `elements` in place; returns how many lines were added."""
    if inline is None or not inline.text:
        return 0
    added = 0
    for element in elements:
        if isinstance(element.content, list):
            added += _walk(element.content, inline.text, inline.lang)
    return added
