from app.models.content_blocks import NavLink, SourceContent
from app.services.whatsapp_discovery import (
    build_whatsapp_widget,
    discover_whatsapp_number,
    whatsapp_number_from_href,
)


def _source(**overrides) -> SourceContent:
    base = {
        "source_kind": "url",
        "source_ref": "https://acme.test/",
        "raw_text": "",
        "links": [],
        "social_links": [],
    }
    base.update(overrides)
    return SourceContent(**base)


class TestNumberFromHref:
    def test_wa_me_path_is_the_number(self):
        assert whatsapp_number_from_href("https://wa.me/60123456789") == "60123456789"
        assert whatsapp_number_from_href("https://www.wa.me/+60123456789") == "60123456789"

    def test_send_endpoints_carry_it_in_the_query(self):
        assert (
            whatsapp_number_from_href("https://api.whatsapp.com/send?phone=60123456789&text=hi")
            == "60123456789"
        )
        assert whatsapp_number_from_href("whatsapp://send?phone=60123456789") == "60123456789"

    def test_punctuation_and_access_codes_normalize(self):
        assert whatsapp_number_from_href("https://wa.me/+60 12-345 6789") == "60123456789"
        assert (
            whatsapp_number_from_href("https://api.whatsapp.com/send?phone=0060123456789")
            == "60123456789"
        )

    def test_a_number_with_no_country_code_is_refused(self):
        # The whole point of the module: a guessed country code opens a chat
        # with a stranger.
        assert whatsapp_number_from_href("https://api.whatsapp.com/send?phone=0123456789") is None
        assert whatsapp_number_from_href("https://wa.me/012345") is None

    def test_non_chat_urls_are_not_numbers(self):
        assert whatsapp_number_from_href("https://whatsapp.com/download") is None
        assert whatsapp_number_from_href("https://wa.me/qr/ABCDEF123456") is None
        assert whatsapp_number_from_href("https://facebook.com/acme") is None
        assert whatsapp_number_from_href("tel:+60123456789") is None
        assert whatsapp_number_from_href("") is None
        assert whatsapp_number_from_href(None) is None


class TestDiscovery:
    def test_a_social_button_is_the_strongest_claim(self):
        source = _source(
            social_links=[
                NavLink(label="Facebook", href="https://facebook.com/acme"),
                NavLink(label="WhatsApp", href="https://wa.me/60123456789"),
            ],
            links=["https://wa.me/60999999999"],
        )

        assert discover_whatsapp_number(source) == "60123456789"

    def test_it_falls_back_to_an_ordinary_link_then_to_prose(self):
        assert discover_whatsapp_number(
            _source(links=["/about", "https://api.whatsapp.com/send?phone=60123456789"])
        ) == "60123456789"

        assert discover_whatsapp_number(
            _source(raw_text="Message us any time on wa.me/60123456789 — we reply fast.")
        ) == "60123456789"

    def test_the_page_the_owner_gave_us_wins_over_a_crawled_sub_page(self):
        # A number three pages deep is as likely to belong to a partner or an
        # author as to the business.
        source = _source(
            links=["https://wa.me/60111111111"],
            discovered_pages=[_source(url_path="/team", links=["https://wa.me/60222222222"])],
        )

        assert discover_whatsapp_number(source) == "60111111111"

    def test_a_sub_page_still_counts_when_the_landing_page_has_none(self):
        source = _source(
            discovered_pages=[_source(url_path="/contact", links=["https://wa.me/60222222222"])]
        )

        assert discover_whatsapp_number(source) == "60222222222"

    def test_a_site_with_no_whatsapp_yields_nothing(self):
        source = _source(
            raw_text="Call us on 03-1234 5678.",
            links=["tel:+60312345678", "mailto:hi@acme.test"],
            social_links=[NavLink(label="Facebook", href="https://facebook.com/acme")],
        )

        assert discover_whatsapp_number(source) is None
        assert discover_whatsapp_number(None) is None


class TestWidgetPayload:
    def test_nothing_discovered_means_no_widget(self):
        assert build_whatsapp_widget(None) is None
        assert build_whatsapp_widget("") is None

    def test_a_discovered_number_ships_enabled(self):
        # The source site already published this number as a contact button, so
        # the generated site carrying it is continuity, not a new disclosure.
        widget = build_whatsapp_widget("60123456789", site_name="Acme Dental")

        assert widget == {
            "enabled": True,
            "phone": "60123456789",
            "displayMode": "labeled",
            "label": "Chat with us",
            "position": "bottom-right",
            "prefill": {
                "message": "Hi Acme Dental! I found you on your website and would like to know more.",
                "includePageUrl": False,
            },
        }

    def test_it_stays_sane_without_a_site_name(self):
        widget = build_whatsapp_widget("60123456789", site_name="  ")

        assert widget is not None
        assert widget["prefill"]["message"].startswith("Hi! I found you")
