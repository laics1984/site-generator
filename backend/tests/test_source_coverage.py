"""The coverage report: what the Page said, and whether the site says it too.

Advisory and pure — it reads a finished site against its source. These pin
the comparisons it makes (a phone however it is spaced, an address however it
is split, a photo by its identity rather than its signed URL) and that every
usable photo is accounted for, placed or with a reason.
"""

from app.models.builder_schema import (
    BodySchema, BuilderElement, BuilderElementContent, GeneratedPage, GeneratedSite, PageSeo,
)
from app.models.content_blocks import ImageMetadata, SourceContent
from app.models.facebook import FacebookHours, FacebookPage, FacebookReview
from app.models.page_knowledge import Offering, PageKnowledge
from app.services.source_coverage import coverage_report

_PHOTO_A = "https://scontent.fbcdn.net/v/t39.30808-6/461111111_1111111111_n.jpg"
_PHOTO_B = "https://scontent.fbcdn.net/v/t39.30808-6/462222222_2222222222_n.jpg"
_POSTER = "https://scontent.fbcdn.net/v/t39.30808-6/463333333_3333333333_n.jpg"


def _text(text: str) -> BuilderElement:
    return BuilderElement(name="Text", type="text", content=BuilderElementContent(innerText=text))


def _image(src: str) -> BuilderElement:
    return BuilderElement(name="Image", type="image", content=BuilderElementContent(src=src))


def _site(*elements: BuilderElement, footer: list[BuilderElement] | None = None) -> GeneratedSite:
    return GeneratedSite(
        site_name="Kiddo",
        pages=[GeneratedPage(slug="", title="Home", is_homepage=True,
                             body_schema=BodySchema(elements=list(elements)), seo=PageSeo())],
        footer_schema=BuilderElement(name="Footer", type="__footer", content=footer or []),
    )


def _page(**kwargs) -> FacebookPage:
    return FacebookPage(name="Kiddo", canonical_url="https://www.facebook.com/kiddo",
                        fetched_via="render", **kwargs)


def _source(*metas: ImageMetadata) -> SourceContent:
    return SourceContent(source_kind="facebook", source_ref="https://www.facebook.com/kiddo",
                         raw_text="Kiddo", image_metadata=list(metas))


def _by_label(report, kind):
    return {item.label: item for item in report.items if item.kind == kind}


def test_facts_are_matched_however_the_site_formats_them():
    page = _page(phone="+60 3-1234 5678", emails=["Hello@Kiddo.my"],
                 single_line_address="12 Jalan Bunga, Subang Jaya, Selangor, Malaysia",
                 hours=[FacebookHours(day="Monday", opens="08:00", closes="18:00")],
                 website="https://www.kiddo.my")
    site = _site(
        _text("Call 03 1234 5678 or write to hello@kiddo.my"),
        _text("12 Jalan Bunga, Subang Jaya, Selangor"),
        footer=[BuilderElement(name="Link", type="link",
                               content=BuilderElementContent(href="https://kiddo.my/", innerText="Web"))],
    )
    facts = _by_label(coverage_report(_source(), site, page), "fact")
    assert {label for label, item in facts.items() if item.placed} == {
        "Phone", "Email", "Address", "Website"}
    assert facts["Opening hours"].placed is False
    assert facts["Opening hours"].reason == "not on any page"


def test_a_fact_the_page_never_stated_is_not_reported():
    report = coverage_report(_source(), _site(), _page(phone="012 345 6789"))
    assert set(_by_label(report, "fact")) == {"Phone"}


def test_every_usable_photo_is_accounted_for_by_identity():
    placed = ImageMetadata(url=f"{_PHOTO_A}?oe=AAA", selection_context="facebook")
    unplaced = ImageMetadata(url=_PHOTO_B, selection_context="facebook")
    poster = ImageMetadata(url=_POSTER, selection_context="facebook", ocr_has_text=True,
                           vision_kind="banner")
    # The site carries photo A under a different signature: the same picture.
    site = _site(_image(f"{_PHOTO_A}?oe=BBB"))
    report = coverage_report(_source(placed, unplaced, poster), site, _page())
    photos = _by_label(report, "photo")
    assert photos[placed.url].placed is True
    assert photos[unplaced.url].reason == "no slot left for it"
    assert "whole" in photos[poster.url].reason
    assert report.summary["photo"].read == 3 and report.summary["photo"].placed == 1


def test_a_review_counts_only_when_quoted():
    page = _page(reviews=[
        FacebookReview(text="My daughter loves the teachers and the reading corner.", author="Aisyah"),
        FacebookReview(text="Friendly staff, clean classrooms, great food.", author="Mei"),
    ])
    site = _site(_text("“My daughter loves the teachers and the reading corner.”"))
    reviews = _by_label(coverage_report(_source(), site, page), "review")
    assert reviews["Aisyah"].placed and not reviews["Mei"].placed


def test_an_offering_counts_when_the_site_names_it():
    source = _source()
    source.page_knowledge = PageKnowledge(offerings=[
        Offering(name="Nursery Programme"), Offering(name="Holiday Camp")], structured_by="llm")
    site = _site(_text("Our Nursery Programme welcomes toddlers"))
    offerings = _by_label(coverage_report(source, site, _page()), "offering")
    assert offerings["Nursery Programme"].placed and not offerings["Holiday Camp"].placed
    assert offerings["Holiday Camp"].reason == "not named on any page"
