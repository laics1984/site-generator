"""A single-page source earns a Programmes / Services page from its offerings.

The gate (`earned_offerings`), the scaffolds (`offering_scaffolds`) and the
planning view each earned page is written from (`offering_source`) are one
module, so a page is offered exactly when it can be filled — and filled from
its own posts, never from the whole digest.
"""

from app.models.content_blocks import ImageMetadata, SourceContent
from app.models.industry import PageScaffold
from app.models.page_knowledge import Offering, PageKnowledge
from app.services.offering_pages import earned_offerings, offering_scaffolds, offering_source
from app.services.page_inference import evidence_pages, infer_page_scaffolds
from app.services.source_router import match_scaffolds_to_pages

_NURSERY_BODY = "Our Nursery Programme welcomes toddlers from two years old. " * 6


def _offering(name, body="A programme the Page describes.", photo_ids=(), own_chars=0):
    return Offering(name=name, kind="programme", body=body, photo_ids=list(photo_ids),
                    own_chars=own_chars, link="https://www.facebook.com/p/1",
                    published_at="2026-10-01T00:00:00+0000")


def _source(*offerings, photos=()):
    return SourceContent(
        source_kind="facebook", source_ref="https://www.facebook.com/kiddo", title="Kiddo",
        raw_text="Kiddo — Kindergarten\nAbout: the whole digest, every post and every fact.",
        image_metadata=[ImageMetadata(url=url, width=1600, height=1000, role="content")
                        for url in photos],
        page_knowledge=PageKnowledge(offerings=list(offerings), structured_by="llm"),
    )


def test_two_offerings_earn_a_programmes_page_and_one_does_not():
    two = _source(_offering("Nursery Programme"), _offering("Preschool Programme"))
    one = _source(_offering("Nursery Programme"))
    assert [p.slug for p in evidence_pages(two, "childcare")] == ["programmes"]
    assert evidence_pages(one, "childcare") == []
    assert earned_offerings(one) == []


def test_anything_but_childcare_earns_a_services_page():
    source = _source(_offering("Aircon servicing"), _offering("Chemical wash"))
    assert [(p.slug, p.title) for p in offering_scaffolds(source, "home-services")] == [
        ("services", "Services")]


def test_an_offering_gets_a_page_of_its_own_only_with_its_own_evidence_and_photo():
    photo = "https://cdn.example/nursery.jpg"
    rich = _offering("Nursery Programme", body=_NURSERY_BODY, photo_ids=[photo], own_chars=len(_NURSERY_BODY))
    no_photo = _offering("Preschool Programme", body=_NURSERY_BODY, own_chars=len(_NURSERY_BODY))
    thin = _offering("Holiday Camp", photo_ids=[photo], own_chars=40)
    source = _source(rich, no_photo, thin, photos=[photo])
    scaffolds = offering_scaffolds(source, "childcare")
    assert [(p.slug, p.parent_slug) for p in scaffolds] == [
        ("programmes", None), ("programmes/nursery-programme", "programmes")]


def test_the_earned_page_is_planned_from_its_own_offerings_alone():
    photo = "https://cdn.example/nursery.jpg"
    other = "https://cdn.example/unrelated.jpg"
    source = _source(_offering("Nursery Programme", photo_ids=[photo]),
                     _offering("Preschool Programme"), photos=[photo, other])
    scaffolds = infer_page_scaffolds(source, industry="childcare", single_page=True)
    routed = match_scaffolds_to_pages(scaffolds, source)
    view = routed["programmes"]
    assert "the whole digest" not in view.raw_text
    assert "Nursery Programme:\nA programme the Page describes." in view.raw_text
    assert [m.url for m in view.image_metadata] == [photo]
    [tree] = view.section_candidates
    assert tree.card_kind == "offerings"
    assert [(c.title, c.image_url) for c in tree.cards] == [
        ("Nursery Programme", photo), ("Preschool Programme", None)]
    # The homepage still reads the whole digest.
    assert routed[""] is source


def test_a_scaffold_that_is_not_an_earned_page_keeps_its_routing():
    source = _source(_offering("Nursery Programme"), _offering("Preschool Programme"))
    about = PageScaffold(page_type="about", slug="about", title="About", sections=["hero", "about"])
    assert offering_source(source, about) is None
    assert match_scaffolds_to_pages([about], source)["about"] is source


def test_a_photo_no_longer_in_the_pool_is_not_offered():
    gone = "https://cdn.example/expired.jpg"
    source = _source(_offering("Nursery Programme", photo_ids=[gone]), _offering("Preschool Programme"))
    view = offering_source(source, offering_scaffolds(source, "childcare")[0])
    assert view.image_metadata == [] and view.section_candidates[0].cards[0].image_url is None
