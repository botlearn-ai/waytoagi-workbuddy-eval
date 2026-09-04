"""Submission registration for human product submission (spec W2b U2).

Machine-stamped counterpart to `submission.py`'s inbox: every human
submission of one task to one product gets a `submission.json` under
`submissions/outbox/{product}_{attempt}/t{NN}/` (shared contract, see the
W2b plan). `start()` stamps `started_at` the moment the operator begins;
`finish()` stamps `finished_at` plus the terminal `outcome`; `followup()`
records each round of clarification the operator sends the product. All
timestamps are machine-generated ISO 8601 with an explicit UTC offset —
never naive — so downstream comparisons (U3, W3) never trip over a
naive/aware `TypeError`.

Secrecy: return values and exception messages here carry only task
ordinals, counts, and outcome values — never task_id, prompt text, or
material/deliverable names or hashes. `outcome_note` and `product_build`
are free-text fields the operator writes about the *product*, not the
task; they are never echoed into an exception message.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from gdpval_eval.models import GdpvalEvalError

_TASK_DIR_RE = re.compile(r"^t(\d+)$")

# Terminal declarations a human operator can record for a task/product pair.
# `delivered` means a scorable file was produced; the other four are
# terminal non-delivery declarations (spec W2b acceptance 2) that route the
# task into W3's declared_no_deliverable bucket instead of an open retry.
OUTCOMES = frozenset(
    {
        "delivered",
        "no_output",
        "unsupported_export",
        "link_only",
        "product_error",
    }
)


class SubmissionLogError(GdpvalEvalError):
    """Raised when submission log state cannot be read or written safely."""


class SubmissionAlreadyStartedError(SubmissionLogError):
    """`start()` found an existing `submission.json` for this task.

    Raised from a bare `FileExistsError` (the file is created with
    `open(path, "x")`) without ever inspecting the existing file's
    contents — a partially-filled or template-looking record can never be
    mistaken for one that is safe to overwrite (red-team FAIL 4: any
    content-based "is this still an empty template" test can misfire and
    silently destroy 30 minutes of real operator work).
    """


class SubmissionNotStartedError(SubmissionLogError):
    """`finish()` or `followup()` was called before `start()` recorded a
    `started_at` for this task (missing `submission.json`, or one present
    without a `started_at`)."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ordinal_from_dir(dir: Path) -> int:
    """Derive the task ordinal from a `t{NN}` directory name.

    The shared contract fixes this layout (`.../t{NN}/submission.json`),
    so the ordinal is read from the directory itself rather than taken as
    a separate parameter — one less value the caller can get out of sync
    with the path it is passing.
    """
    match = _TASK_DIR_RE.match(dir.name)
    if match is None:
        raise SubmissionLogError(
            "submission directory name must look like 't<NN>' to derive the task ordinal"
        )
    return int(match.group(1))


def _write_atomic(path: Path, record: dict) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp")
    text = json.dumps(record, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def _read_record(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SubmissionNotStartedError(
            "submission.json not found for this task; call start() first"
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SubmissionLogError("submission.json is not valid JSON") from exc
    if not isinstance(data, dict) or not data.get("started_at"):
        raise SubmissionNotStartedError(
            "submission.json has no started_at for this task; call start() first"
        )
    return data


def start(dir: Path, *, now: Callable[[], datetime] | None = None) -> dict:
    """Create `submission.json` under `dir` and stamp `started_at`.

    Uses `open(path, "x")` so a second `start()` on an already-registered
    task structurally cannot overwrite it — it raises
    `SubmissionAlreadyStartedError` without reading the existing file.
    """
    dir = Path(dir)
    ordinal = _ordinal_from_dir(dir)
    clock = now or _utc_now

    dir.mkdir(parents=True, exist_ok=True)
    path = dir / "submission.json"

    record = {
        "ordinal": ordinal,
        "outcome": None,
        "outcome_note": None,
        "product_build": None,
        "started_at": clock().isoformat(),
        "finished_at": None,
        "followups_used": 0,
        "deliverable_filename": None,
    }
    text = json.dumps(record, sort_keys=True, ensure_ascii=False, indent=2) + "\n"

    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(text)
    except FileExistsError as exc:
        raise SubmissionAlreadyStartedError(
            "submission.json already exists for this task"
        ) from exc

    return record


def finish(
    dir: Path,
    *,
    outcome: str,
    note: str,
    product_build: str,
    deliverable_filename: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict:
    """Stamp `finished_at` and record the terminal `outcome`.

    Read-modify-write via a temp file + `os.replace` (atomic on the same
    filesystem) — never a half-written `submission.json`. Requires a prior
    `start()` (raises `SubmissionNotStartedError` otherwise) and a
    `finished_at` strictly after the recorded `started_at`.
    """
    if outcome not in OUTCOMES:
        raise SubmissionLogError(
            f"outcome must be one of {sorted(OUTCOMES)}"
        )

    dir = Path(dir)
    path = dir / "submission.json"
    record = _read_record(path)

    started_at = datetime.fromisoformat(record["started_at"])
    clock = now or _utc_now
    finished_dt = clock()
    if finished_dt <= started_at:
        raise SubmissionLogError("finished_at must be after started_at")

    record["outcome"] = outcome
    record["outcome_note"] = note
    record["product_build"] = product_build
    record["deliverable_filename"] = deliverable_filename
    record["finished_at"] = finished_dt.isoformat()

    _write_atomic(path, record)
    return record


def followup(dir: Path, *, followup_allowance: int | None = None) -> dict:
    """Increment `followups_used` by one and persist it.

    Requires a prior `start()`. If `followup_allowance` is given (the
    per-task allowance read from that task's inbox `spec.json`) and the
    new count exceeds it, the increment is still recorded as-is and a
    `UserWarning` is raised — the tool cannot stop an operator from
    sending one more clarification, only make sure it is not silently
    lost from the record.
    """
    dir = Path(dir)
    path = dir / "submission.json"
    record = _read_record(path)

    record["followups_used"] = int(record.get("followups_used") or 0) + 1

    if followup_allowance is not None and record["followups_used"] > followup_allowance:
        warnings.warn(
            "followups_used exceeded followup_allowance for this task; recorded as-is",
            stacklevel=2,
        )

    _write_atomic(path, record)
    return record
