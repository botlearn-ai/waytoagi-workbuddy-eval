"""Tests for src/gdpval_eval/manifest.py: build/freeze/verify (spec T7).

All task ids, rubric item ids, and criterion text below are synthetic,
invented for this test file only (see AGENTS.md secrecy rules).
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from gdpval_eval.crypto import decrypt_bytes, encrypt_bytes, generate_key
from gdpval_eval.manifest import (
    ManifestBuildError,
    ManifestFreezeError,
    build_manifest,
    canonical_json,
    content_fingerprint,
    freeze_manifest,
    verify_manifest,
)
from gdpval_eval.models import RubricItem, Task

DEFAULT_CRITERIA: list[list[tuple[str, int]]] = [
    [
        ("The document contains a chart with quarterly revenue figures.", 3),
        ("The font is legible on all slide titles.", 2),
    ],
    [
        ("The worksheet named Summary exists in the workbook.", 4),
        ("The narrative accurately reflects the source data.", 1),
    ],
]


def _task(
    task_idx: int,
    *,
    criteria: list[tuple[str, int]],
    formats: tuple[str, ...] = ("docx",),
) -> Task:
    rubric = tuple(
        RubricItem(
            rubric_item_id=f"synthetic-item-{task_idx:02d}-{item_idx:02d}",
            criterion=criterion,
            score=score,
        )
        for item_idx, (criterion, score) in enumerate(criteria, start=1)
    )
    return Task(
        task_id=f"synthetic-task-{task_idx:02d}",
        sector="fictional-sector",
        occupation="fictional-occupation",
        prompt="Fictional synthetic prompt for testing.",
        reference_files=(),
        reference_file_urls=(),
        deliverable_files=tuple(f"deliverable-{task_idx:02d}.{fmt}" for fmt in formats),
        deliverable_file_urls=(),
        rubric=rubric,
    )


def _default_tasks() -> list[Task]:
    return [_task(i, criteria=criteria) for i, criteria in enumerate(DEFAULT_CRITERIA, start=1)]


def _default_manifest(
    *, judge: dict | None = None, frozen_at: str = "2026-01-01T00:00:00+00:00"
) -> dict:
    tasks = _default_tasks()
    task_ids = [t.task_id for t in tasks]
    return build_manifest(
        tasks,
        task_ids,
        exam_version="exam_test_v1",
        judge=judge,
        upstream_revision="synthetic-revision-deadbeef",
        source_parquet_sha256="0" * 64,
        frozen_at=frozen_at,
    )


def test_manifest_freeze_verify_roundtrip_ok(tmp_path):
    judge = {"provider": "synthetic-provider", "model": "synthetic-model"}
    manifest = _default_manifest(judge=judge)
    key = generate_key()

    result = freeze_manifest(manifest, key, tmp_path, git_commit="abc123def")
    verify_result = verify_manifest(result.enc_path, result.fingerprint_path, key)

    assert verify_result.ok is True
    assert verify_result.content_fingerprint == content_fingerprint(manifest)
    assert verify_result.content_fingerprint == result.content_fingerprint
    assert verify_result.exam_version == "exam_test_v1"
    assert verify_result.status == "frozen"


def test_manifest_verify_detects_tampered_content(tmp_path):
    manifest = _default_manifest()
    key = generate_key()
    result = freeze_manifest(manifest, key, tmp_path, git_commit=None)

    plaintext = decrypt_bytes(result.enc_path.read_bytes(), key)
    tampered = json.loads(plaintext)
    tampered["content"]["upstream_revision"] = "tampered-synthetic-revision"
    result.enc_path.write_bytes(encrypt_bytes(canonical_json(tampered), key))

    verify_result = verify_manifest(result.enc_path, result.fingerprint_path, key)

    assert verify_result.ok is False


def test_manifest_content_fingerprint_ignores_meta():
    tasks = _default_tasks()
    task_ids = [t.task_id for t in tasks]
    common_kwargs = {
        "exam_version": "exam_test_v1",
        "upstream_revision": "synthetic-revision-deadbeef",
        "source_parquet_sha256": "0" * 64,
    }

    manifest_draft = build_manifest(
        tasks, task_ids, judge=None, frozen_at="2026-01-01T00:00:00+00:00", **common_kwargs
    )
    manifest_frozen = build_manifest(
        tasks,
        task_ids,
        judge={"provider": "synthetic-provider", "model": "synthetic-model"},
        frozen_at="2026-06-15T12:34:56+00:00",
        **common_kwargs,
    )

    assert canonical_json(manifest_draft["content"]) == canonical_json(manifest_frozen["content"])
    assert content_fingerprint(manifest_draft) == content_fingerprint(manifest_frozen)
    assert manifest_draft["meta"]["status"] == "draft-pending-A2"
    assert manifest_frozen["meta"]["status"] == "frozen"


def test_manifest_freeze_refuses_overwrite_without_refreeze_flag(tmp_path):
    manifest = _default_manifest()
    key = generate_key()
    freeze_manifest(manifest, key, tmp_path, git_commit="commit-one")

    with pytest.raises(ManifestFreezeError):
        freeze_manifest(manifest, key, tmp_path, git_commit="commit-two")

    ledger_lines_after_reject = (tmp_path / "LEDGER.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines_after_reject) == 1

    freeze_manifest(manifest, key, tmp_path, git_commit="commit-two", refreeze=True)

    ledger_text_after_refreeze = (tmp_path / "LEDGER.jsonl").read_text(encoding="utf-8")
    ledger_lines_after_refreeze = ledger_text_after_refreeze.splitlines()
    assert len(ledger_lines_after_refreeze) == 2


def test_manifest_sentinel_criterion_does_not_leak_into_frozen_artifacts(tmp_path):
    sentinel = "synthetic-sentinel-9f3c7a-do-not-leak"
    tasks = [_task(1, criteria=[(f"A {sentinel} criterion used only for this test.", 5)])]
    task_ids = [tasks[0].task_id]
    manifest = build_manifest(
        tasks,
        task_ids,
        exam_version="exam_test_v1",
        judge=None,
        upstream_revision="synthetic-revision-deadbeef",
        source_parquet_sha256="0" * 64,
        frozen_at="2026-01-01T00:00:00+00:00",
    )
    key = generate_key()

    result = freeze_manifest(manifest, key, tmp_path, git_commit="commit-sentinel")

    assert sentinel not in canonical_json(manifest).decode("utf-8")
    assert sentinel.encode("utf-8") not in result.fingerprint_path.read_bytes()
    assert sentinel.encode("utf-8") not in result.ledger_path.read_bytes()
    assert sentinel.encode("utf-8") not in result.enc_path.read_bytes()


def test_manifest_item_and_task_key_sets_are_exact():
    manifest = _default_manifest()
    task_entries = manifest["content"]["tasks"]

    assert set(manifest.keys()) == {"content", "meta"}
    assert set(manifest["content"].keys()) == {
        "exam_version",
        "upstream_revision",
        "source_parquet_sha256",
        "disclosure_policy",
        "tasks",
    }
    assert set(manifest["meta"].keys()) == {"frozen_at", "judge", "status"}

    assert len(task_entries) == 2
    for task_entry in task_entries:
        assert set(task_entry.keys()) == {"task_id", "occupation", "deliverable_formats", "items"}
        assert task_entry["deliverable_formats"] == sorted(task_entry["deliverable_formats"])
        for item in task_entry["items"]:
            expected_keys = {"rubric_item_id", "content_hash", "score", "channel", "tags"}
            assert set(item.keys()) == expected_keys


def test_manifest_build_missing_task_id_raises():
    tasks = _default_tasks()
    task_ids = [tasks[0].task_id, "synthetic-task-does-not-exist"]

    with pytest.raises(ManifestBuildError) as exc_info:
        build_manifest(
            tasks,
            task_ids,
            exam_version="exam_test_v1",
            judge=None,
            upstream_revision="synthetic-revision-deadbeef",
            source_parquet_sha256="0" * 64,
        )

    message = str(exc_info.value)
    assert "2" in message
    for task in tasks:
        assert task.task_id not in message
        for item in task.rubric:
            assert item.rubric_item_id not in message


def test_manifest_build_duplicate_rubric_item_id_raises():
    tasks = _default_tasks()
    duplicate_id = tasks[0].rubric[0].rubric_item_id  # task ordinal 1, item ordinal 1
    items = list(tasks[1].rubric)
    items[0] = dataclasses.replace(items[0], rubric_item_id=duplicate_id)  # ordinal 2, item 1
    tasks[1] = dataclasses.replace(tasks[1], rubric=tuple(items))
    task_ids = [t.task_id for t in tasks]

    with pytest.raises(ManifestBuildError) as exc_info:
        build_manifest(
            tasks,
            task_ids,
            exam_version="exam_test_v1",
            judge=None,
            upstream_revision="synthetic-revision-deadbeef",
            source_parquet_sha256="0" * 64,
        )

    message = str(exc_info.value)
    assert "2" in message
    assert "1" in message
    for task in tasks:
        assert task.task_id not in message
    assert duplicate_id not in message


def test_manifest_canonical_json_is_insertion_order_independent():
    obj_a = {"b": 2, "a": 1, "c": {"y": 2, "x": 1}}
    obj_b = {"a": 1, "c": {"x": 1, "y": 2}, "b": 2}

    assert canonical_json(obj_a) == canonical_json(obj_b)
    assert not canonical_json(obj_a).endswith(b"\n")
