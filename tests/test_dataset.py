"""Tests for src/gdpval_eval/dataset.py: parquet parsing (no network).

All fixtures are synthetic pyarrow tables built in-process with fictional
task ids / criterion text — never real GDPval content.
"""

from __future__ import annotations

import hashlib
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gdpval_eval.dataset import (
    DatasetSchemaError,
    RubricParseError,
    load_tasks,
    sha256_file,
)
from gdpval_eval.models import Task

REQUIRED_COLUMNS = (
    "task_id",
    "sector",
    "occupation",
    "prompt",
    "reference_files",
    "reference_file_urls",
    "deliverable_files",
    "deliverable_file_urls",
    "rubric_json",
)


def _row(
    task_id: str,
    rubric_items: list[dict],
    *,
    reference_files: list[str] | None = None,
    reference_file_urls: list[str] | None = None,
    deliverable_files: list[str] | None = None,
    deliverable_file_urls: list[str] | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "sector": "fictional-sector",
        "occupation": "fictional-occupation",
        "prompt": "Fictional synthetic prompt for testing.",
        "reference_files": reference_files if reference_files is not None else ["ref.txt"],
        "reference_file_urls": reference_file_urls
        if reference_file_urls is not None
        else ["https://example.invalid/ref.txt"],
        "deliverable_files": deliverable_files if deliverable_files is not None else ["out.docx"],
        "deliverable_file_urls": deliverable_file_urls
        if deliverable_file_urls is not None
        else ["https://example.invalid/out.docx"],
        "rubric_json": json.dumps(rubric_items),
    }


def _write_parquet(tmp_path, rows: list[dict], *, drop_columns: list[str] | None = None):
    if drop_columns:
        rows = [{k: v for k, v in row.items() if k not in drop_columns} for row in rows]
    table = pa.Table.from_pylist(rows)
    path = tmp_path / "synthetic.parquet"
    pq.write_table(table, path)
    return path


def test_dataset_load_tasks_parses_valid_rows(tmp_path):
    rows = [
        _row(
            "task-0001",
            [
                {
                    "rubric_item_id": "item-0001",
                    "criterion": "Fictional criterion about clear section headings.",
                    "score": 5,
                    "tags": None,
                },
                {
                    "rubric_item_id": "item-0002",
                    "criterion": "Fictional criterion about numeric accuracy.",
                    "score": -3.0,
                    "tags": ["accuracy", "regression"],
                },
            ],
        ),
        _row(
            "task-0002",
            [
                {
                    "rubric_item_id": "item-0003",
                    "criterion": "Fictional criterion about tone consistency.",
                    "score": 2,
                    "tags": [],
                }
            ],
            reference_files=[],
            reference_file_urls=[],
            deliverable_files=["out.xlsx"],
            deliverable_file_urls=["https://example.invalid/out.xlsx"],
        ),
    ]
    path = _write_parquet(tmp_path, rows)

    tasks = load_tasks(path)

    assert len(tasks) == 2
    assert all(isinstance(t, Task) for t in tasks)

    first = tasks[0]
    assert first.task_id == "task-0001"
    assert first.sector == "fictional-sector"
    assert first.occupation == "fictional-occupation"
    assert first.reference_files == ("ref.txt",)
    assert first.deliverable_files == ("out.docx",)
    assert len(first.rubric) == 2

    item0, item1 = first.rubric
    assert item0.rubric_item_id == "item-0001"
    assert item0.score == 5
    assert isinstance(item0.score, int)
    assert item0.tags == ()

    assert item1.rubric_item_id == "item-0002"
    assert item1.score == -3
    assert isinstance(item1.score, int)
    assert item1.tags == ("accuracy", "regression")

    second = tasks[1]
    assert second.task_id == "task-0002"
    assert second.reference_files == ()
    assert second.deliverable_files == ("out.xlsx",)
    assert len(second.rubric) == 1
    assert second.rubric[0].tags == ()


def test_dataset_load_tasks_missing_column_raises_schema_error(tmp_path):
    rows = [_row("task-0001", [{"rubric_item_id": "item-0001", "criterion": "x", "score": 1}])]
    path = _write_parquet(tmp_path, rows, drop_columns=["sector"])

    with pytest.raises(DatasetSchemaError) as exc_info:
        load_tasks(path)

    message = str(exc_info.value)
    assert "sector" in message
    assert "task-0001" not in message


def test_dataset_load_tasks_malformed_json_raises_parse_error(tmp_path):
    secret_task_id = "task-0099"
    bad_row = _row(secret_task_id, [])
    bad_row["rubric_json"] = "{not-valid-json"
    rows = [
        _row("task-0001", [{"rubric_item_id": "item-0001", "criterion": "x", "score": 1}]),
        bad_row,
    ]
    path = _write_parquet(tmp_path, rows)

    with pytest.raises(RubricParseError) as exc_info:
        load_tasks(path)

    message = str(exc_info.value)
    assert "1" in message
    assert secret_task_id not in message


def test_dataset_load_tasks_missing_key_raises_parse_error(tmp_path):
    secret_criterion = "Fictional criterion that must never leak into errors."
    rows = [
        _row(
            "task-0001",
            [{"rubric_item_id": "item-0001", "criterion": secret_criterion, "score": 3}],
        ),
        _row(
            "task-0002",
            # missing "criterion" key entirely
            [{"rubric_item_id": "item-0002", "score": 3}],
        ),
    ]
    path = _write_parquet(tmp_path, rows)

    with pytest.raises(RubricParseError) as exc_info:
        load_tasks(path)

    message = str(exc_info.value)
    assert "1" in message
    assert "0" in message
    assert secret_criterion not in message
    assert "task-0002" not in message


@pytest.mark.parametrize("bad_score", [0, 2.5])
def test_dataset_load_tasks_invalid_score_raises_parse_error(tmp_path, bad_score):
    rows = [
        _row(
            "task-0001",
            [{"rubric_item_id": "item-0001", "criterion": "x", "score": bad_score}],
        )
    ]
    path = _write_parquet(tmp_path, rows)

    with pytest.raises(RubricParseError) as exc_info:
        load_tasks(path)

    message = str(exc_info.value)
    assert "task-0001" not in message
    assert "0" in message


def test_dataset_sha256_file_returns_hex_digest(tmp_path):
    path = tmp_path / "content.bin"
    path.write_bytes(b"hello world")

    digest = sha256_file(path)

    assert digest == hashlib.sha256(b"hello world").hexdigest()
    assert digest == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    assert len(digest) == 64
