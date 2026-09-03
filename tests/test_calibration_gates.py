"""Tests for calibration gate logic (pure functions). Synthetic data only."""

from __future__ import annotations

from fractions import Fraction

from gdpval_eval.calibration_gates import (
    TaskGateInput,
    overall_harness_failure,
    provider_gate_failures,
    task_gate_failures,
)
from gdpval_eval.judge import JudgeConfig
from gdpval_eval.models import ItemState
from gdpval_eval.verdicts import VerdictRecord


def _gate(**overrides) -> TaskGateInput:
    defaults = dict(
        ordinal=3,
        fmt="docx",
        gold=Fraction(4),
        control=Fraction(4),
        truncated=Fraction(2),
        shuffled=Fraction(2),
        flip_rate=Fraction(3, 4),
    )
    defaults.update(overrides)
    return TaskGateInput(**defaults)


def _rec(**overrides) -> VerdictRecord:
    defaults = dict(
        product="calib-gold",
        task_id="task-0001",
        rubric_item_id="item-1",
        attempt=1,
        exam_version="exam_v1",
        channel="text",
        state=ItemState.CONDITION_MET,
        reason=None,
        raw="synthetic",
        cost=0.0,
        served_model="synthetic-model",
        served_provider="SynProvider",
        judged_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return VerdictRecord(**defaults)


_CFG = JudgeConfig(
    model="synthetic-model",
    provider_order=("SynProvider",),
    allow_fallbacks=False,
    temperature=0,
)


def test_calibration_gates_all_green_no_failures():
    failures, notes = task_gate_failures(_gate())
    assert failures == []
    assert all("分差" in n for n in notes)  # 分差 note 恒输出,无失败类 note


def test_calibration_gates_score_gates_each_fail():
    assert task_gate_failures(_gate(gold=Fraction(29, 10)))[0]  # gold < 3.0
    assert task_gate_failures(_gate(control=Fraction(51, 10)))[0]  # |g-c| > 1.0 量具失真
    # 两个变体分差都 <1.0 才失败
    both_weak, _ = task_gate_failures(
        _gate(truncated=Fraction(31, 10), shuffled=Fraction(33, 10))
    )
    assert any("无任何改坏变体" in f for f in both_weak)
    assert task_gate_failures(_gate(flip_rate=Fraction(1, 2)))[0]  # flip < 60%
    none_failures, _ = task_gate_failures(_gate(shuffled=None))
    assert none_failures == ["题3: 存在 task_score=None 的交付物,不可判定"]


def test_calibration_gates_one_detectable_variant_suffices():
    """truncated 分差不足但 shuffled ≥1.0 → 绝对分差闸通过(F1 修法:
    按该题可检出的变体施加)。"""
    failures, notes = task_gate_failures(
        _gate(truncated=Fraction(37, 10), shuffled=Fraction(2))
    )
    assert failures == []
    assert any("control−truncated=0.30" in n for n in notes)


def test_calibration_gates_control_loss_between_02_and_10_is_note_not_failure():
    """量具损耗在 (0.2, 1.0] 区间只出 note,分差基准换 control 后不拦。"""
    failures, notes = task_gate_failures(
        _gate(gold=Fraction(35, 10), control=Fraction(3), truncated=Fraction(19, 10))
    )
    assert failures == []
    assert any("量具损耗" in n for n in notes)


def test_calibration_gates_flip_rate_boundary_60_percent_passes():
    failures, _ = task_gate_failures(_gate(flip_rate=Fraction(60, 100)))
    assert failures == []


def test_calibration_gates_non_pdf_without_met_in_expected_set_fails():
    failures, _ = task_gate_failures(_gate(flip_rate=None))
    assert any("空转" in f for f in failures)


def test_calibration_gates_pdf_skips_flip_rate_with_explicit_note():
    failures, notes = task_gate_failures(
        _gate(fmt="pdf", flip_rate=None, shuffled=Fraction(5, 2))
    )
    assert failures == []
    assert any("pdf" in n for n in notes)

    # pdf 双变体皆为截半类:两个分差都 <1.0 才失败
    failing, _ = task_gate_failures(
        _gate(fmt="pdf", flip_rate=None, truncated=Fraction(33, 10), shuffled=Fraction(7, 2))
    )
    assert any("无任何改坏变体" in f for f in failing)


def test_calibration_gates_provider_gate_catches_model_provider_and_missing():
    records = [
        _rec(rubric_item_id="ok"),
        _rec(rubric_item_id="bad-provider", served_provider="Elsewhere"),
        _rec(rubric_item_id="bad-model", served_model="other-model"),
        _rec(rubric_item_id="missing", served_provider=None, served_model=None),
        _rec(rubric_item_id="incomplete", state=None),  # ignored: not resolved
        _rec(rubric_item_id="det", channel="deterministic"),  # ignored: not text
        _rec(  # ignored: no external call happened
            rubric_item_id="empty",
            state=ItemState.NO_EVIDENCE,
            reason="empty_extraction",
            served_provider=None,
            served_model=None,
        ),
    ]
    failures = provider_gate_failures(records, _CFG)
    assert len(failures) == 3
    assert any("缺 served" in f for f in failures)
    assert any("provider" in f for f in failures)
    assert any("model" in f for f in failures)


def test_calibration_gates_provider_gate_clean_pass():
    assert provider_gate_failures([_rec()], _CFG) == []


def test_calibration_gates_overall_harness_threshold():
    assert overall_harness_failure(100, 2) is None  # 恰 2% 不触发
    assert overall_harness_failure(100, 3) is not None
    assert overall_harness_failure(0, 0) is None
