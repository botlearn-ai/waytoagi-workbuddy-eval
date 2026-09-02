"""Tests for rubric_item_hash (spec T1).

All criterion fixtures below are synthetic sentences invented for this test
file; none originate from the real GDPval dataset (see AGENTS.md secrecy
rules).
"""

from __future__ import annotations

import hashlib

import pytest

from gdpval_eval.hashing import rubric_item_hash


def test_hashing_same_input_produces_same_output():
    criterion = "Includes a data source citation for each chart."
    first = rubric_item_hash(criterion, 2)
    second = rubric_item_hash(criterion, 2)

    assert first == second
    assert len(first) == 64
    assert all(ch in "0123456789abcdef" for ch in first)

    expected = hashlib.sha256(criterion.encode("utf-8") + b"\x1f" + b"2").hexdigest()
    assert first == expected


def test_hashing_trailing_whitespace_changes_hash():
    base = "Uses consistent heading styles across sections"
    padded = base + " "

    assert rubric_item_hash(base, 3) != rubric_item_hash(padded, 3)


def test_hashing_curly_quote_preserved_verbatim():
    straight = "Client's summary avoids technical jargon."
    curly = "Client’s summary avoids technical jargon."

    result = rubric_item_hash(curly, 5)
    expected = hashlib.sha256(curly.encode("utf-8") + b"\x1f" + b"5").hexdigest()

    assert result == expected
    assert result != rubric_item_hash(straight, 5)


def test_hashing_negative_score_is_legal():
    criterion = "Includes a disclaimer stating the output is not legal advice."
    result = rubric_item_hash(criterion, -10)
    expected = hashlib.sha256(criterion.encode("utf-8") + b"\x1f" + b"-10").hexdigest()

    assert result == expected


def test_hashing_score_zero_raises_value_error():
    with pytest.raises(ValueError):
        rubric_item_hash("Lists each assumption made during the analysis.", 0)


def test_hashing_blank_criterion_raises_value_error():
    for blank in ("", "   ", "\n\t "):
        with pytest.raises(ValueError):
            rubric_item_hash(blank, 4)


def test_hashing_error_message_omits_criterion_text():
    sentinel = "SENTINEL_UNIQUE_CRITERION_TEXT_7f3a9d"

    with pytest.raises(ValueError) as exc_info:
        rubric_item_hash(sentinel, 0)

    assert sentinel not in str(exc_info.value)
