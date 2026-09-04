"""Tests for src/gdpval_eval/submission_check.py: outbox validation and
deliverable resolution (spec W2b U3).

All task/product identifiers, prompts, and file contents below are
synthetic, invented for this test file only (see AGENTS.md secrecy
rules). Deliverables and materials are tiny .docx files generated on the
fly with python-docx, following the pattern in tests/test_extract.py.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docx import Document as DocxDocument

from gdpval_eval.submission_check import (
    NoDeliverableDeclared,
    SubmissionCheckError,
    check_all_outboxes,
    check_outbox,
    resolve_deliverable,
)

_LONG_TEXT = "Synthetic fictional filler sentence for extraction. " * 5  # > 200 chars


def _docx_bytes(tmp_path: Path, scratch_name: str, text: str) -> bytes:
    path = tmp_path / scratch_name
    doc = DocxDocument()
    doc.add_paragraph(text)
    doc.save(path)
    data = path.read_bytes()
    path.unlink()
    return data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _spec(
    ordinal: int,
    *,
    required_format: str = "docx",
    material_sha256: list[str] | None = None,
    followup_allowance: int = 3,
) -> dict:
    material_sha256 = material_sha256 or []
    return {
        "ordinal": ordinal,
        "required_format": required_format,
        "material_count": len(material_sha256),
        "material_sha256": material_sha256,
        "time_limit_minutes": 30,
        "followup_allowance": followup_allowance,
    }


def _log(
    ordinal: int,
    *,
    outcome: str = "delivered",
    deliverable_filename: str = "",
    followups_used: int = 0,
) -> dict:
    start = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    finish = start + timedelta(minutes=10)
    return {
        "ordinal": ordinal,
        "outcome": outcome,
        "outcome_note": "",
        "product_build": "fictional-build-1.0",
        "started_at": start.isoformat(),
        "finished_at": finish.isoformat(),
        "followups_used": followups_used,
        "deliverable_filename": deliverable_filename,
    }


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _task_dirs(
    tmp_path: Path, product: str, attempt: int, ordinal: int
) -> tuple[Path, Path]:
    inbox_task_dir = tmp_path / "inbox" / f"{product}_{attempt}" / f"t{ordinal:02d}"
    outbox_task_dir = tmp_path / "outbox" / f"{product}_{attempt}" / f"t{ordinal:02d}"
    inbox_task_dir.mkdir(parents=True)
    outbox_task_dir.mkdir(parents=True)
    return inbox_task_dir, outbox_task_dir


def test_submission_check_material_echo_blocks(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 1
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)

    material_bytes = _docx_bytes(tmp_path, "material.docx", _LONG_TEXT)
    materials_dir = inbox_dir / "materials"
    materials_dir.mkdir()
    (materials_dir / "handout.docx").write_bytes(material_bytes)
    material_sha = _sha256(material_bytes)

    _write_json(inbox_dir / "spec.json", _spec(ordinal, material_sha256=[material_sha]))

    # Operator accidentally placed the source material back as the deliverable.
    (outbox_dir / "answer.docx").write_bytes(material_bytes)
    _write_json(
        outbox_dir / "submission.json", _log(ordinal, deliverable_filename="answer.docx")
    )

    report = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )

    assert report.ready_for_grading is False
    assert report.blocking[ordinal] == ("material_echo",)
    assert ordinal not in report.declared
    assert report.by_ordinal[ordinal].status == "blocked"


def test_submission_check_junk_files_excluded_from_candidates(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 2
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)
    _write_json(inbox_dir / "spec.json", _spec(ordinal))

    deliverable_bytes = _docx_bytes(tmp_path, "answer.docx", _LONG_TEXT)
    (outbox_dir / "answer.docx").write_bytes(deliverable_bytes)
    # Real-world Finder/Word/Chrome debris that must never count toward "multiple".
    (outbox_dir / ".DS_Store").write_bytes(b"\x00\x01binary-finder-metadata")
    (outbox_dir / "~$answer.docx").write_bytes(b"word-lockfile-bytes")
    (outbox_dir / "download.crdownload").write_bytes(b"partial-chrome-download")

    _write_json(
        outbox_dir / "submission.json", _log(ordinal, deliverable_filename="answer.docx")
    )

    report = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )

    assert report.ready_for_grading is True
    assert report.blocking == {}
    assert report.by_ordinal[ordinal].status == "ready"
    assert report.by_ordinal[ordinal].deliverable_sha256 == _sha256(deliverable_bytes)


def test_submission_check_attachments_dir_excluded(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 3
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)
    _write_json(inbox_dir / "spec.json", _spec(ordinal))

    deliverable_bytes = _docx_bytes(tmp_path, "answer.docx", _LONG_TEXT)
    (outbox_dir / "answer.docx").write_bytes(deliverable_bytes)

    attachments_dir = outbox_dir / "attachments"
    attachments_dir.mkdir()
    (attachments_dir / "supporting.xlsx").write_bytes(b"not a real xlsx, irrelevant bytes")

    _write_json(
        outbox_dir / "submission.json", _log(ordinal, deliverable_filename="answer.docx")
    )

    report = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )

    assert report.ready_for_grading is True
    assert report.by_ordinal[ordinal].status == "ready"
    assert ordinal not in report.blocking


def test_submission_check_format_mismatch_warns_not_blocks(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 4
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)
    _write_json(inbox_dir / "spec.json", _spec(ordinal, required_format="pptx"))

    deliverable_bytes = _docx_bytes(tmp_path, "answer.docx", _LONG_TEXT)
    (outbox_dir / "answer.docx").write_bytes(deliverable_bytes)
    _write_json(
        outbox_dir / "submission.json", _log(ordinal, deliverable_filename="answer.docx")
    )

    report = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )

    result = report.by_ordinal[ordinal]
    assert result.status == "ready"
    assert ordinal not in report.blocking
    assert "format_mismatch" in result.warnings
    assert "format_mismatch" in report.warnings[ordinal]
    assert report.ready_for_grading is True


def test_submission_check_declared_outcome_skips_blocking_and_resolve_raises(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 5
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)
    _write_json(inbox_dir / "spec.json", _spec(ordinal))

    # Product could not produce any export for this task; no deliverable file exists.
    _write_json(outbox_dir / "submission.json", _log(ordinal, outcome="no_output"))

    report = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )

    assert ordinal not in report.blocking
    assert report.declared[ordinal] == "no_output"
    assert report.ready_for_grading is True
    assert report.by_ordinal[ordinal].status == "declared"

    with pytest.raises(NoDeliverableDeclared):
        resolve_deliverable(
            tmp_path / "outbox", product=product, attempt=attempt, ordinal=ordinal
        )


def test_submission_check_resolve_deliverable_rejects_sha256_drift(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 6
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)
    _write_json(inbox_dir / "spec.json", _spec(ordinal))

    deliverable_bytes = _docx_bytes(tmp_path, "answer.docx", _LONG_TEXT)
    (outbox_dir / "answer.docx").write_bytes(deliverable_bytes)
    _write_json(
        outbox_dir / "submission.json", _log(ordinal, deliverable_filename="answer.docx")
    )

    check_outbox(tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt)

    resolved_path = resolve_deliverable(
        tmp_path / "outbox", product=product, attempt=attempt, ordinal=ordinal
    )
    assert resolved_path == outbox_dir / "answer.docx"

    # Operator swapped in a different export under the same attempt, unnoticed.
    (outbox_dir / "answer.docx").write_bytes(b"a completely different set of bytes entirely")

    with pytest.raises(SubmissionCheckError):
        resolve_deliverable(
            tmp_path / "outbox", product=product, attempt=attempt, ordinal=ordinal
        )


def test_submission_check_dedup_blocks_cross_product_duplicate(tmp_path):
    shared_bytes = _docx_bytes(tmp_path, "shared.docx", _LONG_TEXT)

    for product in ("prod-a", "prod-b"):
        task_dir = tmp_path / "outbox" / f"{product}_1" / "t01"
        task_dir.mkdir(parents=True)
        (task_dir / "answer.docx").write_bytes(shared_bytes)

    report = check_all_outboxes(tmp_path, attempt=1)

    assert report.blocking is True
    assert len(report.duplicate_groups) == 1
    assert set(report.duplicate_groups[0].locations) == {"prod-a:t01", "prod-b:t01"}


def test_submission_check_log_syntax_error_is_log_unparseable(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 7
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)
    _write_json(inbox_dir / "spec.json", _spec(ordinal))

    deliverable_bytes = _docx_bytes(tmp_path, "answer.docx", _LONG_TEXT)
    (outbox_dir / "answer.docx").write_bytes(deliverable_bytes)
    (outbox_dir / "submission.json").write_text("{not valid json,,,", encoding="utf-8")

    report = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )

    assert report.ready_for_grading is False
    assert report.blocking[ordinal] == ("log_unparseable",)


def test_submission_check_missing_deliverable_blocks(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 11
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)
    _write_json(inbox_dir / "spec.json", _spec(ordinal))
    _write_json(
        outbox_dir / "submission.json", _log(ordinal, deliverable_filename="answer.docx")
    )

    report = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )

    assert report.blocking[ordinal] == ("missing",)
    assert report.ready_for_grading is False
    with pytest.raises(SubmissionCheckError):
        resolve_deliverable(
            tmp_path / "outbox", product=product, attempt=attempt, ordinal=ordinal
        )


def test_submission_check_multiple_candidates_block(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 12
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)
    _write_json(inbox_dir / "spec.json", _spec(ordinal))
    (outbox_dir / "answer.docx").write_bytes(_docx_bytes(tmp_path, "a.docx", _LONG_TEXT))
    (outbox_dir / "answer_v2.docx").write_bytes(
        _docx_bytes(tmp_path, "b.docx", _LONG_TEXT + "second")
    )
    _write_json(
        outbox_dir / "submission.json", _log(ordinal, deliverable_filename="answer.docx")
    )

    report = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )

    # Never silently pick one of the two.
    assert report.blocking[ordinal] == ("multiple",)
    assert report.by_ordinal[ordinal].deliverable_filename is None


def test_submission_check_unparseable_deliverable_blocks(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 13
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)
    _write_json(inbox_dir / "spec.json", _spec(ordinal))
    (outbox_dir / "answer.docx").write_bytes(b"this is not a real docx container")
    _write_json(
        outbox_dir / "submission.json", _log(ordinal, deliverable_filename="answer.docx")
    )

    report = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )

    assert report.blocking[ordinal] == ("unparseable",)


def test_submission_check_empty_deliverable_blocks(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 14
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)
    _write_json(inbox_dir / "spec.json", _spec(ordinal))
    (outbox_dir / "answer.docx").write_bytes(_docx_bytes(tmp_path, "c.docx", "tiny"))
    _write_json(
        outbox_dir / "submission.json", _log(ordinal, deliverable_filename="answer.docx")
    )

    report = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )

    # An empty deliverable must never reach the grader: the scoring
    # denominator drops no-evidence points, so a blank file would raise
    # the score instead of lowering it.
    assert report.blocking[ordinal] == ("empty",)
    with pytest.raises(SubmissionCheckError):
        resolve_deliverable(
            tmp_path / "outbox", product=product, attempt=attempt, ordinal=ordinal
        )


def test_submission_check_log_missing_blocks(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 15
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)
    _write_json(inbox_dir / "spec.json", _spec(ordinal))
    (outbox_dir / "answer.docx").write_bytes(_docx_bytes(tmp_path, "d.docx", _LONG_TEXT))

    report = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )

    assert report.blocking[ordinal] == ("log_missing",)


def test_submission_check_declared_filename_mismatch_blocks(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 16
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)
    _write_json(inbox_dir / "spec.json", _spec(ordinal))
    (outbox_dir / "answer.docx").write_bytes(_docx_bytes(tmp_path, "e.docx", _LONG_TEXT))
    _write_json(
        outbox_dir / "submission.json",
        _log(ordinal, deliverable_filename="a_different_export.docx"),
    )

    report = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )

    # The operator logged one filename and left another on disk — which
    # of the two was graded must never be guessed.
    assert report.blocking[ordinal] == ("log_incomplete",)


def test_submission_check_stale_report_is_cleared_on_rerun(tmp_path):
    product, attempt, ordinal = "prod-a", 1, 17
    inbox_dir, outbox_dir = _task_dirs(tmp_path, product, attempt, ordinal)
    _write_json(inbox_dir / "spec.json", _spec(ordinal))
    (outbox_dir / "answer.docx").write_bytes(_docx_bytes(tmp_path, "f.docx", _LONG_TEXT))
    log_path = outbox_dir / "submission.json"
    _write_json(log_path, _log(ordinal, deliverable_filename="answer.docx"))

    first = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )
    assert first.ready_for_grading is True
    assert (outbox_dir / "check_report.json").is_file()

    # The log is invalidated while the deliverable's bytes stay intact.
    log_path.write_text("{ not json", encoding="utf-8")
    second = check_outbox(
        tmp_path / "inbox", tmp_path / "outbox", product=product, attempt=attempt
    )

    assert second.blocking[ordinal] == ("log_unparseable",)
    assert not (outbox_dir / "check_report.json").exists()
    with pytest.raises(SubmissionCheckError):
        resolve_deliverable(
            tmp_path / "outbox", product=product, attempt=attempt, ordinal=ordinal
        )
