"""Outbox validation and deliverable resolution (spec W2b U3).

This is the sole gate between a human product submission and the grader:
`check_outbox` qualifies every task ordinal in one (product, attempt)
outbox against its inbox contract, and `resolve_deliverable` is the only
way W3 is meant to turn a task ordinal into a file path. `check_all_outboxes`
runs a second, cross-(product, attempt) integrity pass: the same exported
bytes showing up under two different task/product locations is the worst
failure mode this harness can have (it would publish a fabricated
cross-product comparison), and a single (product, attempt) scope can never
see that on its own.

Secrecy: nothing here ever touches task_id, prompt text, criterion text,
rubric_item_id, material file names, or any sha256 value outside of the
in-memory report / the gitignored `check_report.json` it writes. Every
message and report field refers to tasks by ordinal only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gdpval_eval.dataset import sha256_file
from gdpval_eval.extract import ExtractError, extract_document
from gdpval_eval.models import GdpvalEvalError

_CHUNK_SIZE = 1024 * 1024
_TASK_DIR_RE = re.compile(r"^t\d{2}$")
_ALLOWED_EXTS = frozenset({"xlsx", "docx", "pptx", "pdf"})

# Must match grade.py's own empty-extraction guard (`_EMPTY_EXTRACTION_CHARS`):
# an empty deliverable admitted here would shrink grade.py's denominator and
# *raise* the reported score, so it has to be blocked before the grader ever
# sees it rather than "handled" by the existing empty-text guard downstream.
_EMPTY_TEXT_CHARS = 200

_OUTCOME_VALUES = frozenset(
    {"delivered", "no_output", "unsupported_export", "link_only", "product_error"}
)
_REQUIRED_LOG_FIELDS = (
    "ordinal",
    "outcome",
    "outcome_note",
    "product_build",
    "started_at",
    "finished_at",
    "followups_used",
    "deliverable_filename",
)


class SubmissionCheckError(GdpvalEvalError):
    """An outbox task could not be validated or a deliverable could not be
    resolved for a reason that requires human attention (not a declared
    non-deliverable outcome — see `NoDeliverableDeclared` for that).

    Messages carry only task ordinals and category names — never task_id,
    prompt text, file names, or sha256 values.
    """


class NoDeliverableDeclared(GdpvalEvalError):
    """`resolve_deliverable` was called for a task ordinal whose
    `submission.json.outcome` is a declared non-deliverable terminal state
    (anything other than "delivered"). Deliberately not a subclass of
    `SubmissionCheckError`: callers (W3) must be able to catch this and
    record a 0-point, annotated task without that `except` clause also
    swallowing genuine validation failures.
    """


@dataclass(frozen=True)
class TaskCheckResult:
    """Full check outcome for one task ordinal's outbox directory."""

    ordinal: int
    status: str  # "ready" | "declared" | "blocked"
    blocking: tuple[str, ...]
    outcome: str | None
    deliverable_sha256: str | None
    warnings: tuple[str, ...]
    deliverable_filename: str | None = None


@dataclass(frozen=True)
class OutboxReport:
    """Result of `check_outbox` for one (product, attempt)."""

    by_ordinal: dict[int, TaskCheckResult]
    ready_for_grading: bool
    blocking: dict[int, tuple[str, ...]]
    declared: dict[int, str]
    warnings: dict[int, tuple[str, ...]]


@dataclass(frozen=True)
class DuplicateGroup:
    """Task locations (`"{product}:t{NN}"`) sharing one deliverable sha256."""

    locations: tuple[str, ...]


@dataclass(frozen=True)
class DedupReport:
    """Result of `check_all_outboxes` across every (product, attempt)."""

    duplicate_groups: tuple[DuplicateGroup, ...]
    blocking: bool


def check_outbox(
    inbox_root: Path, outbox_root: Path, *, product: str, attempt: int
) -> OutboxReport:
    inbox_product_dir = Path(inbox_root) / f"{product}_{attempt}"
    outbox_product_dir = Path(outbox_root) / f"{product}_{attempt}"

    if not inbox_product_dir.is_dir():
        raise SubmissionCheckError(
            "inbox has not been prepared for this product/attempt"
        )

    ordinals = sorted(
        int(entry.name[1:])
        for entry in inbox_product_dir.iterdir()
        if entry.is_dir() and _TASK_DIR_RE.match(entry.name)
    )

    by_ordinal: dict[int, TaskCheckResult] = {}
    blocking: dict[int, tuple[str, ...]] = {}
    declared: dict[int, str] = {}
    warnings: dict[int, tuple[str, ...]] = {}

    for ordinal in ordinals:
        inbox_task_dir = inbox_product_dir / f"t{ordinal:02d}"
        outbox_task_dir = outbox_product_dir / f"t{ordinal:02d}"
        result = _check_task(inbox_task_dir, outbox_task_dir, ordinal)
        by_ordinal[ordinal] = result

        if result.status == "blocked":
            blocking[ordinal] = result.blocking
        elif result.status == "declared" and result.outcome is not None:
            declared[ordinal] = result.outcome

        if result.warnings:
            warnings[ordinal] = result.warnings

        if result.status in ("ready", "declared"):
            _write_check_report(outbox_task_dir, result)
        else:
            # A stale report from an earlier passing run would let
            # resolve_deliverable hand out a file this run just blocked.
            (outbox_task_dir / "check_report.json").unlink(missing_ok=True)

    return OutboxReport(
        by_ordinal=by_ordinal,
        ready_for_grading=not blocking,
        blocking=blocking,
        declared=declared,
        warnings=warnings,
    )


