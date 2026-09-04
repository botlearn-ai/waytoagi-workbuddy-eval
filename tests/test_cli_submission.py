"""Tests for the W2b submission-kit CLI wiring (spec W2b U4):
`prepare_inbox_main`, `log_submission_main`, `check_outbox_main` in
src/gdpval_eval/cli.py.

All task ids, product names, prompts, and file contents below are
synthetic, invented for this test file only (see AGENTS.md secrecy
rules). Every assertion that checks stdout/stderr for absence of a
sentinel string is a leak regression test for the CLI layer itself.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from docx import Document as DocxDocument

from gdpval_eval.cli import (
    check_outbox_main,
    log_submission_main,
    prepare_inbox_main,
)

SENTINEL = "synthetic-sentinel-cli-9f2c-do-not-leak"
_LONG_TEXT = "Synthetic fictional filler sentence for extraction. " * 5  # > 200 chars


def _synthetic_manifest(task_ids: list[str], *, status: str = "frozen") -> dict:
    return {
        "content": {
            "exam_version": "exam_cli_submission_test",
            "tasks": [
                {
                    "task_id": tid,
                    "occupation": "fictional-occupation",
                    "deliverable_formats": ["docx"],
                    "items": [],
                }
                for tid in task_ids
            ],
        },
        "meta": {"status": status},
    }


def _synthetic_parquet(path: Path, task_ids: list[str]) -> None:
    rows = [
        {
            "task_id": tid,
            "sector": "fictional-sector",
            "occupation": "fictional-occupation",
            "prompt": f"Fictional synthetic prompt carrying the {SENTINEL} marker for {tid}.",
            "reference_files": [],
            "reference_file_urls": [],
            "deliverable_files": [],
            "deliverable_file_urls": [],
        }
        for tid in task_ids
    ]
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path)


def _docx_bytes(tmp_path: Path, scratch_name: str, text: str) -> bytes:
    path = tmp_path / scratch_name
    doc = DocxDocument()
    doc.add_paragraph(text)
    doc.save(path)
    data = path.read_bytes()
    path.unlink()
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _spec_json(*, required_format: str = "docx", followup_allowance: int = 1) -> dict:
    return {
        "ordinal": 1,
        "required_format": required_format,
        "material_count": 0,
        "material_sha256": [],
        "time_limit_minutes": 30,
        "followup_allowance": followup_allowance,
    }


def _submission_log(
    *, ordinal: int, outcome: str = "delivered", deliverable_filename: str = "answer.docx"
) -> dict:
    started = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    finished = started + timedelta(minutes=10)
    return {
        "ordinal": ordinal,
        "outcome": outcome,
        "outcome_note": "synthetic note",
        "product_build": "fictional-build-1.0",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "followups_used": 0,
        "deliverable_filename": deliverable_filename,
    }


def test_cli_submission_prepare_inbox_pass_then_precondition_errors(tmp_path, capsys):
    task_ids = ["cli-sub-synth-task-0001", "cli-sub-synth-task-0002"]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_synthetic_manifest(task_ids)), encoding="utf-8")
    parquet_path = tmp_path / "synthetic.parquet"
    _synthetic_parquet(parquet_path, task_ids)
    submissions_root = tmp_path / "submissions"

    exit_code = prepare_inbox_main(
        [
            "--manifest-plain",
            str(manifest_path),
            "--parquet",
            str(parquet_path),
            "--product",
            "prod-a",
            "--attempt",
            "1",
            "--submissions-root",
            str(submissions_root),
        ]
    )
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "prepare_inbox" in out
    assert task_ids[0] not in out
    assert SENTINEL not in out

    task1_dir = submissions_root / "inbox" / "prod-a_1" / "t01"
    spec = json.loads((task1_dir / "spec.json").read_text(encoding="utf-8"))
    assert spec["ordinal"] == 1
    assert spec["required_format"] == "docx"
    assert task_ids[0] not in json.dumps(spec)
    prompt_text = (task1_dir / "prompt.txt").read_text(encoding="utf-8")
    assert SENTINEL in prompt_text  # written to the gitignored tree, not stdout

    # Non-frozen manifest is a precondition error, not a validation failure.
    manifest_path.write_text(
        json.dumps(_synthetic_manifest(task_ids, status="draft")), encoding="utf-8"
    )
    draft_exit = prepare_inbox_main(
        [
            "--manifest-plain",
            str(manifest_path),
            "--parquet",
            str(parquet_path),
            "--product",
            "prod-a",
            "--attempt",
            "1",
            "--submissions-root",
            str(submissions_root),
        ]
    )
    draft_err = capsys.readouterr().err
    assert draft_exit == 2
    assert task_ids[0] not in draft_err
    assert SENTINEL not in draft_err

    # Missing --parquet is a precondition error too.
    missing_parquet_exit = prepare_inbox_main(
        [
            "--manifest-plain",
            str(manifest_path),
            "--parquet",
            str(tmp_path / "does-not-exist.parquet"),
            "--product",
            "prod-a",
            "--attempt",
            "1",
            "--submissions-root",
            str(submissions_root),
        ]
    )
    assert missing_parquet_exit == 2


def test_cli_submission_log_submission_start_finish_followup_flow(tmp_path, capsys):
    submissions_root = tmp_path / "submissions"
    common = [
        "--submissions-root",
        str(submissions_root),
        "--product",
        "prod-a",
        "--attempt",
        "1",
    ]

    start_exit = log_submission_main([*common, "--ordinal", "1", "start"])
    assert start_exit == 0
    submission_path = submissions_root / "outbox" / "prod-a_1" / "t01" / "submission.json"
    assert submission_path.exists()

    # x-mode: a second start() on the same task must not overwrite it.
    original = submission_path.read_bytes()
    restart_exit = log_submission_main([*common, "--ordinal", "1", "start"])
    assert restart_exit == 2
    assert submission_path.read_bytes() == original

    # finish() before start() on a different ordinal is a precondition error.
    early_finish_exit = log_submission_main(
        [
            *common,
            "--ordinal",
            "2",
            "finish",
            "--outcome",
            "delivered",
            "--note",
            "synthetic note",
            "--product-build",
            "synthetic-build-1.0",
        ]
    )
    assert early_finish_exit == 2

    finish_exit = log_submission_main(
        [
            *common,
            "--ordinal",
            "1",
            "finish",
            "--outcome",
            "delivered",
            "--note",
            "synthetic note",
            "--product-build",
            "synthetic-build-1.0",
            "--deliverable-filename",
            "answer.docx",
        ]
    )
    assert finish_exit == 0
    record = json.loads(submission_path.read_text(encoding="utf-8"))
    assert record["outcome"] == "delivered"
    assert record["deliverable_filename"] == "answer.docx"

    # followup() reads followup_allowance from the same task's inbox spec.json.
    inbox_spec_path = submissions_root / "inbox" / "prod-a_1" / "t01" / "spec.json"
    _write_json(inbox_spec_path, _spec_json(followup_allowance=1))

    followup1_exit = log_submission_main([*common, "--ordinal", "1", "followup"])
    assert followup1_exit == 0
    capsys.readouterr()

    followup2_exit = log_submission_main([*common, "--ordinal", "1", "followup"])
    followup2_err = capsys.readouterr().err
    # Exceeding the allowance is recorded, not blocked (spec: "记录不检查").
    assert followup2_exit == 0
    assert "warning" in followup2_err.lower()
    record = json.loads(submission_path.read_text(encoding="utf-8"))
    assert record["followups_used"] == 2

    # followup() on a started task with no inbox spec.json passes through cleanly.
    log_submission_main([*common, "--ordinal", "3", "start"])
    no_spec_exit = log_submission_main([*common, "--ordinal", "3", "followup"])
    no_spec_err = capsys.readouterr().err
    assert no_spec_exit == 0
    assert no_spec_err == ""


def test_cli_submission_check_outbox_pass_then_blocked_on_missing_deliverable(
    tmp_path, capsys
):
    submissions_root = tmp_path / "submissions"
    inbox_task_dir = submissions_root / "inbox" / "prod-a_1" / "t01"
    outbox_task_dir = submissions_root / "outbox" / "prod-a_1" / "t01"
    inbox_task_dir.mkdir(parents=True)
    outbox_task_dir.mkdir(parents=True)

    _write_json(inbox_task_dir / "spec.json", _spec_json())
    deliverable_bytes = _docx_bytes(tmp_path, "answer.docx", _LONG_TEXT)
    (outbox_task_dir / "answer.docx").write_bytes(deliverable_bytes)
    _write_json(outbox_task_dir / "submission.json", _submission_log(ordinal=1))

    common = [
        "--submissions-root",
        str(submissions_root),
        "--product",
        "prod-a",
        "--attempt",
        "1",
    ]

    pass_exit = check_outbox_main(common)
    pass_out = capsys.readouterr().out
    assert pass_exit == 0
    assert "题01" in pass_out
    assert "ready_for_grading=True" in pass_out
    assert "answer.docx" not in pass_out
    assert deliverable_bytes.hex() not in pass_out

    # Simulate the deliverable going missing after the operator's export step.
    (outbox_task_dir / "answer.docx").unlink()
    (outbox_task_dir / "check_report.json").unlink(missing_ok=True)

    blocked_exit = check_outbox_main(common)
    blocked_out = capsys.readouterr().out
    assert blocked_exit == 1
    assert "missing" in blocked_out
    assert "ready_for_grading=False" in blocked_out


def test_cli_submission_check_outbox_precondition_error_and_global_dedup(tmp_path, capsys):
    submissions_root = tmp_path / "submissions"

    # Precondition error: no inbox has ever been prepared for this product/attempt.
    unprepared_exit = check_outbox_main(
        [
            "--submissions-root",
            str(submissions_root),
            "--product",
            "prod-never-prepared",
            "--attempt",
            "1",
        ]
    )
    unprepared_err = capsys.readouterr().err
    assert unprepared_exit == 2
    assert unprepared_err != ""

    # Two products accidentally submit the exact same exported bytes for t01.
    shared_bytes = _docx_bytes(tmp_path, "shared.docx", _LONG_TEXT)

    for product in ("prod-a", "prod-b"):
        inbox_task_dir = submissions_root / "inbox" / f"{product}_1" / "t01"
        outbox_task_dir = submissions_root / "outbox" / f"{product}_1" / "t01"
        inbox_task_dir.mkdir(parents=True)
        outbox_task_dir.mkdir(parents=True)
        _write_json(inbox_task_dir / "spec.json", _spec_json())
        (outbox_task_dir / "answer.docx").write_bytes(shared_bytes)
        _write_json(outbox_task_dir / "submission.json", _submission_log(ordinal=1))

    dedup_exit = check_outbox_main(
        [
            "--submissions-root",
            str(submissions_root),
            "--product",
            "prod-a",
            "--attempt",
            "1",
        ]
    )
    dedup_out = capsys.readouterr().out
    assert dedup_exit == 1
    assert "ready_for_grading=True" in dedup_out  # prod-a's own task is fine on its own
    assert "duplicate_groups=1" in dedup_out
    assert "blocking=True" in dedup_out
