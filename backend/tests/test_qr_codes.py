"""QR codes in source images (services/qr_codes.py).

Two halves: reading a code out of the pixels, and saying what it is for. The
second is the one that must never guess — a caption on a bank account is a claim
about where a visitor's money goes, so every sentence here is read off the
payload or is not written.
"""

import unittest

from app.services.qr_codes import _UNRECOGNISED, decode, purpose_of

try:
    import cv2
    import numpy as np
except ImportError:  # the optional OCR wheel brings OpenCV
    cv2 = None

# watr.org.my's footer code, verbatim: a DuitNow merchant QR whose Payload Format
# Indicator is "02" rather than the spec's "01", category 8661.
WATR_DUITNOW = (
    "00020201021126610014A000000615000101065018540215000001234079150031000000000005"
    "204866153034585802MY5913SIBKL WATR-QR6013PETALING JAYA6105463506215011110310300000"
    "630432FD"
)


def _emvco(merchant_account: str, category: str, name: str) -> str:
    """A minimal well-formed EMVCo payload (the CRC value is not checked)."""
    def field(tag: str, value: str) -> str:
        return f"{tag}{len(value):02d}{value}"

    return "".join([
        field("00", "01"),
        field("26", merchant_account),
        field("52", category),
        field("58", "SG"),
        field("59", name),
        field("63", "ABCD"),
    ])


class PaymentPurposeTest(unittest.TestCase):
    def test_watr_duitnow_code_is_a_donation(self):
        purpose = purpose_of(WATR_DUITNOW)
        self.assertEqual(purpose.title, "Give with DuitNow")
        self.assertIn("DuitNow", purpose.description)
        self.assertIn("give to SIBKL WATR-QR", purpose.description)

    def test_a_payment_code_offers_no_link(self):
        """It has no URL: a banking app reads the image, so there is nothing to tap."""
        purpose = purpose_of(WATR_DUITNOW)
        self.assertIsNone(purpose.action_href)
        self.assertIn("Save this image", purpose.description)

    def test_a_shop_is_paid_not_given_to(self):
        payload = _emvco("0009SG.PAYNOW0101201234567K", "5812", "Kopi House")
        purpose = purpose_of(payload)
        self.assertEqual(purpose.title, "Pay with PayNow")
        self.assertIn("to pay Kopi House", purpose.description)

    def test_an_unknown_scheme_is_described_without_naming_one(self):
        payload = _emvco("0012XX.UNKNOWN.PAY", "8398", "Food Bank")
        purpose = purpose_of(payload)
        self.assertEqual(purpose.title, "Give by QR")
        self.assertIn("your banking or e-wallet app", purpose.description)

    def test_a_truncated_payload_is_not_read_as_a_payment(self):
        """Structure is the test, so a payload that stops before its CRC is not
        EMVCo, however it starts."""
        self.assertEqual(purpose_of(WATR_DUITNOW[:60]), _UNRECOGNISED)


class LinkPurposeTest(unittest.TestCase):
    def test_whatsapp_chat(self):
        purpose = purpose_of("https://wa.me/60123456789")
        self.assertEqual(purpose.title, "Chat on WhatsApp")
        self.assertEqual(purpose.action_href, "https://wa.me/60123456789")

    def test_whatsapp_group(self):
        purpose = purpose_of("https://chat.whatsapp.com/AbCdEf")
        self.assertEqual(purpose.title, "Join the WhatsApp group")

    def test_any_other_link_names_its_host(self):
        purpose = purpose_of("https://www.qr.page/g/5fcQ2xyDvRX")
        self.assertEqual(purpose.title, "Visit qr.page")
        self.assertEqual(purpose.action_label, "Open link")

    def test_phone_and_email(self):
        call = purpose_of("tel:+60 3-1234 5678")
        self.assertEqual(call.action_href, "tel:+60312345678")
        mail = purpose_of("mailto:hello%40example.com?subject=Hi")
        self.assertEqual(mail.action_href, "mailto:hello@example.com")

    def test_only_safe_schemes_become_a_button(self):
        """The action is written into an <a href>; a code's payload is not trusted."""
        for payload in ("javascript:alert(1)", "data:text/html,hi", "WIFI:S:home;;", "hello"):
            with self.subTest(payload=payload):
                purpose = purpose_of(payload)
                self.assertIsNone(purpose.action_href)
                self.assertEqual(purpose, _UNRECOGNISED)


@unittest.skipIf(cv2 is None, "OpenCV not installed (optional OCR dependency)")
class DecodeTest(unittest.TestCase):
    def test_round_trips_an_encoded_code(self):
        encoded = cv2.QRCodeEncoder.create().encode("https://wa.me/60123456789")
        image = cv2.resize(encoded, (encoded.shape[1] * 8, encoded.shape[0] * 8),
                           interpolation=cv2.INTER_NEAREST)
        framed = cv2.copyMakeBorder(image, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
        rgb = cv2.cvtColor(framed, cv2.COLOR_GRAY2RGB)
        self.assertEqual(decode(rgb), "https://wa.me/60123456789")

    def test_a_photo_without_a_code_reads_nothing(self):
        noise = np.random.default_rng(7).integers(0, 255, (256, 256, 3), dtype=np.uint8)
        self.assertIsNone(decode(noise))


if __name__ == "__main__":
    unittest.main()
