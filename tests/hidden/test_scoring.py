"""Hidden contract tests for gdpval_eval.scoring.score_task (unit T3).

Blind-graded: not visible to the implementer. All arithmetic is checked
with exact fractions.Fraction equality -- never pytest.approx -- per the
spec's "no floating point, ever" requirement. All item data is synthetic
(abstract score=1/2/5/-2-style items); nothing here is a real rubric
criterion or task_id.

Contract under test (from the work-unit spec):
- JudgedItem.score must be a nonzero int (0 -> ValueError); state must be a
  genuine ItemState member (anything else -> ValueError).
- full_base = sum of all positive scores, regardless of state.
- no_evidence_points = sum of positive scores whose state is NO_EVIDENCE.
- denominator D = full_base - no_evidence_points.
- D == 0 -> status=NO_DENOMINATOR, score_5=0, earned=0, needs_review=True.
- Two independent, non-chained protection rules (both based on full_base):
  (1) a single CONDITION_MET positive item counts for at most
      15% * full_base (excess -> capped_item_count += 1);
  (2) the CONDITION_MET-negative deduction total is floored at
      -30% * full_base (deduction_capped=True if the floor binds).
- earned = sum of capped positive contributions + floored deduction total.
- score_5 = 5 * max(0, earned) / D, exact Fraction throughout.
- no_evidence_ratio = no_evidence_points / full_base (0 if full_base == 0);
  > 20% -> needs_review=True (NO_DENOMINATOR is always needs_review=True).
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from gdpval_eval.models import ItemState, JudgedItem, ScoreStatus
from gdpval_eval.scoring import score_task

# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "items",
    [
        [JudgedItem(score=0, state=ItemState.CONDITION_MET)],
        [JudgedItem(score=0, state=ItemState.NO_EVIDENCE)],
        [
            JudgedItem(score=5, state=ItemState.CONDITION_MET),
            JudgedItem(score=0, state=ItemState.CONDITION_NOT_MET),
        ],
        [
            JudgedItem(score=-3, state=ItemState.CONDITION_MET),
            JudgedItem(score=10, state=ItemState.CONDITION_MET),
            JudgedItem(score=0, state=ItemState.CONDITION_MET),
        ],
    ],
)
def test_score_task_valueerror_on_zero_score(items):
    """A score of exactly 0 is invalid for any item, in any position."""
    with pytest.raises(ValueError):
        score_task(items)


@pytest.mark.parametrize(
    "bad_state",
    ["condition_met", None, 1, object(), "CONDITION_MET"],
)
def test_score_task_valueerror_on_non_enum_state(bad_state):
    """Anything that is not a genuine ItemState member is rejected, even a
    string that spells out a valid state's name or value.
    """
    items = [JudgedItem(score=5, state=bad_state)]

    with pytest.raises(ValueError):
        score_task(items)


# ---------------------------------------------------------------------------
# Non-monotonicity regression (the red-team fix: protection rules anchor on
# full_base, not on the shifting denominator D)
# ---------------------------------------------------------------------------


def test_score_task_nonmonotonic_regression_no_evidence_to_condition_met():
    """Flipping one positive item's state from NO_EVIDENCE to CONDITION_MET
    (all else equal) must never decrease score_5.
    """
    items_no_evidence = [
        JudgedItem(score=20, state=ItemState.NO_EVIDENCE),
        JudgedItem(score=80, state=ItemState.CONDITION_MET),
    ]
    items_condition_met = [
        JudgedItem(score=20, state=ItemState.CONDITION_MET),
        JudgedItem(score=80, state=ItemState.CONDITION_MET),
    ]

    before = score_task(items_no_evidence)
    after = score_task(items_condition_met)

    assert before.full_base == 100
    assert after.full_base == 100
    assert before.denominator == 80
    assert after.denominator == 100
    assert before.earned == Fraction(15)
    assert after.earned == Fraction(30)
    assert before.score_5 == Fraction(15, 16)
    assert after.score_5 == Fraction(3, 2)
    assert after.score_5 >= before.score_5


# ---------------------------------------------------------------------------
# Protection rule 1: 15% single-item cap
# ---------------------------------------------------------------------------


def test_score_task_15_percent_item_cap_triggered_and_mixed():
    """Items above 15% of full_base are truncated and counted in
    capped_item_count; an item at or below the threshold is untouched.
    """
    items = [
        JudgedItem(score=700, state=ItemState.CONDITION_MET),  # > 150 -> capped
        JudgedItem(score=200, state=ItemState.CONDITION_MET),  # > 150 -> capped
        JudgedItem(score=100, state=ItemState.CONDITION_MET),  # <= 150 -> not capped
    ]

    result = score_task(items)

    assert result.status == ScoreStatus.SCORED
    assert result.full_base == 1000
    assert result.denominator == 1000
    assert result.capped_item_count == 2
    assert result.deduction_capped is False
    assert result.earned == Fraction(400)
    assert result.score_5 == Fraction(2)
    assert result.needs_review is False


# ---------------------------------------------------------------------------
# Protection rule 2: 30% deduction floor
# ---------------------------------------------------------------------------


def test_score_task_30_percent_deduction_floor_triggered():
    """CONDITION_MET negative items summing past -30% * full_base are
    floored at -30% * full_base and deduction_capped is set.
    """
    items = [
        JudgedItem(score=1000, state=ItemState.CONDITION_NOT_MET),  # filler, no earn
        JudgedItem(score=-300, state=ItemState.CONDITION_MET),
        JudgedItem(score=-300, state=ItemState.CONDITION_MET),
    ]

    result = score_task(items)

    assert result.status == ScoreStatus.SCORED
    assert result.full_base == 1000
    assert result.denominator == 1000
    assert result.deduction_capped is True
    assert result.earned == Fraction(-300)
    assert result.score_5 == Fraction(0)


def test_score_task_30_percent_deduction_floor_not_triggered():
    """A deduction total that stays within -30% * full_base is applied
    verbatim, and deduction_capped stays False.
    """
    items = [
        JudgedItem(score=100, state=ItemState.CONDITION_MET),
        JudgedItem(score=900, state=ItemState.CONDITION_NOT_MET),  # filler
        JudgedItem(score=-50, state=ItemState.CONDITION_MET),
    ]

    result = score_task(items)

    assert result.status == ScoreStatus.SCORED
    assert result.full_base == 1000
    assert result.denominator == 1000
    assert result.capped_item_count == 0
    assert result.deduction_capped is False
    assert result.earned == Fraction(50)
    assert result.score_5 == Fraction(1, 4)


# ---------------------------------------------------------------------------
# Negative-item three-state semantics
# ---------------------------------------------------------------------------


def test_score_task_negative_item_three_states_effects():
    """Only a CONDITION_MET negative item deducts. CONDITION_NOT_MET and
    NO_EVIDENCE negative items deduct nothing, and negative items never
    affect full_base / the denominator regardless of their state.
    """
    items = [
        JudgedItem(score=1000, state=ItemState.CONDITION_NOT_MET),  # filler
        JudgedItem(score=50, state=ItemState.CONDITION_MET),  # earns
        JudgedItem(score=-40, state=ItemState.CONDITION_MET),  # violation -> deducts
        JudgedItem(score=-30, state=ItemState.CONDITION_NOT_MET),  # no violation
        JudgedItem(score=-20, state=ItemState.NO_EVIDENCE),  # no evidence
    ]

    result = score_task(items)

    assert result.status == ScoreStatus.SCORED
    assert result.full_base == 1050
    assert result.no_evidence_points == 0
    assert result.denominator == 1050
    assert result.capped_item_count == 0
    assert result.deduction_capped is False
    assert result.earned == Fraction(10)
    assert result.score_5 == Fraction(1, 21)
    assert result.needs_review is False


# ---------------------------------------------------------------------------
# D == 0 via three distinct entry paths
# ---------------------------------------------------------------------------


def test_score_task_no_denominator_empty_list():
    """An empty item list has full_base == 0 -> D == 0 -> NO_DENOMINATOR,
    with a 0 (not undefined) no_evidence_ratio.
    """
    result = score_task([])

    assert result.status == ScoreStatus.NO_DENOMINATOR
    assert result.score_5 == Fraction(0)
    assert result.earned == Fraction(0)
    assert result.needs_review is True
    assert result.no_evidence_ratio == Fraction(0)
    assert result.denominator == 0
    assert result.full_base == 0
    assert result.no_evidence_points == 0


def test_score_task_no_denominator_all_negative_items():
    """All-negative item lists have full_base == 0 regardless of state mix
    -> D == 0 -> NO_DENOMINATOR.
    """
    items = [
        JudgedItem(score=-5, state=ItemState.CONDITION_MET),
        JudgedItem(score=-10, state=ItemState.CONDITION_NOT_MET),
        JudgedItem(score=-7, state=ItemState.NO_EVIDENCE),
    ]

    result = score_task(items)

    assert result.status == ScoreStatus.NO_DENOMINATOR
    assert result.score_5 == Fraction(0)
    assert result.earned == Fraction(0)
    assert result.needs_review is True
    assert result.no_evidence_ratio == Fraction(0)
    assert result.denominator == 0
    assert result.full_base == 0
    assert result.no_evidence_points == 0


def test_score_task_no_denominator_all_no_evidence_positive_items():
    """full_base > 0 but every positive item is NO_EVIDENCE, so
    no_evidence_points == full_base -> D == 0 -> NO_DENOMINATOR, and
    no_evidence_ratio is the full_base>0 branch (ratio == 1).
    """
    items = [
        JudgedItem(score=10, state=ItemState.NO_EVIDENCE),
        JudgedItem(score=20, state=ItemState.NO_EVIDENCE),
    ]

    result = score_task(items)

    assert result.status == ScoreStatus.NO_DENOMINATOR
    assert result.score_5 == Fraction(0)
    assert result.earned == Fraction(0)
    assert result.needs_review is True
    assert result.full_base == 30
    assert result.no_evidence_points == 30
    assert result.no_evidence_ratio == Fraction(1)
    assert result.denominator == 0


# ---------------------------------------------------------------------------
# needs_review ratio threshold boundary (> 20%, not >=)
# ---------------------------------------------------------------------------


def test_score_task_needs_review_exact_20_percent_not_triggered():
    """A no_evidence_ratio of exactly 20% does not trigger needs_review;
    the spec requires strictly greater than 20%.
    """
    items = [
        JudgedItem(score=20, state=ItemState.NO_EVIDENCE),
        JudgedItem(score=80, state=ItemState.CONDITION_NOT_MET),
    ]

    result = score_task(items)

    assert result.status == ScoreStatus.SCORED
    assert result.no_evidence_ratio == Fraction(1, 5)
    assert result.needs_review is False


def test_score_task_needs_review_just_over_20_percent_triggered():
    """A no_evidence_ratio just above 20% flips needs_review to True."""
    items = [
        JudgedItem(score=21, state=ItemState.NO_EVIDENCE),
        JudgedItem(score=79, state=ItemState.CONDITION_NOT_MET),
    ]

    result = score_task(items)

    assert result.status == ScoreStatus.SCORED
    assert result.no_evidence_ratio == Fraction(21, 100)
    assert result.needs_review is True


# ---------------------------------------------------------------------------
# earned < 0 is clamped to 0 for score_5 (independent of the 30% floor)
# ---------------------------------------------------------------------------


def test_score_task_earned_negative_clamped_in_score5():
    """When the raw earned total (capped positive contributions plus an
    unfloored deduction) is negative, score_5 is clamped at 0 rather than
    going negative.
    """
    items = [
        JudgedItem(score=100, state=ItemState.CONDITION_MET),
        JudgedItem(score=900, state=ItemState.CONDITION_NOT_MET),  # filler
        JudgedItem(score=-150, state=ItemState.CONDITION_MET),
    ]

    result = score_task(items)

    assert result.status == ScoreStatus.SCORED
    assert result.deduction_capped is False  # -150 > -300 floor, not capped
    assert result.earned == Fraction(-50)
    assert result.score_5 == Fraction(0)


# ---------------------------------------------------------------------------
# Exact Fraction arithmetic (no floats, ever)
# ---------------------------------------------------------------------------


def test_score_task_fraction_exactness_not_float():
    """score_5 is an exact Fraction object, not a float approximation, even
    when the true value is a repeating decimal.
    """
    items = [
        JudgedItem(score=20, state=ItemState.CONDITION_MET),
        JudgedItem(score=280, state=ItemState.CONDITION_NOT_MET),  # filler
    ]

    result = score_task(items)

    assert isinstance(result.score_5, Fraction)
    assert isinstance(result.earned, Fraction)
    assert result.score_5 == Fraction(1, 3)
    assert result.earned == Fraction(20)


# ---------------------------------------------------------------------------
# Comprehensive mixed positive/negative scenario spanning the full unit
# ---------------------------------------------------------------------------


def test_score_task_mixed_positive_negative_comprehensive():
    """One item over the 15% cap, one under it, a NO_EVIDENCE item that
    narrows the denominator, an uncapped CONDITION_MET deduction, and two
    negative items (CONDITION_NOT_MET, NO_EVIDENCE) that must have zero
    effect -- exercised together end to end.
    """
    items = [
        JudgedItem(score=200, state=ItemState.CONDITION_MET),  # over cap (97.5)
        JudgedItem(score=50, state=ItemState.CONDITION_MET),  # under cap
        JudgedItem(score=300, state=ItemState.CONDITION_NOT_MET),  # filler, no earn
        JudgedItem(score=100, state=ItemState.NO_EVIDENCE),  # narrows denominator
        JudgedItem(score=-80, state=ItemState.CONDITION_MET),  # deducts, unfloored
        JudgedItem(score=-40, state=ItemState.CONDITION_NOT_MET),  # no effect
        JudgedItem(score=-20, state=ItemState.NO_EVIDENCE),  # no effect
    ]

    result = score_task(items)

    assert result.status == ScoreStatus.SCORED
    assert result.full_base == 650
    assert result.no_evidence_points == 100
    assert result.denominator == 550
    assert result.capped_item_count == 1
    assert result.deduction_capped is False
    assert result.earned == Fraction(135, 2)
    assert result.score_5 == Fraction(27, 44)
    assert result.no_evidence_ratio == Fraction(2, 13)
    assert result.needs_review is False
