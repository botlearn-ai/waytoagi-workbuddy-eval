"""Tests for src/gdpval_eval/submission.py: inbox preparation (spec W2b U1).

All task ids, prompts, and material file names below are synthetic,
invented for this test file only (see AGENTS.md secrecy rules). All URLs
use the reserved example.invalid domain with httpx.MockTransport — no
request ever leaves the process.
"""

from __future__ import annotations

import json

import httpx
import pytest

from gdpval_eval.submission import InboxReport, SubmissionError, build_inbox


def _manifest(task_ids: list[str], *, status: str = "frozen", formats=None) -> dict:
    formats = formats or {tid: ["docx"] for tid in task_ids}
    return {
        "content": {
            "tasks": [
                {
                    "task_id": tid,
                    "occupation": "fictional-occupation",
                    "deliverable_formats": formats[tid],
                    "items": [],
                }
                for tid in task_ids
            ],
        },
        "meta": {"status": status},
    }


def _row(
    task_id: str,
    *,
    prompt: str = "Fictional synthetic prompt for testing.",
    reference_files: tuple[str, ...] = (),
    reference_file_urls: tuple[str, ...] = (),
) -> dict:
    return {
        "task_id": task_id,
        "sector": "fictional-sector",
        "occupation": "fictional-occupation",
        "prompt": prompt,
        "reference_files": reference_files,
        "reference_file_urls": reference_file_urls,
        "deliverable_files": (),
        "deliverable_file_urls": (),
    }


def _reject_any_request(request: httpx.Request) -> httpx.Response:
    raise AssertionError("no network call expected")


def test_submission_idempotent_rerun_no_redownload(tmp_path):
    task_id = "task-fict-0001"
    url = "https://example.invalid/materials/spreadsheet.xlsx"
    body = b"fictional material bytes for idempotency test"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    transport = httpx.MockTransport(handler)
    manifest = _manifest([task_id])
    rows = {
        task_id: _row(
            task_id, reference_files=("spreadsheet.xlsx",), reference_file_urls=(url,)
        )
    }
    dest = tmp_path / "inbox"

    first = build_inbox(manifest, rows, dest, product="prod-a", attempt=1, transport=transport)
    assert isinstance(first, InboxReport)
    assert first.materials_downloaded == 1
    assert first.prepared == 1
    assert first.skipped == 0
    assert len(calls) == 1

    second = build_inbox(manifest, rows, dest, product="prod-a", attempt=1, transport=transport)
    assert second.materials_downloaded == 0
    assert second.materials_repaired == 0
    assert second.skipped == 1
    assert second.prepared == 0
    assert len(calls) == 1  # no new network call on rerun


def test_submission_damaged_material_redownloaded(tmp_path):
    task_id = "task-fict-0002"
    url = "https://example.invalid/materials/notes.docx"
    body = b"fictional good bytes for corruption test"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    transport = httpx.MockTransport(handler)
    manifest = _manifest([task_id])
    rows = {task_id: _row(task_id, reference_files=("notes.docx",), reference_file_urls=(url,))}
    dest = tmp_path / "inbox"

    build_inbox(manifest, rows, dest, product="prod-a", attempt=1, transport=transport)
    material_path = dest / "prod-a_1" / "t01" / "materials" / "notes.docx"
    assert material_path.read_bytes() == body
    material_path.write_bytes(b"corrupted garbage bytes, wrong content entirely")

    report = build_inbox(manifest, rows, dest, product="prod-a", attempt=1, transport=transport)

    assert report.materials_repaired == 1
    assert report.materials_downloaded == 0
    assert material_path.read_bytes() == body
    assert len(calls) == 2


def test_submission_missing_material_redownloaded(tmp_path):
    task_id = "task-fict-0003"
    url = "https://example.invalid/materials/handout.pdf"
    body = b"fictional bytes for missing-file test"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    transport = httpx.MockTransport(handler)
    manifest = _manifest([task_id])
    rows = {task_id: _row(task_id, reference_files=("handout.pdf",), reference_file_urls=(url,))}
    dest = tmp_path / "inbox"

    build_inbox(manifest, rows, dest, product="prod-a", attempt=1, transport=transport)
    material_path = dest / "prod-a_1" / "t01" / "materials" / "handout.pdf"
    material_path.unlink()  # simulate accidental deletion; spec.json baseline untouched

    report = build_inbox(manifest, rows, dest, product="prod-a", attempt=1, transport=transport)

    assert report.materials_downloaded == 1
    assert report.materials_repaired == 0
    assert material_path.read_bytes() == body
    assert len(calls) == 2