def resolve_deliverable(
    outbox_root: Path, *, product: str, attempt: int, ordinal: int
) -> Path:
    task_dir = Path(outbox_root) / f"{product}_{attempt}" / f"t{ordinal:02d}"
    report_path = task_dir / "check_report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmissionCheckError(
            f"task ordinal {ordinal}: no usable check_report; run check_outbox first"
        ) from exc

    status = report.get("status")
    if status == "declared":
        raise NoDeliverableDeclared(
            f"task ordinal {ordinal}: outcome is a declared non-deliverable state"
        )
    if status != "ready":
        raise SubmissionCheckError(
            f"task ordinal {ordinal}: check_report does not record a ready deliverable"
        )

    filename = report.get("deliverable_filename")
    recorded_sha256 = report.get("deliverable_sha256")
    if not isinstance(filename, str) or not isinstance(recorded_sha256, str):
        raise SubmissionCheckError(
            f"task ordinal {ordinal}: check_report is missing deliverable identity fields"
        )

    path = _safe_join(task_dir, filename, ordinal)
    if not path.is_file():
        raise SubmissionCheckError(
            f"task ordinal {ordinal}: recorded deliverable is no longer present on disk"
        )

    if sha256_file(path) != recorded_sha256:
        raise SubmissionCheckError(
            f"task ordinal {ordinal}: deliverable sha256 has drifted since check_outbox"
        )

    return path


def check_all_outboxes(submissions_root: Path, *, attempt: int) -> DedupReport:
    outbox_root = Path(submissions_root) / "outbox"
    if not outbox_root.is_dir():
        return DedupReport(duplicate_groups=(), blocking=False)

    suffix = f"_{attempt}"
    sha_to_locations: dict[str, list[str]] = {}

    for product_dir in sorted(outbox_root.iterdir()):
        if not product_dir.is_dir() or not product_dir.name.endswith(suffix):
            continue
        product = product_dir.name[: -len(suffix)]
        if not product:
            continue
        for task_dir in sorted(product_dir.iterdir()):
            if not task_dir.is_dir() or not _TASK_DIR_RE.match(task_dir.name):
                continue
            candidates = _candidate_files(task_dir)
            if len(candidates) != 1:
                continue
            sha256 = sha256_file(candidates[0])
            location = f"{product}:{task_dir.name}"
            sha_to_locations.setdefault(sha256, []).append(location)

    duplicate_groups = tuple(
        DuplicateGroup(locations=tuple(locations))
        for locations in sha_to_locations.values()
        if len(locations) > 1
    )

    return DedupReport(duplicate_groups=duplicate_groups, blocking=bool(duplicate_groups))


def _check_task(
    inbox_task_dir: Path, outbox_task_dir: Path, ordinal: int
) -> TaskCheckResult:
    spec = _load_spec(inbox_task_dir, ordinal)

    log_data, log_status = _load_log(outbox_task_dir / "submission.json")
    if log_status == "missing":
        return TaskCheckResult(ordinal, "blocked", ("log_missing",), None, None, ())
    if log_status == "unparseable":
        return TaskCheckResult(ordinal, "blocked", ("log_unparseable",), None, None, ())
    if log_status == "incomplete":
        outcome = log_data.get("outcome") if isinstance(log_data, dict) else None
        outcome = outcome if outcome in _OUTCOME_VALUES else None
        return TaskCheckResult(ordinal, "blocked", ("log_incomplete",), outcome, None, ())

    assert log_data is not None  # log_status == "ok" implies a parsed dict
    outcome = log_data["outcome"]

    if outcome != "delivered":
        return TaskCheckResult(ordinal, "declared", (), outcome, None, ())

    candidates = _candidate_files(outbox_task_dir)
    if len(candidates) == 0:
        return TaskCheckResult(ordinal, "blocked", ("missing",), outcome, None, ())
    if len(candidates) > 1:
        return TaskCheckResult(ordinal, "blocked", ("multiple",), outcome, None, ())

    candidate = candidates[0]
    declared_name = log_data.get("deliverable_filename")
    if not isinstance(declared_name, str) or Path(declared_name).name != candidate.name:
        return TaskCheckResult(ordinal, "blocked", ("log_incomplete",), outcome, None, ())

    try:
        extracted = extract_document(candidate)
    except ExtractError:
        return TaskCheckResult(ordinal, "blocked", ("unparseable",), outcome, None, ())

    if extracted.facts.text_chars < _EMPTY_TEXT_CHARS:
        return TaskCheckResult(ordinal, "blocked", ("empty",), outcome, None, ())

    deliverable_sha256 = sha256_file(candidate)
    material_hashes = set(spec.get("material_sha256") or ())
    if deliverable_sha256 in material_hashes:
        return TaskCheckResult(ordinal, "blocked", ("material_echo",), outcome, None, ())

    warnings: list[str] = []

    required_format = spec.get("required_format")
    ext = candidate.suffix.lower().lstrip(".")
    if required_format and ext != required_format:
        warnings.append("format_mismatch")

    allowance = spec.get("followup_allowance")
    followups_used = log_data.get("followups_used")
    if (
        isinstance(allowance, int)
        and isinstance(followups_used, int)
        and followups_used > allowance
    ):
        warnings.append("followup_allowance_exceeded")

    if _echoes_material_text(extracted.text, inbox_task_dir / "materials"):
        warnings.append("material_text_echo")

    return TaskCheckResult(
        ordinal,
        "ready",
        (),
        outcome,
        deliverable_sha256,
        tuple(warnings),
        deliverable_filename=candidate.name,
    )


