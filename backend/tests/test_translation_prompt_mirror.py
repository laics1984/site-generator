"""
The builder's "Translate with AI" runs in webtree-cms-api, not here
(TranslationSuggester, builder docs/auto-translate-plan.md): the CMS owns the
drafts, the plan gate and the monthly AI pool. It translates a page's gaps
with THIS module's rules, so they are pinned from here, as
test_surface_ink_mirror pins the builder's ink:

- `TranslationSuggester::PROMPT` is `translations.TRANSLATION_PROMPT`, byte for
  byte. A prompt tuned here and not there would translate a page one way at
  generation and another in the builder.
- `MAX_SOURCE_CHARS` is `_MAX_SOURCE_CHARS`.
- `isUntranslatable` reads the same four patterns as `is_untranslatable` (a
  URL, an email, a phone number, a bare number), so neither side asks a model
  about a string the other copies as is.

Skips when the CMS checkout is not beside this one.
"""

from __future__ import annotations

import inspect
import re
import unittest
from pathlib import Path

from app.services import translations
from app.services.contact_text import EMAIL_RE, PHONE_RE

_WEBTREE = Path(__file__).resolve().parents[3]
_SUGGESTER = _WEBTREE / "webtree-cms-api" / "app" / "Services" / "Localization" / "TranslationSuggester.php"

# The two patterns is_untranslatable writes inline; the other two are
# contact_text's. Checked against the function's own source below.
_URL = r"(?:https?://|www\.)\S+"
_NUMBER = r"[\d\s.,:%+\-/()]+"


def _php(case: unittest.TestCase) -> str:
    if not _SUGGESTER.exists():
        case.skipTest(f"sibling repo absent: {_SUGGESTER}")
    return _SUGGESTER.read_text()


class TranslationPromptMirrorTest(unittest.TestCase):
    def test_the_cms_prompt_is_the_generators(self):
        match = re.search(r"PROMPT = <<<'PROMPT'\n(.*?)\nPROMPT;", _php(self), re.S)
        self.assertIsNotNone(match, "TranslationSuggester::PROMPT nowdoc not found")
        self.assertEqual(match.group(1), translations.TRANSLATION_PROMPT)

    def test_the_owners_words_are_capped_alike(self):
        match = re.search(r"MAX_SOURCE_CHARS = (\d+);", _php(self))
        self.assertIsNotNone(match, "TranslationSuggester::MAX_SOURCE_CHARS not found")
        self.assertEqual(int(match.group(1)), translations._MAX_SOURCE_CHARS)

    def test_both_copy_the_same_strings_without_asking(self):
        php = _php(self)
        body = php[php.index("function isUntranslatable"):]
        body = body[: body.index("\n    }\n")]
        patterns = re.findall(r"preg_match\('~\\A(.*?)\\z~u'", body)
        self.assertEqual(patterns, [_URL, EMAIL_RE.pattern, PHONE_RE.pattern, _NUMBER])

        source = inspect.getsource(translations.is_untranslatable)
        self.assertIn(f'r"{_URL}"', source)
        self.assertIn(f'r"{_NUMBER}"', source)
        self.assertIn("EMAIL_RE.fullmatch", source)
        self.assertIn("PHONE_RE.fullmatch", source)


if __name__ == "__main__":
    unittest.main()
