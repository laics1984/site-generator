"""Facts two combined sources disagree on are reported, never resolved behind
the user's back (services/source_conflicts.py)."""

from __future__ import annotations

import io
import unittest
from unittest import mock

from app.config import settings
from app.models.content_blocks import SourceContent
from app.services.contact_text import phone_key, phones_in
from app.services.source_merge import merge_sources

WEBSITE = SourceContent(
    source_kind="url", source_ref="https://acorns.my",
    raw_text="Call us on +60 3-1234 5678 or write to hello@acorns.my",
)


def _source(kind: str, text: str) -> SourceContent:
    return SourceContent(source_kind=kind, source_ref=f"{kind}-ref", raw_text=text)


class ConflictTest(unittest.TestCase):
    def test_a_different_number_is_a_conflict_and_the_first_sources_value_stands(self):
        merged = merge_sources(WEBSITE, _source("facebook", "Phone: 03-9876 5432"))
        [conflict] = merged.source_conflicts
        self.assertEqual(conflict.field, "phone")
        self.assertEqual(conflict.kept, "+60 3-1234 5678")
        self.assertEqual(
            [(v.source, v.value) for v in conflict.values],
            [("website", "+60 3-1234 5678"), ("Facebook Page", "03-9876 5432")],
        )

    def test_one_number_written_two_ways_is_not_a_conflict(self):
        merged = merge_sources(WEBSITE, _source("pdf", "Tel: 03-1234 5678"))
        self.assertEqual(merged.source_conflicts, [])

    def test_sharing_any_value_is_agreement(self):
        both = _source("docx", "Office 03-1234 5678, hotline 03-5555 0000")
        self.assertEqual(merge_sources(WEBSITE, both).source_conflicts, [])

    def test_emails_compare_without_case(self):
        merged = merge_sources(WEBSITE, _source("paste", "Email HELLO@ACORNS.MY"))
        self.assertEqual(merged.source_conflicts, [])
        other = merge_sources(WEBSITE, _source("paste", "Email admin@other.my"))
        self.assertEqual([c.field for c in other.source_conflicts], ["email"])

    def test_a_source_silent_on_a_fact_cannot_contradict_it(self):
        self.assertEqual(merge_sources(WEBSITE, _source("pdf", "No contact details.")).source_conflicts, [])

    def test_conflicts_survive_a_second_merge(self):
        once = merge_sources(WEBSITE, _source("facebook", "Phone: 03-9876 5432"))
        twice = merge_sources(once, _source("paste", "Opening hours 8am-6pm"))
        self.assertEqual(len(twice.source_conflicts), 1)


class ContactTextTest(unittest.TestCase):
    def test_short_digit_runs_are_not_phones(self):
        self.assertEqual(phones_in("Founded 1998, fees RM 450, 123-45"), [])
        self.assertEqual(phones_in("call 012-345 6789 today"), ["012-345 6789"])

    def test_the_key_ignores_country_code_and_punctuation(self):
        self.assertEqual(phone_key("+60 3-1234 5678"), phone_key("(03) 1234-5678"))


class ScannedPdfTest(unittest.TestCase):
    """A PDF with no text layer is read with OCR instead of refused."""

    def setUp(self):
        from app.services import text_detection

        if not text_detection._engine():
            self.skipTest("rapidocr-onnxruntime not installed")

    @staticmethod
    def _scan(lines: list[str]) -> bytes:
        import fitz
        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 34)
        except OSError:
            font = ImageFont.load_default(size=34)
        for i, line in enumerate(lines):
            draw.text((100, 150 + i * 80), line, fill="black", font=font)
        buf = io.BytesIO()
        image.save(buf, "PNG")
        pdf = fitz.open()
        pdf.new_page(width=595, height=842).insert_image(
            fitz.Rect(0, 0, 595, 842), stream=buf.getvalue()
        )
        return pdf.tobytes()

    def test_a_scan_is_read(self):
        from app.services.doc_parser import parse_pdf

        with mock.patch.object(settings, "ocr_text_detection_enabled", True):
            parsed = parse_pdf(self._scan(["Our Forest Friday programme", "Call 03-1234 5678"]), "scan.pdf")
        self.assertIn("Forest Friday", parsed.raw_text)
        # Spacing inside a number varies by recognizer; its identity does not.
        self.assertEqual([phone_key(p) for p in phones_in(parsed.raw_text)], [phone_key("03-1234 5678")])

    def test_with_ocr_off_a_scan_reads_as_empty(self):
        from app.services.doc_parser import parse_pdf

        parsed = parse_pdf(self._scan(["Our Forest Friday programme"]), "scan.pdf")  # conftest: OCR off
        self.assertEqual(parsed.raw_text, "")


if __name__ == "__main__":
    unittest.main()
