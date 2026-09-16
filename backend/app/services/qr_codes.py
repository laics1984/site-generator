"""
QR codes in source images: read one, and say what it is for.

A QR code is the one image a site cannot use decoratively. Cropped, it no longer
scans; laid under a headline, it is noise; and shown bare, it is an instruction
with the instruction missing — watr.org.my's footer code is a DuitNow donation
account, and nothing on the image says so beyond "SIBKL WATR-QR".

Why decode rather than detect: detection alone fires on photographs (a building
facade's window grid produced corner points on a measured sample) while a
successful DECODE never did, and the payload is what the caption is written
from. OpenCV's detector ships with rapidocr (text_detection's optional
dependency), reads the 512px frame that pass already decodes, and costs ~10ms.

Why the caption is derived, never written: the payload states its own purpose in
a registry anyone can check — an EMVCo merchant template names its payment
scheme and the merchant's ISO 18245 category, a URL names its host. A model asked
to describe the code could only guess at the rest, and a guessed purpose on a
bank account ("donate to our building fund") is a false claim about where a
visitor's money goes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class QrPurpose:
    """What scanning a code does, stated only from what the payload says."""

    title: str
    description: str
    # A destination a phone can open directly. None for a payment code, which
    # has no URL — its app reads the image, not a link.
    action_label: str | None = None
    action_href: str | None = None


def decode(pixels: Any) -> str | None:
    """The payload of a QR code in an RGB array, or None.

    None covers every way of not knowing — no code, an unreadable code, or
    OpenCV absent — because a code that cannot be read cannot be described, and
    the caller treats the image as having none.
    """
    try:
        import cv2
    except ImportError:
        return None
    try:
        payload, _corners, _straight = cv2.QRCodeDetector().detectAndDecode(pixels)
    except cv2.error:
        return None
    return payload.strip() or None


def purpose_of(payload: str) -> QrPurpose:
    """Read what a decoded payload is for. First reader that recognises it wins."""
    for read in (_payment, _link, _phone, _email):
        purpose = read(payload)
        if purpose is not None:
            return purpose
    return _UNRECOGNISED


_UNRECOGNISED = QrPurpose(
    title="Scan with your phone",
    description="Point your phone's camera at the code to open it.",
)


# --- payment (EMVCo merchant-presented QR) --------------------------------------

# Payload Format Indicator: tag 00, two characters — the first field of every
# EMVCo merchant QR. The spec fixes its value at "01", but codes in the wild carry
# "02" (watr.org.my's DuitNow code does), so the VALUE is not the test: the
# structure is. A payload that opens with the indicator and parses field by field
# all the way to the mandatory closing CRC (tag 63) is an EMVCo payload.
_EMVCO_PREFIX = "0002"
_CRC_TAG = "63"
# Merchant Account Information templates; subtag 00 is the scheme's identifier.
_ACCOUNT_TAGS = tuple(str(tag) for tag in range(26, 52))
_MERCHANT_CATEGORY_TAG = "52"
_MERCHANT_NAME_TAG = "59"

# Scheme identifier prefix → the name a visitor's banking app uses. Matched as a
# prefix, case-insensitively: an identifier carries a product suffix after the
# registered application id (DuitNow's is A000000615 + 0001).
_PAYMENT_SCHEMES: tuple[tuple[str, str], ...] = (
    ("A000000615", "DuitNow"),
    ("SG.PAYNOW", "PayNow"),
    ("A000000677", "PromptPay"),
    ("ID.CO.QRIS", "QRIS"),
    ("BR.GOV.BCB.PIX", "Pix"),
    ("A000000727", "VietQR"),
)

# ISO 18245 categories whose payments are gifts, not purchases: 8398 charitable
# and social service organisations, 8661 religious organisations.
_DONATION_CATEGORIES = frozenset({"8398", "8661"})

# A phone cannot scan its own screen, and a payment code has no link to tap.
# Every scheme above lets its apps read a code from a saved image instead.
_SAVED_IMAGE_TIP = "On a phone? Save this image and open it from your app's scanner."


def _tlv(data: str) -> dict[str, str]:
    """Top-level EMVCo tag-length-value fields. Stops at the first malformed one."""
    fields: dict[str, str] = {}
    index = 0
    while index + 4 <= len(data):
        tag, length = data[index : index + 2], data[index + 2 : index + 4]
        if not length.isdigit():
            break
        end = index + 4 + int(length)
        if end > len(data):
            break
        fields.setdefault(tag, data[index + 4 : end])
        index = end
    return fields


def _scheme_name(fields: dict[str, str]) -> str | None:
    for tag in _ACCOUNT_TAGS:
        identifier = _tlv(fields.get(tag, "")).get("00", "").upper()
        for prefix, name in _PAYMENT_SCHEMES:
            if identifier.startswith(prefix):
                return name
    return None


def _payment(payload: str) -> QrPurpose | None:
    if not payload.startswith(_EMVCO_PREFIX):
        return None
    fields = _tlv(payload)
    if _CRC_TAG not in fields:
        return None
    scheme = _scheme_name(fields)
    merchant = fields.get(_MERCHANT_NAME_TAG, "").strip()
    gives = fields.get(_MERCHANT_CATEGORY_TAG) in _DONATION_CATEGORIES
    verb = "give" if gives else "pay"
    recipient = f" to {merchant}" if gives and merchant else f" {merchant}" if merchant else ""
    app = f"any {scheme} banking or e-wallet app" if scheme else "your banking or e-wallet app"
    return QrPurpose(
        title=f"{verb.capitalize()} with {scheme}" if scheme else f"{verb.capitalize()} by QR",
        description=f"Scan with {app} to {verb}{recipient}. {_SAVED_IMAGE_TIP}",
    )


# --- links, phone numbers, email addresses ----------------------------------------

_WHATSAPP_CHAT_HOSTS = frozenset({"wa.me", "api.whatsapp.com"})
_WHATSAPP_GROUP_HOST = "chat.whatsapp.com"


def _link(payload: str) -> QrPurpose | None:
    parsed = urlparse(payload)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.removeprefix("www.")
    if host in _WHATSAPP_CHAT_HOSTS:
        return QrPurpose(
            "Chat on WhatsApp", "Scan to start a WhatsApp chat.", "Open WhatsApp", payload
        )
    if host == _WHATSAPP_GROUP_HOST:
        return QrPurpose(
            "Join the WhatsApp group", "Scan to join the group on WhatsApp.",
            "Join on WhatsApp", payload,
        )
    return QrPurpose(
        f"Visit {host}", f"Scan with your phone's camera to open {host}.", "Open link", payload
    )


def _phone(payload: str) -> QrPurpose | None:
    if not payload.lower().startswith("tel:"):
        return None
    number = re.sub(r"[^0-9+]", "", payload[4:])
    if not number:
        return None
    return QrPurpose("Call us", f"Scan to call {number}.", "Call", f"tel:{number}")


def _email(payload: str) -> QrPurpose | None:
    if not payload.lower().startswith("mailto:"):
        return None
    address = unquote(payload[7:].split("?", 1)[0]).strip()
    if "@" not in address:
        return None
    return QrPurpose("Email us", f"Scan to email {address}.", "Send email", f"mailto:{address}")
