"""Rule 7: raw payloads are kept; card numbers and contact details are not.

`scrub` runs inside every mapper, before a payload reaches a canonical record.
It drops fields known to hold contact details or card numbers, then removes
anything left in a string that still looks like one.

A shopper is known only by a keyed hash of their normalised phone or email.
"""

import hashlib
import hmac
import re
from typing import cast
from uuid import UUID

REMOVED = "[removed]"

# Field names, lower case, across the providers' payloads.
DROP_KEYS = frozenset(
    {
        "email",
        "contact",
        "phone",
        "mobile",
        "firstname",
        "lastname",
        "name",
        "name_on_card",
        "vpa",
        "upi_va",
        "upi_id",
        "cardnum",
        "card_no",
        "card_number",
        "last4",
        "customer_details",
        "customer_email",
        "customer_phone",
        "customer_name",
        "address1",
        "address2",
        "city",
        "zipcode",
    }
)

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
UPI_ID = re.compile(r"\b[A-Za-z0-9._-]{2,}@[A-Za-z]{2,}\b")
# Only with the +91 prefix: a bare run of ten or twelve digits is as likely a bank reference.
PHONE = re.compile(r"\+91[\s-]?\d{10}(?!\d)")
MASKED_CARD = re.compile(r"(?<![A-Za-z0-9])(?=[\dXx*]*[Xx*])[\dXx*]{12,19}(?![A-Za-z0-9])")
DIGIT_RUN = re.compile(r"(?<![A-Za-z0-9])\d{13,19}(?![A-Za-z0-9])")


def luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def scrub_text(value: str) -> str:
    value = EMAIL.sub(REMOVED, value)
    value = UPI_ID.sub(REMOVED, value)
    value = PHONE.sub(REMOVED, value)
    value = MASKED_CARD.sub(REMOVED, value)
    return DIGIT_RUN.sub(lambda m: REMOVED if luhn(m.group()) else m.group(), value)


def scrub[T](value: T) -> T:
    """A copy of a payload with contact details and card numbers removed."""
    cleaned: object = value
    if isinstance(value, dict):
        cleaned = {
            key: scrub(item) for key, item in value.items() if str(key).lower() not in DROP_KEYS
        }
    elif isinstance(value, list):
        cleaned = [scrub(item) for item in value]
    elif isinstance(value, str):
        cleaned = scrub_text(value)
    return cast(T, cleaned)


def normalise_contact(phone: object = None, email: object = None) -> str | None:
    """Phone if there is one (its last ten digits), else the email in lower case."""
    digits = re.sub(r"\D", "", str(phone)) if phone else ""
    if len(digits) >= 10:
        return f"phone:{digits[-10:]}"
    address = str(email).strip().lower() if email else ""
    return f"email:{address}" if address else None


def tenant_token_key(root_key: str, tenant_id: UUID) -> bytes:
    """One hashing key per tenant, so a token means nothing outside its tenant."""
    if not root_key:
        raise RuntimeError("TOKEN_HASH_ROOT_KEY is not set; shopper tokens cannot be made.")
    return hmac.new(root_key.encode(), tenant_id.bytes, hashlib.sha256).digest()


def shopper_token(key: bytes, contact: str) -> str:
    return hmac.new(key, contact.encode(), hashlib.sha256).hexdigest()
