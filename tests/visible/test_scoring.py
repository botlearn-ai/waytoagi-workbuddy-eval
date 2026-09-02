"""Visible sample tests for gdpval_eval.scoring.score_task (unit T3).

These are guidance examples for the implementer; the authoritative,
comprehensive contract tests live in tests/hidden/test_scoring.py.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from gdpval_eval.models import ItemState, JudgedItem, ScoreStatus
from gdpval_eval.scoring import score_task


def test_score_task_happy_path_no_protection_rules_triggered():
    """Basic scoring: one met positive item, one unmet positive filler item,
    one not-met negative item (no violation -> no deduction). No item is
    anywhere near the 15%/30% protection-rule thresholds, so this exercises
    only the base earned/denominator/score_5 formula.
    """
    items = [
        JudgedItem(score=10, state=ItemState.CONDITION_MET),
        JudgedItem(score=90, state=ItemState.CONDITION_NOT_MET),
        JudgedItem(score=-5, state=ItemState.CONDITION_NOT_MET),
    ]

    result = score_task(items)

    assert result.status == ScoreStatus.SCORED
    assert result.full_base == 100
    assert result.no_evidence_points == 0
    assert result.denominator == 100
    assert result.capped_item_count == 0
    assert result.deduction_capped is False
    assert result.earned == Fraction(10)
    assert result.score_5 == Fraction(1, 2)
    assert result.needs_review is False


def test_score_task_rejects_zero_score():
    """score == 0 is invalid for any item, regardless of state."""
    items = [JudgedItem(score=0, state=ItemState.CONDITION_MET)]

    with pytest.raises(ValueError):
        score_task(items)


def test_score_task_no_evidence_narrows_denominator():
    """A positive NO_EVIDENCE item is excluded from earned but still counted
    in full_base, so it shrinks the denominator (D = full_base - no_evidence
    points) without being penalized as a miss.
    """
    items = [
        JudgedItem(score=5, state=ItemState.CONDITION_MET),
        JudgedItem(score=15, state=ItemState.NO_EVIDENCE),
        JudgedItem(score=80, state=ItemState.CONDITION_NOT_MET),
    ]

    result = score_task(items)

    assert result.status == ScoreStatus.SCORED
    assert result.full_base == 100
    assert result.no_evidence_points == 15
    assert result.denominator == 85
    assert result.earned == Fraction(5)
    assert result.score_5 == Fraction(5, 17)
    assert result.needs_review is False
