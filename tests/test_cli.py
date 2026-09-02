"""Tests for src/gdpval_eval/cli.py: CLI entry-point assembly (spec T8).

All task ids, rubric item ids, and criterion text below are synthetic,
invented for this test file only (see AGENTS.md secrecy rules). Every
assertion that checks CLI stdout/stderr for absence of a sentinel string is
a leak regression test: the underlying secrecy contract lives in
models.py/exam.py/manifest.py, but this file additionally proves the CLI
layer itself never prints task_id or criterion-derived text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gdpval_eval.cli import (
    freeze_manifest_main,
    verify_dataset_main,
    verify_manifest_main,
)
from gdpval_eval.crypto import generate_key

N_TASKS = 20
ITEMS_PER_TASK = 8
SENTINEL = "synthetic-sentinel-4b19e2-do-not-leak"


def _task_id(task_idx: int) -> str:
    return f"cli-synthetic-task-{task_idx:04d}"


def _rubric_items(task_idx: int) -> list[dict]:
    items = []
    for item_idx in range(1, ITEMS_PER_TASK + 1):
        criterion = f"Fictional synthetic criterion {task_idx}-{item_idx}."
        if task_idx == 1 and item_idx == 1:
            criterion = f"Fictional synthetic criterion carrying the {SENTINEL} marker."
        items.append(
            {
                "rubric_item_id": f"cli-synthetic-item-{task_idx:04d}-{item_idx:02d}",
                "criterion": criterion,
                "score": 1,
                "tags": [],
            }
        )
    return items


def _row(task_idx: int) -> dict:
    return {
        "task_id": _task_id(task_idx),
        "sector": "fictional-sector",
        "occupation": "fictional-occupation",
        "prompt": "Fictional synthetic prompt for CLI testing.",
        "reference_files": [],
        "reference_file_urls": [],
        "deliverable_files": [f"deliverable-{task_idx:04d}.docx"],
        "deliverable_file_urls": [f"https://example.invalid/deliverable-{task_idx:04d}.docx"],
        "rubric_json": json.dumps(_rubric_items(task_idx)),
    }


def _write_synthetic_dataset(tmp_path: Path) -> tuple[Path, Path, dict]:
    """Build a synthetic 20-task parquet + matching task-ids file + expect dict."""
    rows = [_row(i) for i in range(1, N_TASKS + 1)]
    table = pa.Table.from_pylist(rows)
    parquet_path = tmp_path / "synthetic.parquet"
    pq.write_table(table, parquet_path)

    task_ids_path = tmp_path / "task_ids.txt"
    task_ids_path.write_text(
        "\n".join(_task_id(i) for i in range(1, N_TASKS + 1)) + "\n", encoding="utf-8"
    )

    expect = {
        "totals": {
            "task_count": N_TASKS,
            "rubric_item_count": N_TASKS * ITEMS_PER_TASK,
            "score_distribution": {"1": N_TASKS * ITEMS_PER_TASK},
        },
        "tasks": {
            _task_id(i): {
                "format": "docx",
                "rubric_count": ITEMS_PER_TASK,
                "full_base": ITEMS_PER_TASK,
            }
            for i in range(1, N_TASKS + 1)
        },
    }

    return parquet_path, task_ids_path, expect


def test_cli_help_flags_exit_zero():
    for main in (verify_dataset_main, freeze_manifest_main, verify_manifest_main):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0


def test_cli_verify_dataset_pass_then_fails_on_mismatch(tmp_path, capsys):
    parquet_path, task_ids_path, expect = _write_synthetic_dataset(tmp_path)
    expect_path = tmp_path / "expect.json"
    expect_path.write_text(json.dumps(expect), encoding="utf-8")

    exit_code = verify_dataset_main(
        [
            "--task-ids",
            str(task_ids_path),
            "--expect",
            str(expect_path),
            "--parquet",
            str(parquet_path),
        ]
    )
    out = capsys.readouterr().out

    assert exit_code == 0
    assert _task_id(1) not in out
    assert SENTINEL not in out

    # Corrupt task ordinal 3's expected full_base -> must now fail and name ordinal 3.
    expect["tasks"][_task_id(3)]["full_base"] = 999
    expect_path.write_text(json.dumps(expect), encoding="utf-8")

    exit_code = verify_dataset_main(
        [
            "--task-ids",
            str(task_ids_path),
            "--expect",
            str(expect_path),
            "--parquet",
            str(parquet_path),
        ]
    )
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "3" in out
    assert _task_id(3) not in out
    assert SENTINEL not in out


def test_cli_freeze_then_verify_manifest_roundtrip(tmp_path, monkeypatch, capsys):
    parquet_path, task_ids_path, _expect = _write_synthetic_dataset(tmp_path)
    out_dir = tmp_path / "manifests"
    plaintext_dir = tmp_path / "secrets"
    monkeypatch.setenv("GDPVAL_MANIFEST_KEY", generate_key())

    freeze_exit = freeze_manifest_main(
        [
            "--task-ids",
            str(task_ids_path),
            "--parquet",
            str(parquet_path),
            "--revision",
            "synthetic-revision-deadbeef",
            "--out",
            str(out_dir),
            "--exam-version",
            "exam_cli_test",
            "--plaintext-out",
            str(plaintext_dir),
        ]
    )
    freeze_out = capsys.readouterr().out

    assert freeze_exit == 0
    enc_path = out_dir / "exam_cli_test.manifest.enc"
    fingerprint_path = out_dir / "exam_cli_test.fingerprint.sha256"
    ledger_path = out_dir / "LEDGER.jsonl"
    plaintext_path = plaintext_dir / "exam_cli_test.manifest.json"
    assert enc_path.exists()
    assert fingerprint_path.exists()
    assert ledger_path.exists()
    assert plaintext_path.exists()
    assert _task_id(1) not in freeze_out
    assert SENTINEL not in freeze_out

    verify_exit = verify_manifest_main(
        ["--enc", str(enc_path), "--fingerprint", str(fingerprint_path)]
    )
    verify_out = capsys.readouterr().out

    assert verify_exit == 0
    assert _task_id(1) not in verify_out
    assert SENTINEL not in verify_out


def test_cli_freeze_manifest_env_and_judge_arg_errors(tmp_path, monkeypatch):
    parquet_path, task_ids_path, _expect = _write_synthetic_dataset(tmp_path)
    monkeypatch.delenv("GDPVAL_MANIFEST_KEY", raising=False)

    missing_key_exit = freeze_manifest_main(
        [
            "--task-ids",
            str(task_ids_path),
            "--parquet",
            str(parquet_path),
            "--revision",
            "synthetic-revision-deadbeef",
            "--out",
            str(tmp_path / "manifests"),
        ]
    )
    assert missing_key_exit == 2

    monkeypatch.setenv("GDPVAL_MANIFEST_KEY", generate_key())
    partial_judge_exit = freeze_manifest_main(
        [
            "--task-ids",
            str(task_ids_path),
            "--parquet",
            str(parquet_path),
            "--revision",
            "synthetic-revision-deadbeef",
            "--out",
            str(tmp_path / "manifests"),
            "--judge-model",
            "synthetic-model",
        ]
    )
    assert partial_judge_exit == 2
