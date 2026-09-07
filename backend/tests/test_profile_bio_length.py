"""A bio is bounded, and the bound never cuts a word in half.

`BIO_MAX_LEN` is a safety rail: `_extract_profile_bio` keeps every qualifying
line inside a card's container, so a mis-detected container (a whole column, a
whole section) would otherwise make the page's body text somebody's biography.

It was 480 and applied as a bare character slice in TWO places — a literal in
`scraper._extract_profile_bio` and `_BIO_MAX_LEN` in `clean_team_bio`. On
watr.org.my three of four people lost 58-65% of their story and the card's
"Show more" expanded to a half-word ("...para-medical st"); the fourth, whose
bio was 316 characters, came through whole, which is what pinned the cap as the
cause rather than the renderer.
"""

import unittest

from app.services.profile_text import BIO_MAX_LEN, clean_team_bio, truncate_bio


class TruncateBioTest(unittest.TestCase):
    def test_text_under_the_cap_is_returned_verbatim(self):
        # The common case, and the one that matters most: a bio the source
        # published short must arrive byte-identical, ellipsis included if the
        # author wrote one.
        for text in ("", "Short.", "A bio with no full stop", "x" * BIO_MAX_LEN):
            with self.subTest(text[:20]):
                self.assertEqual(truncate_bio(text), text)

    def test_a_long_bio_is_cut_at_a_sentence_end(self):
        text = ("She counsels families across the region. " * 200)

        bio = truncate_bio(text)

        self.assertLessEqual(len(bio), BIO_MAX_LEN)
        self.assertTrue(bio.endswith("region."), bio[-40:])
        # A sentence end already reads as a finished thought; punctuating it
        # would state a truncation the reader cannot see.
        self.assertFalse(bio.endswith("…"))

    def test_a_cut_with_no_late_sentence_end_lands_between_words(self):
        # One early full stop then unbroken prose: cutting at that stop would
        # throw away far more than the cap asks for, so a word boundary wins.
        text = "Short opening. " + ("interminable " * 400)

        bio = truncate_bio(text)

        self.assertLessEqual(len(bio), BIO_MAX_LEN)
        self.assertTrue(bio.endswith("…"), bio[-30:])
        # The property that actually matters: no half-word.
        self.assertTrue(bio[:-1].rstrip().endswith("interminable"), bio[-30:])

    def test_a_cut_never_exceeds_the_cap_or_returns_nothing(self):
        for text in (
            "word " * 2000,
            "nospacesatall" * 500,
            "Sentence. " * 500,
            "\n".join(f"Line number {i} of a directory card." for i in range(300)),
        ):
            with self.subTest(text[:16]):
                bio = truncate_bio(text)
                self.assertLessEqual(len(bio), BIO_MAX_LEN + 1)  # +1 for the ellipsis
                self.assertTrue(bio.strip())

    def test_clean_team_bio_applies_the_same_bound(self):
        # The downstream cleaner and the extractor must agree, or a bio that
        # survived extraction gets cut a second time by a different rule.
        bio = clean_team_bio("She counsels families across the region. " * 200)

        self.assertLessEqual(len(bio), BIO_MAX_LEN)
        self.assertTrue(bio.endswith("region."), bio[-40:])

    def test_a_three_paragraph_biography_fits(self):
        # The shape this exists for: a real professional bio, the length
        # watr.org.my's actually are (1385, 1207, 1149 characters).
        paragraph = "Aisha has led community programmes for many years. " * 10
        text = "\n".join([paragraph] * 3)
        self.assertGreater(len(text), 1385)

        self.assertEqual(truncate_bio(text), text)
