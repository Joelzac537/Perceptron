"""Deterministic identifier extraction and comparison for the Event Router.

Pure functions over text. No LLM, no I/O, no clock, no database. Everything here is
regex and arithmetic, so it is cheap enough to run against every candidate loop before
any model is consulted, and its results are reproducible in tests without an API key.

Scope boundary: these helpers report what a string *contains*. They do not decide whether
an event matches a loop, and they never judge whether evidence proves anything.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Final

# Keys of the dict returned by extract_identifiers. The router and its tests import these
# rather than repeating string literals.
KEY_ORDER_ID: Final = "order_id"
KEY_POLICY_ID: Final = "policy_id"
KEY_DOCUMENT_ID: Final = "document_id"
KEY_AMOUNT: Final = "amount"
KEY_CURRENCY: Final = "currency"

IDENTIFIER_KEYS: Final = (
    KEY_ORDER_ID,
    KEY_POLICY_ID,
    KEY_DOCUMENT_ID,
    KEY_AMOUNT,
    KEY_CURRENCY,
)

# The only currency the connectors emit today. Revisit when a non-USD source app lands.
DEFAULT_CURRENCY: Final = "USD"

# What counts as "inside the same token" for token_match. Deliberately excludes '-' so a
# hyphenated policy number reads as one token, and excludes '#'/'$' so a sigil never hides
# the identifier that follows it.
TOKEN_CHARS: Final = "A-Za-z0-9_"

# An identifier-looking run of characters: starts alphanumeric, may contain hyphens,
# underscores and slashes. Candidates without a digit are discarded by _first_identifier,
# which keeps prose like "Order Returns Team" from being read as an order id.
_ID_TOKEN: Final = r"[A-Za-z0-9][A-Za-z0-9\-_/]*"

# Optional "id"/"no."/"number" filler and separator between a keyword and its value, so
# "Order A1298", "Order #A1298" and "order number: A1298" all parse.
_LABEL_GAP: Final = r"(?:\s+(?:id|no\.?|num(?:ber)?))?\s*[#:]?\s*"

ORDER_ID_RE: Final = re.compile(rf"\border(?:s)?\b{_LABEL_GAP}({_ID_TOKEN})", re.IGNORECASE)
POLICY_ID_RE: Final = re.compile(rf"\bpolicy\b{_LABEL_GAP}({_ID_TOKEN})", re.IGNORECASE)
INVOICE_ID_RE: Final = re.compile(
    rf"\b(?:invoice|inv\.?)\b{_LABEL_GAP}({_ID_TOKEN})", re.IGNORECASE
)

# A policy number in the carriers' house format (RP-2025-8891, CA-2026-1120), recognised
# even when the word "policy" is absent.
STRUCTURED_POLICY_RE: Final = re.compile(r"(?<![\w-])([A-Za-z]{2,4}-\d{4}-\d{3,6})(?![\w-])")

# A bare "#A1298", used as an order-id fallback when no keyword introduced one.
HASH_ID_RE: Final = re.compile(rf"#\s*({_ID_TOKEN})")

# A decimal number, optionally comma-grouped. The grouped alternative is listed first so
# "1,129.00" is consumed whole rather than as "1".
_AMOUNT_BODY: Final = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"

# Leading guard: refuse to start mid-number or mid-identifier. Excluding '-' keeps the
# "2025" inside RP-2025-8891 from reading as a standalone amount.
_AMOUNT_LEAD: Final = r"(?<![\w.,-])"

# extract_identifiers only reports amounts written as currency, so "Order A1298" and
# "3-5 business days" contribute nothing.
CURRENCY_AMOUNT_RE: Final = re.compile(rf"{_AMOUNT_LEAD}\$\s*({_AMOUNT_BODY})")

# amount_match is deliberately more permissive: a bare "129" in the body should still
# compare equal to an expected 129.0.
NUMERIC_AMOUNT_RE: Final = re.compile(rf"{_AMOUNT_LEAD}\$?\s*({_AMOUNT_BODY})")


def _first_identifier(pattern: re.Pattern[str], text: str) -> str | None:
    """First capture of `pattern` in `text` that contains at least one digit."""
    for match in pattern.finditer(text):
        candidate = match.group(1).strip("-_/")
        if any(char.isdigit() for char in candidate):
            return candidate
    return None


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _find_amounts(pattern: re.Pattern[str], text: str) -> list[Decimal]:
    amounts = [_to_decimal(match.group(1)) for match in pattern.finditer(text)]
    return [amount for amount in amounts if amount is not None]


def extract_identifiers(text: str | None) -> dict[str, object]:
    """Pull hard identifiers out of free text.

    Returns a dict using only the keys in `IDENTIFIER_KEYS`; keys with nothing found are
    omitted, so an empty dict means the text carried no deterministic signal at all.
    `amount` is a float and arrives with `currency` alongside it.

    Empty, whitespace-only and None input return an empty dict rather than raising, since
    `Event.content` and `Event.subject` are both optional on the wire.
    """
    if not text or not text.strip():
        return {}

    found: dict[str, object] = {}

    order_id = _first_identifier(ORDER_ID_RE, text)
    policy_id = _first_identifier(POLICY_ID_RE, text) or _first_identifier(
        STRUCTURED_POLICY_RE, text
    )
    document_id = _first_identifier(INVOICE_ID_RE, text)

    # A bare "#1234" is an order id only if nothing better claimed it and no keyword
    # pattern already accounted for that exact value.
    if order_id is None:
        claimed = {policy_id, document_id}
        for match in HASH_ID_RE.finditer(text):
            candidate = match.group(1).strip("-_/")
            if any(char.isdigit() for char in candidate) and candidate not in claimed:
                order_id = candidate
                break

    if order_id is not None:
        found[KEY_ORDER_ID] = order_id
    if policy_id is not None:
        found[KEY_POLICY_ID] = policy_id
    if document_id is not None:
        found[KEY_DOCUMENT_ID] = document_id

    amounts = _find_amounts(CURRENCY_AMOUNT_RE, text)
    if amounts:
        found[KEY_AMOUNT] = float(amounts[0])
        found[KEY_CURRENCY] = DEFAULT_CURRENCY

    return found


def token_match(value: str | None, text: str | None) -> bool:
    """True when `value` occurs in `text` as a complete token, case-insensitively.

    Token boundaries are the characters in `TOKEN_CHARS`. "a1298" matches inside
    "order #a1298 has been" because '#' and ' ' are not token characters, but not inside
    "xa1298y". Never use this for amounts: "129" would match inside "129.00" on the left
    boundary and the numeric comparison in `amount_match` is the correct tool.
    """
    if not value or not text:
        return False

    needle = value.strip()
    if not needle:
        return False

    pattern = rf"(?<![{TOKEN_CHARS}]){re.escape(needle)}(?![{TOKEN_CHARS}])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def amount_match(expected: float, text: str | None) -> bool:
    """True when `text` contains an amount numerically equal to `expected`.

    Comparison goes through `Decimal(str(x))` so 129, 129.0 and "$129.00" are all equal
    while "$1,129.00" is not. Comma grouping is understood, so "1,129.00" is eleven
    hundred and twenty-nine rather than one. Bare numbers count, which makes this
    intentionally looser than the currency-only extraction in `extract_identifiers`.
    """
    if not text:
        return False

    try:
        target = Decimal(str(expected))
    except InvalidOperation:
        return False

    return any(amount == target for amount in _find_amounts(NUMERIC_AMOUNT_RE, text))
