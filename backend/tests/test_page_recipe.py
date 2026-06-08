import unittest

from app.models.industry import PageScaffold
from app.services.page_inference import core_pages_not_in_inferred


class PageRecipeTest(unittest.TestCase):
    def test_semantic_inferred_page_suppresses_template_core_slug_variant(self):
        core = [
            PageScaffold(page_type="home", slug="", title="Home", is_homepage=True, sections=[]),
            PageScaffold(page_type="about", slug="about", title="About", sections=[]),
            PageScaffold(page_type="contact", slug="contact", title="Contact", sections=[]),
            PageScaffold(page_type="privacy", slug="privacy", title="Privacy", sections=[], is_legal=True),
            PageScaffold(page_type="terms", slug="terms", title="Terms", sections=[], is_legal=True),
        ]
        inferred = [
            PageScaffold(page_type="home", slug="", title="Home", is_homepage=True, sections=[]),
            PageScaffold(page_type="about", slug="about-us", title="About Us", sections=[]),
            PageScaffold(page_type="contact", slug="contact-us", title="Contact Us", sections=[]),
        ]

        result = core_pages_not_in_inferred(core, inferred)

        self.assertEqual([page.slug for page in result], ["privacy", "terms"])


if __name__ == "__main__":
    unittest.main()
