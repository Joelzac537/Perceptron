"""Tests for the Event Router's deterministic identifier layer."""

import pytest
from fixtures.router import load_event

from app.events.identifiers import (
    DEFAULT_CURRENCY,
    IDENTIFIER_KEYS,
    KEY_AMOUNT,
    KEY_CURRENCY,
    KEY_DOCUMENT_ID,
    KEY_ORDER_ID,
    KEY_POLICY_ID,
    amount_match,
    extract_identifiers,
    token_match,
)

# --------------------------------------------------------------------------------------
# extract_identifiers: order ids
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We have processed a refund for Order #A1298.", "A1298"),
        ("We have processed a refund for Order A1298.", "A1298"),
        ("order number: A1298 is on its way", "A1298"),
        ("Order no. A1298 shipped", "A1298"),
        ("Refund confirmation for #A1298", "A1298"),
        ("your order #a1298 has been refunded", "a1298"),
    ],
)
def test_order_id_forms(text: str, expected: str) -> None:
    assert extract_identifiers(text)[KEY_ORDER_ID] == expected


def test_order_keyword_followed_by_prose_is_not_an_order_id() -> None:
    """An identifier candidate with no digit is prose, not an id."""
    assert KEY_ORDER_ID not in extract_identifiers("Thanks, Merchant Order Returns Team")


# --------------------------------------------------------------------------------------
# extract_identifiers: policy and invoice numbers
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Policy #RP-2025-8891 is due for renewal", "RP-2025-8891"),
        ("policy number RP-2025-8891", "RP-2025-8891"),
        ("Please review RP-2025-8891 before Friday", "RP-2025-8891"),
        ("Your car policy CA-2026-1120 renews soon", "CA-2026-1120"),
    ],
)
def test_policy_id_forms(text: str, expected: str) -> None:
    assert extract_identifiers(text)[KEY_POLICY_ID] == expected


def test_invoice_number_maps_to_document_id() -> None:
    found = extract_identifiers("See Invoice #INV-4471 attached.")
    assert found[KEY_DOCUMENT_ID] == "INV-4471"
    assert KEY_ORDER_ID not in found, "the invoice hash must not be claimed as an order id"


# --------------------------------------------------------------------------------------
# extract_identifiers: amounts
# --------------------------------------------------------------------------------------


def test_dollar_amount_becomes_float_with_currency() -> None:
    found = extract_identifiers("We have processed a $129.00 refund for Order #A1298.")
    assert found[KEY_AMOUNT] == 129.0
    assert isinstance(found[KEY_AMOUNT], float)
    assert found[KEY_CURRENCY] == DEFAULT_CURRENCY


def test_comma_grouped_amount_is_read_whole() -> None:
    found = extract_identifiers("A credit of $1,129.00 was applied.")
    assert found[KEY_AMOUNT] == 1129.0


def test_bare_number_is_not_a_currency_amount() -> None:
    """Without a currency marker, 129 is just a number; extraction stays conservative."""
    assert KEY_AMOUNT not in extract_identifiers("Your refund of 129 is on the way")


def test_order_id_digits_are_not_read_as_an_amount() -> None:
    assert KEY_AMOUNT not in extract_identifiers("Order #A1298 shipped in 3-5 business days")


# --------------------------------------------------------------------------------------
# extract_identifiers: shape and empty input
# --------------------------------------------------------------------------------------


def test_only_documented_keys_are_returned() -> None:
    found = extract_identifiers("Order #A1298 for $129.00, policy RP-2025-8891, Invoice #INV-7")
    assert set(found) <= set(IDENTIFIER_KEYS)
    assert found[KEY_ORDER_ID] == "A1298"
    assert found[KEY_POLICY_ID] == "RP-2025-8891"
    assert found[KEY_DOCUMENT_ID] == "INV-7"
    assert found[KEY_AMOUNT] == 129.0


def test_keys_absent_rather_than_none_when_nothing_found() -> None:
    assert extract_identifiers("Please get the final version from Mike.") == {}


