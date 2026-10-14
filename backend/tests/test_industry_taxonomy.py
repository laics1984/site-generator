"""The site industry taxonomy is one slug set across two repos.

The generator classifies a site (`IndustryCategory`) and the push stores that
slug on the CMS entity, which validates it against
`webtree-cms-api/config/benchmarks.php`. A slug on one side only is a site
whose industry is silently dropped on push (or one the generator can never
produce), so the two sets must be equal. Skipped when the CMS repo is not
checked out beside this one — the `BlankTemplateBodyTest` idiom.
"""

from __future__ import annotations

import pathlib
import re
import unittest
from typing import get_args

from app.models.industry import IndustryCategory
from app.services.industry_templates import TEMPLATES

_CMS_CONFIG = (
    pathlib.Path(__file__).resolve().parents[3] / "webtree-cms-api" / "config" / "benchmarks.php"
)


def _cms_industry_slugs(php: str) -> set[str]:
    start = php.index("'industries' => [") + len("'industries' => [")
    block = php[start : php.index("],", start)]
    return set(re.findall(r"'([a-z-]+)'\s*=>", block))


class IndustryTaxonomyTest(unittest.TestCase):
    def test_the_cms_accepts_exactly_the_generators_industries(self):
        if not _CMS_CONFIG.exists():
            self.skipTest("webtree-cms-api not checked out beside this repo")
        self.assertEqual(
            _cms_industry_slugs(_CMS_CONFIG.read_text()), set(get_args(IndustryCategory))
        )

    def test_every_industry_has_a_page_template(self):
        # The override dropdown lists TEMPLATES; an industry without one can be
        # detected but never chosen.
        self.assertEqual(set(TEMPLATES), set(get_args(IndustryCategory)))


if __name__ == "__main__":
    unittest.main()
