"""Visible sample tests for gdpval_eval.verdicts.VerdictStore (unit U4).

These are guidance examples for the implementer; the authoritative,
comprehensive contract tests live in tests/hidden/test_verdicts.py.

All data below is synthetic: fake product/task/rubric ids, no real
criterion text or dataset content.
"""

from __future__ import annotations

import pytest

from gdpval_eval.models import ItemState
from gdpval_eval.verdicts import DuplicateVerdictError, VerdictRecord, VerdictStore


def _record(**overrides) -> VerdictRecord:
    """Build a synthetic VerdictRecord with sensible defaults, overridable
    per test.
    """
    fields = {
        "product": "synthetic-prod",
        "task_id": "task-0001",
        "rubric_item_id": "item-001",
        "attempt": 1,
        "exam_version": "exam_v1",
        "channel": "text",
        "state": ItemState.CONDITION_MET,
        "reason": None,
        "raw": "synthetic raw model output",
        "cost": 0.01,
        "served_model": "synthetic-model",
        "served_provider": "synthetic-provider",
        "judged_at": "2026-01-01T00:00:00Z",
    }
    fields.update(overrides)
    return VerdictRecord(**fields)


def test_verdict_store_write_then_load_round_trip(tmp_path):
    """A resolved record written to the store round-trips through load()
    with all fields intact, including the ItemState enum.
    """
    store = VerdictStore(tmp_path / "runs")
    record = _record()

    store.write(record)
    loaded = store.load("synthetic-prod", 1, "exam_v1")

    assert len(loaded) == 1
    assert loaded[0].rubric_item_id == "item-001"
    assert loaded[0].state == ItemState.CONDITION_MET
    assert loaded[0].task_id == "task-0001"


def test_verdict_store_resolved_ids_reflects_written_item(tmp_path):
    """After writing a resolved verdict, its rubric_item_id shows up in
    resolved_ids() for that product/attempt/exam_version.
    """
    store = VerdictStore(tmp_path / "runs")
    store.write(_record(rubric_item_id="item-042", state=ItemState.CONDITION_NOT_MET))

    resolved = store.resolved_ids("synthetic-prod", 1, "exam_v1")

    assert resolved == {"item-042"}


def test_verdict_store_rejects_duplicate_resolved_write(tmp_path):
    """Writing a second resolved verdict for the same key is rejected and
    does not modify the already-stored verdict.
    """
    store = VerdictStore(tmp_path / "runs")
    key_kwargs = dict(
        product="synthetic-prod",
        task_id="task-0001",
        rubric_item_id="item-007",
        attempt=1,
        exam_version="exam_v1",
    )
    store.write(_record(**key_kwargs, state=ItemState.CONDITION_MET))

    with pytest.raises(DuplicateVerdictError):
        store.write(_record(**key_kwargs, state=ItemState.CONDITION_NOT_MET))

    loaded = store.load("synthetic-prod", 1, "exam_v1")
    assert len(loaded) == 1
    assert loaded[0].state == ItemState.CONDITION_MET