def test_submission_task_without_materials_ok(tmp_path):
    task_id = "task-fict-0004"
    manifest = _manifest([task_id])
    rows = {task_id: _row(task_id)}
    dest = tmp_path / "inbox"

    report = build_inbox(
        manifest,
        rows,
        dest,
        product="prod-a",
        attempt=1,
        transport=httpx.MockTransport(_reject_any_request),
    )

    assert report.prepared == 1
    assert report.materials_downloaded == 0
    assert report.materials_repaired == 0

    task_dir = dest / "prod-a_1" / "t01"
    materials_dir = task_dir / "materials"
    assert materials_dir.is_dir()
    assert list(materials_dir.iterdir()) == []

    spec = json.loads((task_dir / "spec.json").read_text(encoding="utf-8"))
    assert spec["ordinal"] == 1
    assert spec["material_count"] == 0
    assert spec["material_sha256"] == []
    assert spec["required_format"] == "docx"
    assert isinstance(spec["time_limit_minutes"], int) and spec["time_limit_minutes"] > 0
    assert isinstance(spec["followup_allowance"], int) and spec["followup_allowance"] > 0


def test_submission_manifest_not_frozen_raises(tmp_path):
    task_id = "task-fict-0005"
    manifest = _manifest([task_id], status="draft-pending-A2")
    rows = {task_id: _row(task_id)}
    dest = tmp_path / "inbox"

    with pytest.raises(SubmissionError):
        build_inbox(
            manifest,
            rows,
            dest,
            product="prod-a",
            attempt=1,
            transport=httpx.MockTransport(_reject_any_request),
        )

    assert not dest.exists()


def test_submission_inbox_report_counts(tmp_path):
    task_a, task_b, task_c = "task-fict-0006a", "task-fict-0006b", "task-fict-0006c"
    url_b = "https://example.invalid/materials/one.xlsx"
    url_c1 = "https://example.invalid/materials/two.pdf"
    url_c2 = "https://example.invalid/materials/three.docx"
    bodies = {
        url_b: b"fictional bytes one",
        url_c1: b"fictional bytes two",
        url_c2: b"fictional bytes three, a little longer",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = bodies[str(request.url)]
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    transport = httpx.MockTransport(handler)
    manifest = _manifest([task_a, task_b, task_c])
    rows = {
        task_a: _row(task_a),
        task_b: _row(task_b, reference_files=("one.xlsx",), reference_file_urls=(url_b,)),
        task_c: _row(
            task_c,
            reference_files=("two.pdf", "three.docx"),
            reference_file_urls=(url_c1, url_c2),
        ),
    }
    dest = tmp_path / "inbox"

    report = build_inbox(manifest, rows, dest, product="prod-a", attempt=1, transport=transport)

    assert report.prepared == 3
    assert report.skipped == 0
    assert report.materials_downloaded == 3
    assert report.materials_repaired == 0


def test_submission_prompt_and_spec_overwritten_each_run(tmp_path):
    task_id = "task-fict-0007"
    manifest = _manifest([task_id])
    rows = {task_id: _row(task_id, prompt="Correct fictional prompt text for overwrite test.")}
    dest = tmp_path / "inbox"

    task_dir = dest / "prod-a_1" / "t01"
    task_dir.mkdir(parents=True)
    (task_dir / "prompt.txt").write_text("STALE leftover prompt content", encoding="utf-8")
    (task_dir / "spec.json").write_text(json.dumps({"stale_field": True}), encoding="utf-8")

    build_inbox(
        manifest,
        rows,
        dest,
        product="prod-a",
        attempt=1,
        transport=httpx.MockTransport(_reject_any_request),
    )

    assert (task_dir / "prompt.txt").read_text(
        encoding="utf-8"
    ) == "Correct fictional prompt text for overwrite test."
    spec = json.loads((task_dir / "spec.json").read_text(encoding="utf-8"))
    assert "stale_field" not in spec
    assert spec["ordinal"] == 1


def test_submission_spec_json_excludes_material_filenames(tmp_path):
    task_id = "task-fict-0008"
    url = "https://example.invalid/materials/confidential_name_xyz.pptx"
    body = b"fictional bytes for confidentiality test"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    manifest = _manifest([task_id])
    rows = {
        task_id: _row(
            task_id,
            reference_files=("confidential_name_xyz.pptx",),
            reference_file_urls=(url,),
        )
    }
    dest = tmp_path / "inbox"

    build_inbox(
        manifest, rows, dest, product="prod-a", attempt=1, transport=httpx.MockTransport(handler)
    )

    task_dir = dest / "prod-a_1" / "t01"
    spec_text = (task_dir / "spec.json").read_text(encoding="utf-8")
    assert "confidential_name_xyz" not in spec_text
    assert (task_dir / "materials" / "confidential_name_xyz.pptx").exists()
