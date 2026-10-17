"""Unresolved optional imagery must not reserve an empty featured-image frame."""
import pytest
from app.services.template_filler import fill_template

@pytest.mark.asyncio
@pytest.mark.parametrize("value", [{"query": None}, {"icon": "unknown-glyph"}, {"src": " "}, {"query": "missing"}])
async def test_unresolved_image_and_empty_frame_are_pruned(value):
    async def resolve(query):
        return "", None
    template = {"id":"test", "tree":{"type":"section", "name":"Section", "styles":{}, "content":[
        {"type":"container", "name":"Frame", "styles":{"minHeight":"320px"}, "content":[{"type":"image", "name":"Image", "styles":{}, "$slot":"image"}]},
        {"type":"text", "name":"Heading", "styles":{}, "$slot":"heading"}]}}
    result = await fill_template(template, {"image":value,"heading":"Our services"}, resolve_image=resolve)
    assert len(result.content) == 1
    assert result.content[0].type == "text"

@pytest.mark.asyncio
async def test_valid_image_is_retained():
    async def resolve(query):
        return "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'/>", None
    result = await fill_template({"id":"test","tree":{"type":"image", "name":"Image", "styles":{},"$slot":"image"}},
        {"image":{"query":"Service photo"}}, resolve_image=resolve)
    assert result.content.src.startswith("data:image/")

@pytest.mark.asyncio
async def test_catalog_about_image_split_collapses_visual_when_source_is_empty():
    from app.services.template_filler import get_template
    template = get_template('about-image-split')
    async def resolve(query):
        return '', None
    result = await fill_template(template, {**template['sampleContent'], 'image': {'query':None}}, resolve_image=resolve)
    def walk(node):
        yield node
        if isinstance(node.content,list):
            for child in node.content:
                yield from walk(child)
    assert not any(n.type == 'image' or n.name == 'About Visual' for n in walk(result))
    assert any(n.type == 'text' for n in walk(result))
