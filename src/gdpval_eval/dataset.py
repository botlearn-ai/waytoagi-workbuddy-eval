"""Dataset download and parsing for the GDPval parquet export.

Secrecy: parsed criterion text and task_ids must never end up in an
exception message (see models.py). Row-level failures are located by
parquet row number + in-task item ordinal instead.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq

from gdpval_eval.models import GdpvalEvalError, RubricItem, Task

_DEFAULT_HF_ENDPOINT = "https://huggingface.co"
_CHUNK_SIZE = 1024 * 1024

_REQUIRED_COLUMNS = (
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

_REQUIRED_RUBRIC_KEYS = ("rubric_item_id", "criterion", "score")


class DatasetDownloadError(GdpvalEvalError):
    """Raised when the upstream parquet download does not return HTTP 200."""


class DatasetSchemaError(GdpvalEvalError):
    """Raised when the parquet file is missing a required column."""


class RubricParseError(GdpvalEvalError):
    """Raised when a row's rubric_json is malformed or a rubric item is invalid."""


def download_parquet(dest: Path, revision: str) -> Path:
    endpoint = os.environ.get("HF_ENDPOINT", _DEFAULT_HF_ENDPOINT)
    url = f"{endpoint}/datasets/openai/gdpval/resolve/{revision}/data/train-00000-of-00001.parquet"

    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, follow_redirects=True) as response:
        if response.status_code != 200:
            raise DatasetDownloadError(
                f"parquet download failed with HTTP status {response.status_code}"
            )
        with dest.open("wb") as fh:
            for chunk in response.iter_bytes(_CHUNK_SIZE):
                fh.write(chunk)
    return dest


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_tasks(parquet: Path) -> list[Task]:
    table = pq.read_table(parquet)
    missing = [name for name in _REQUIRED_COLUMNS if name not in table.column_names]
    if missing:
        raise DatasetSchemaError(f"missing required column(s): {', '.join(missing)}")

    tasks: list[Task] = []
    for row_number, row in enumerate(table.to_pylist()):
        rubric = _parse_rubric_json(row["rubric_json"], row_number)
        tasks.append(
            Task(
                task_id=row["task_id"],
                sector=row["sector"],
                occupation=row["occupation"],
                prompt=row["prompt"],
                reference_files=tuple(row["reference_files"] or ()),
                reference_file_urls=tuple(row["reference_file_urls"] or ()),
                deliverable_files=tuple(row["deliverable_files"] or ()),
                deliverable_file_urls=tuple(row["deliverable_file_urls"] or ()),
                rubric=rubric,
            )
        )
    return tasks


def _parse_rubric_json(raw: str | None, row_number: int) -> tuple[RubricItem, ...]:
    try:
        items = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError) as exc:
        raise RubricParseError(f"row {row_number}: malformed rubric_json") from exc

    if not isinstance(items, list):
        raise RubricParseError(f"row {row_number}: rubric_json is not a list")

    return tuple(
        _parse_rubric_item(item, row_number, item_number)
        for item_number, item in enumerate(items)
    )


def _parse_rubric_item(item: Any, row_number: int, item_number: int) -> RubricItem:
    if not isinstance(item, dict):
        raise RubricParseError(
            f"row {row_number}, item {item_number}: rubric item is not an object"
        )

    missing = [key for key in _REQUIRED_RUBRIC_KEYS if key not in item]
    if missing:
        raise RubricParseError(f"row {row_number}, item {item_number}: missing required key(s)")

    score = _coerce_score(item["score"], row_number, item_number)

    tags_raw = item.get("tags")
    tags: tuple[str, ...] = () if tags_raw is None else tuple(tags_raw)

    return RubricItem(
        rubric_item_id=item["rubric_item_id"],
        criterion=item["criterion"],
        score=score,
        tags=tags,
    )


def _coerce_score(raw: Any, row_number: int, item_number: int) -> int:
    if isinstance(raw, bool):
        raise RubricParseError(f"row {row_number}, item {item_number}: score is not a number")
    if isinstance(raw, int):
        score = raw
    elif isinstance(raw, float) and raw.is_integer():
        score = int(raw)
    else:
        raise RubricParseError(
            f"row {row_number}, item {item_number}: score is not an integer value"
        )

    if score == 0:
        raise RubricParseError(f"row {row_number}, item {item_number}: score must not be zero")
    return score
