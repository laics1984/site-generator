"""CMS owns category values/labels; generator artifacts and prompts cannot drift."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from typing import get_args

from app.models.industry import IndustryCategory
from app.models.industry_catalog import INDUSTRY_LABELS
from app.services.industry_templates import TEMPLATES, all_industries_summary
from app.services.prompts import DETECT_BRAND_PROMPT, LEGACY_SYSTEM_PROMPT, _SCAFFOLD_PROMPT_HEAD
from scripts.sync_industries import SOURCE, generated_files, load_catalog


class IndustryTaxonomyTest(unittest.TestCase):
    def test_generated_vocabulary_and_types_match_the_cms_source(self):
        if not SOURCE.exists():
            self.skipTest("CMS source not checked out beside the generator; run sync_industries.py --check at release")
        catalog = load_catalog(SOURCE)
        self.assertEqual(INDUSTRY_LABELS, catalog)
        for path, expected in generated_files(catalog).items():
            self.assertEqual(path.read_text(), expected, f"{path}: run scripts/sync_industries.py")

    def test_every_industry_has_a_page_template(self):
        self.assertEqual(set(TEMPLATES), set(get_args(IndustryCategory)))
        self.assertEqual(list(get_args(IndustryCategory)), list(INDUSTRY_LABELS))

    def test_template_and_dropdown_labels_are_the_catalog_labels(self):
        self.assertEqual(
            [(row["id"], row["label"]) for row in all_industries_summary()],
            list(INDUSTRY_LABELS.items()),
        )
        for slug, template in TEMPLATES.items():
            self.assertEqual(template.label, INDUSTRY_LABELS[slug])

    def test_all_generation_prompts_use_the_complete_category_vocabulary(self):
        for prompt in (DETECT_BRAND_PROMPT, _SCAFFOLD_PROMPT_HEAD):
            choices = re.search(r'"industry_category":\s*([^\n]+)', prompt).group(1)
            self.assertEqual(re.findall(r'"([a-z-]+)"', choices), list(INDUSTRY_LABELS))
            self.assertNotIn("__INDUSTRY", prompt)
        self.assertIn("Pick `industry_category` from: " + ", ".join(INDUSTRY_LABELS) + ".", LEGACY_SYSTEM_PROMPT)

    def test_catalog_rejects_duplicate_or_invalid_entries(self):
        malformed = [
            '{"other":"Other","other":"Something else"}',
            '{"other":"Other","Bad slug":"Bad"}',
            '{"other":""}',
            '{"agency":"Agency"}',
            '[]',
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "industries.json"
            for raw in malformed:
                path.write_text(raw)
                with self.subTest(raw=raw), self.assertRaises(ValueError):
                    load_catalog(path)