@pytest.mark.parametrize("text", [None, "", "   ", "\n\t "])
def test_empty_input_returns_empty_dict(text: str | None) -> None:
    assert extract_identifiers(text) == {}


# --------------------------------------------------------------------------------------
# token_match
# --------------------------------------------------------------------------------------


def test_token_match_across_non_token_boundaries() -> None:
    assert token_match("a1298", "order #a1298 has been") is True


def test_token_match_is_case_insensitive() -> None:
    assert token_match("A1298", "order #a1298 has been") is True
    assert token_match("a1298", "Order #A1298 has been") is True


def test_token_match_rejects_substring_of_a_larger_token() -> None:
    assert token_match("a1298", "xa1298y") is False


@pytest.mark.parametrize("text", ["a1298y", "xa1298", "a1298_b", "9a1298"])
def test_token_match_rejects_partial_boundaries(text: str) -> None:
    assert token_match("a1298", text) is False


def test_token_match_treats_hyphenated_id_as_one_token() -> None:
    assert token_match("RP-2025-8891", "policy RP-2025-8891 renews") is True
    assert token_match("RP-2025-8891", "policy RP-2025-88912 renews") is False


@pytest.mark.parametrize(
    ("value", "text"),
    [(None, "order #a1298"), ("a1298", None), ("", "order #a1298"), ("   ", "order #a1298")],
)
def test_token_match_empty_input_is_false(value: str | None, text: str | None) -> None:
    assert token_match(value, text) is False


# --------------------------------------------------------------------------------------
# amount_match
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["$129.00", "a $129.00 refund", "129", "129.0", "129.00", "amount: $129"],
)
def test_amount_match_accepts_equal_values(text: str) -> None:
    assert amount_match(129.0, text) is True


@pytest.mark.parametrize("text", ["$1,129.00", "1,129.00", "$1290.00", "$12.90", "$1.29"])
def test_amount_match_rejects_different_values(text: str) -> None:
    assert amount_match(129.0, text) is False


def test_comma_grouped_amount_matches_its_own_value() -> None:
    assert amount_match(1129.0, "$1,129.00") is True


def test_amount_match_ignores_digits_inside_identifiers() -> None:
    """1298 lives inside A1298, so it is not an amount."""
    assert amount_match(1298.0, "Order #A1298") is False


def test_amount_match_ignores_digits_inside_a_policy_number() -> None:
    assert amount_match(2025.0, "policy RP-2025-8891") is False


@pytest.mark.parametrize("text", [None, "", "   ", "no numbers here"])
def test_amount_match_empty_input_is_false(text: str | None) -> None:
    assert amount_match(129.0, text) is False


def test_integer_and_two_decimal_forms_are_equal() -> None:
    assert amount_match(129, "$129.00") is True
    assert amount_match(129.00, "129") is True


# --------------------------------------------------------------------------------------
# Against the router fixtures
# --------------------------------------------------------------------------------------


def test_refund_fixture_yields_the_identifiers_loop_refund_001_needs() -> None:
    event = load_event("refund_confirmed_no_thread")
    found = extract_identifiers(event.content)
    assert found[KEY_ORDER_ID] == "A1298"
    assert found[KEY_AMOUNT] == 129.0
    assert found[KEY_CURRENCY] == DEFAULT_CURRENCY


def test_mike_fixture_yields_no_deterministic_identifiers() -> None:
    """The fixture that forces the semantic path must give this layer nothing to work with."""
    event = load_event("mike_has_final_no_thread")
    assert extract_identifiers(event.content) == {}
    assert extract_identifiers(event.subject) == {}


def test_completed_loop_fixture_yields_its_own_order_id() -> None:
    event = load_event("completed_loop_refund")
    found = extract_identifiers(event.content)
    assert found[KEY_ORDER_ID] == "Z9001"
    assert found[KEY_AMOUNT] == 45.0
