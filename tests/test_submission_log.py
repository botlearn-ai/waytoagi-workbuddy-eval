"""Tests for src/gdpval_eval/submission_log.py: submission registration (spec W2b U2).

All directory names, outcome notes, and build strings below are synthetic,
invented for this test file only (see AGENTS.md secrecy rules) — nothing
here originates from the real GDPval dataset.
"""

from __future__ import annotations

import json
import warnings
from datetime import UTC, datetime

import pytest

from gdpval_eval.submission_log import (
    SubmissionAlreadyStartedError,
    SubmissionLogError,
    SubmissionNotStartedError,
    finish,
    followup,
    start,
)


def test_submission_log_start_creates_record_with_aware_started_at(tmp_path):
    task_dir = tmp_path / "t01"

    record = start(task_dir)

    submission_path = task_dir / "submission.json"
    assert submission_path.exists()
    assert record["ordinal"] == 1
    assert record["outcome"] is None
    assert record["followups_used"] == 0
    assert record["deliverable_filename"] is None

    parsed = datetime.fromisoformat(record["started_at"])
    assert parsed.tzinfo is not None

    on_disk = json.loads(submission_path.read_text(encoding="utf-8"))
    assert on_disk == record


def test_submission_log_start_twice_does_not_overwrite(tmp_path):
    task_dir = tmp_path / "t02"
    start(task_dir)
    submission_path = task_dir / "submission.json"
    original_bytes = submission_path.read_bytes()

    with pytest.raises(SubmissionAlreadyStartedError):
        start(task_dir)

    assert submission_path.read_bytes() == original_bytes


def test_submission_log_finish_without_start_raises(tmp_path):
    never_started = tmp_path / "t03"

    with pytest.raises(SubmissionNotStartedError):
        finish(
            never_started,
            outcome="delivered",
            note="synthetic note",
            product_build="synthetic-build-1.0",
            deliverable_filename="output.docx",
        )

    # A submission.json that exists but was never stamped by start() (no
    # started_at) must be treated the same as "never started".
    corrupted = tmp_path / "t04"
    corrupted.mkdir()
    (corrupted / "submission.json").write_text(
        json.dumps({"ordinal": 4, "started_at": None}), encoding="utf-8"
    )

    with pytest.raises(SubmissionNotStartedError):
        finish(
            corrupted,
            outcome="delivered",
            note="synthetic note",
            product_build="synthetic-build-1.0",
            deliverable_filename="output.docx",
        )


def test_submission_log_finish_happy_path_sets_fields_and_finished_at(tmp_path):
    task_dir = tmp_path / "t05"
    started = start(task_dir)

    record = finish(
        task_dir,
        outcome="delivered",
        note="synthetic operator note",
        product_build="synthetic-build-2.1",
        deliverable_filename="output.pptx",
    )

    assert record["outcome"] == "delivered"
    assert record["outcome_note"] == "synthetic operator note"
    assert record["product_build"] == "synthetic-build-2.1"
    assert record["deliverable_filename"] == "output.pptx"

    finished_at = datetime.fromisoformat(record["finished_at"])
    started_at = datetime.fromisoformat(started["started_at"])
    assert finished_at.tzinfo is not None
    assert finished_at > started_at

    on_disk = json.loads((task_dir / "submission.json").read_text(encoding="utf-8"))
    assert on_disk == record


def test_submission_log_finish_invalid_outcome_raises(tmp_path):
    task_dir = tmp_path / "t06"
    start(task_dir)

    with pytest.raises(SubmissionLogError):
        finish(
            task_dir,
            outcome="somehow_finished",
            note="synthetic note",
            product_build="synthetic-build-1.0",
            deliverable_filename="output.xlsx",
        )

    # Validation happens before any mutation: finished_at stays unset.
    on_disk = json.loads((task_dir / "submission.json").read_text(encoding="utf-8"))
    assert on_disk["finished_at"] is None


def test_submission_log_followup_increments_and_preserves_other_fields(tmp_path):
    task_dir = tmp_path / "t07"
    started = start(task_dir)

    once = followup(task_dir)
    twice = followup(task_dir)

    assert once["followups_used"] == 1
    assert twice["followups_used"] == 2
    assert twice["ordinal"] == started["ordinal"]
    assert twice["started_at"] == started["started_at"]


def test_submission_log_followup_over_allowance_warns_but_records(tmp_path):
    task_dir = tmp_path / "t08"
    start(task_dir)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        under = followup(task_dir, followup_allowance=1)
    assert under["followups_used"] == 1
    assert len(caught) == 0

    with pytest.warns(UserWarning):
        over = followup(task_dir, followup_allowance=1)
    assert over["followups_used"] == 2


def test_submission_log_finish_requires_finished_at_after_started_at(tmp_path):
    task_dir = tmp_path / "t09"
    fixed = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    start(task_dir, now=lambda: fixed)

    with pytest.raises(SubmissionLogError):
        finish(
            task_dir,
            outcome="delivered",
            note="synthetic note",
            product_build="synthetic-build-1.0",
            deliverable_filename="output.docx",
            now=lambda: fixed,
        )
