"""Task scoring: three item states plus two protection rules (spec §4.1–4.2)."""

from __future__ import annotations

from fractions import Fraction

from gdpval_eval.models import ItemState, JudgedItem, ScoreStatus, TaskScore

_CAP_RATIO = Fraction(15, 100)
_DEDUCTION_FLOOR_RATIO = Fraction(30, 100)
_REVIEW_RATIO = Fraction(20, 100)


def score_task(items: list[JudgedItem]) -> TaskScore:
    for item in items:
        if not isinstance(item.score, int) or item.score == 0:
            raise ValueError("item score must be a nonzero int")
        if not isinstance(item.state, ItemState):
            raise ValueError("item state must be a known ItemState member")

    full_base = sum(item.score for item in items if item.score > 0)
    no_evidence_points = sum(
        item.score
        for item in items
        if item.score > 0 and item.state == ItemState.NO_EVIDENCE
    )
    denominator = full_base - no_evidence_points

    if denominator == 0:
        no_evidence_ratio = (
            Fraction(no_evidence_points, full_base) if full_base > 0 else Fraction(0)
        )
        return TaskScore(
            status=ScoreStatus.NO_DENOMINATOR,
            score_5=Fraction(0),
            earned=Fraction(0),
            denominator=0,
            full_base=full_base,
            no_evidence_points=no_evidence_points,
            no_evidence_ratio=no_evidence_ratio,
            capped_item_count=0,
            deduction_capped=False,
            needs_review=True,
        )

    cap = _CAP_RATIO * full_base
    earned_positive = Fraction(0)
    capped_item_count = 0
    for item in items:
        if item.score > 0 and item.state == ItemState.CONDITION_MET:
            if item.score > cap:
                earned_positive += cap
                capped_item_count += 1
            else:
                earned_positive += item.score

    deduction_sum = sum(
        item.score
        for item in items
        if item.score < 0 and item.state == ItemState.CONDITION_MET
    )
    deduction_floor = -_DEDUCTION_FLOOR_RATIO * full_base
    deduction_capped = deduction_sum < deduction_floor
    deduction = deduction_floor if deduction_capped else Fraction(deduction_sum)

    earned = earned_positive + deduction
    score_5 = Fraction(5) * max(Fraction(0), earned) / denominator

    no_evidence_ratio = Fraction(no_evidence_points, full_base)
    needs_review = no_evidence_ratio > _REVIEW_RATIO

    return TaskScore(
        status=ScoreStatus.SCORED,
        score_5=score_5,
        earned=earned,
        denominator=denominator,
        full_base=full_base,
        no_evidence_points=no_evidence_points,
        no_evidence_ratio=no_evidence_ratio,
        capped_item_count=capped_item_count,
        deduction_capped=deduction_capped,
        needs_review=needs_review,
    )