def _candidate_files(task_dir: Path) -> list[Path]:
    """Deliverable candidates directly under `task_dir`.

    Excludes hidden files (Finder `.DS_Store`), Office lock files (`~$...`),
    anything under `attachments/` (implicit: only direct children are ever
    considered), and any extension outside the four supported deliverable
    formats — a stray unsupported export belongs in the `unsupported_export`
    declared outcome, not in this candidate count.
    """
    if not task_dir.is_dir():
        return []
    candidates = []
    for entry in sorted(task_dir.iterdir()):
        if entry.is_dir():
            continue
        name = entry.name
        if name.startswith(".") or name.startswith("~$"):
            continue
        ext = entry.suffix.lower().lstrip(".")
        if ext not in _ALLOWED_EXTS:
            continue
        candidates.append(entry)
    return candidates


def _echoes_material_text(deliverable_text: str, materials_dir: Path) -> bool:
    if not materials_dir.is_dir():
        return False
    for entry in sorted(materials_dir.iterdir()):
        if entry.is_dir():
            continue
        try:
            material_doc = extract_document(entry)
        except ExtractError:
            continue
        if material_doc.text == deliverable_text:
            return True
    return False


def _load_spec(inbox_task_dir: Path, ordinal: int) -> dict:
    spec_path = inbox_task_dir / "spec.json"
    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmissionCheckError(
            f"task ordinal {ordinal}: inbox spec.json missing or unreadable"
        ) from exc
    if not isinstance(data, dict):
        raise SubmissionCheckError(f"task ordinal {ordinal}: inbox spec.json malformed")
    return data


def _load_log(log_path: Path) -> tuple[dict | None, str]:
    """Returns (parsed_data_or_None, status) where status is one of
    "ok" / "missing" / "unparseable" / "incomplete"."""
    if not log_path.exists():
        return None, "missing"
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None, "unparseable"
    if not isinstance(data, dict):
        return None, "unparseable"

    if any(field not in data for field in _REQUIRED_LOG_FIELDS):
        return data, "incomplete"
    if data["outcome"] not in _OUTCOME_VALUES:
        return data, "incomplete"

    try:
        start_dt = datetime.fromisoformat(data["started_at"])
        finish_dt = datetime.fromisoformat(data["finished_at"])
    except (TypeError, ValueError):
        return data, "incomplete"
    if finish_dt <= start_dt:
        return data, "incomplete"

    followups_used = data["followups_used"]
    if not isinstance(followups_used, int) or isinstance(followups_used, bool):
        return data, "incomplete"
    if followups_used < 0:
        return data, "incomplete"

    return data, "ok"


def _write_check_report(outbox_task_dir: Path, result: TaskCheckResult) -> None:
    report = {
        "ordinal": result.ordinal,
        "status": result.status,
        "outcome": result.outcome,
        "deliverable_sha256": result.deliverable_sha256,
        "deliverable_filename": result.deliverable_filename,
        "warnings": list(result.warnings),
    }
    path = outbox_task_dir / "check_report.json"
    path.write_text(
        json.dumps(report, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _safe_join(task_dir: Path, filename: str, ordinal: int) -> Path:
    """Join `filename`'s basename onto `task_dir`, rejecting traversal.

    Only the basename of `filename` is ever used; the resolved path is
    then asserted to still live directly inside `task_dir` before any
    caller opens or hashes it.
    """
    name = Path(filename).name
    if not name or name in {".", ".."}:
        raise SubmissionCheckError(f"task ordinal {ordinal}: invalid deliverable filename")
    resolved = (task_dir / name).resolve()
    if resolved.parent != task_dir.resolve():
        raise SubmissionCheckError(
            f"task ordinal {ordinal}: deliverable path escapes its task directory"
        )
    return resolved


