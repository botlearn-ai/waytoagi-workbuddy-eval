"""Manifest construction, freezing, and verification (spec T7).

Secrecy: task_id, rubric_item_id, criterion text, and content hashes never
appear in exception messages here — items are located by task ordinal
(position in task_ids, 1-based) and in-task item ordinal (position in that
task's rubric, 1-based) only, same convention as exam.py. The manifest
*content* itself intentionally carries rubric_item_id and content_hash (that
disclosure is the point of freezing), but it only ever reaches disk through
`freeze_manifest`, encrypted with Fernet.

`canonical_json` is the single fingerprint preimage function for the whole
repo: stable key order, no ASCII-escaping, minimal separators, no trailing
newline. Any other JSON serialization of the same dict would fingerprint
differently, so every fingerprint- and ciphertext-producing path in this
module must route through it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gdpval_eval.channels import assign_channel
from gdpval_eval.crypto import decrypt_bytes, encrypt_bytes
from gdpval_eval.hashing import rubric_item_hash
from gdpval_eval.models import GdpvalEvalError, Task

DISCLOSURE_POLICY = (
    "After all three products complete round 1: publish the decryption key, "
    "the 20 task_ids, and each item's rubric_item_id/content_hash/channel. "
    "Criterion text remains available only via the upstream HuggingFace dataset."
)


class ManifestBuildError(GdpvalEvalError):
    """Raised when build_manifest's inputs fail structural validation.

    Messages carry only task ordinal / in-task item ordinal — never
    task_id, rubric_item_id, content hashes, or criterion text.
    """


class ManifestFreezeError(GdpvalEvalError):
    """Raised when freeze_manifest's output would silently overwrite an
    existing frozen artifact without --refreeze."""


def canonical_json(obj: object) -> bytes:
    """The single fingerprint/ciphertext preimage function for this repo."""
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def build_manifest(
    tasks: list[Task],
    task_ids: list[str],
    *,
    exam_version: str,
    judge: dict | None,
    upstream_revision: str,
    source_parquet_sha256: str,
    frozen_at: str | None = None,
) -> dict:
    tasks_by_id = {task.task_id: task for task in tasks}
    seen_rubric_item_ids: set[str] = set()
    task_entries: list[dict] = []

    for ordinal, task_id in enumerate(task_ids, start=1):
        task = tasks_by_id.get(task_id)
        if task is None:
            raise ManifestBuildError(
                f"exam task ordinal {ordinal} references a task_id absent from the loaded dataset"
            )

        items: list[dict] = []
        for item_ordinal, item in enumerate(task.rubric, start=1):
            if item.rubric_item_id in seen_rubric_item_ids:
                raise ManifestBuildError(
                    f"exam task ordinal {ordinal} item ordinal {item_ordinal} duplicates a "
                    "rubric_item_id already seen elsewhere in the exam"
                )
            seen_rubric_item_ids.add(item.rubric_item_id)

            items.append(
                {
                    "rubric_item_id": item.rubric_item_id,
                    "content_hash": rubric_item_hash(item.criterion, item.score),
                    "score": item.score,
                    "channel": assign_channel(item.criterion).channel.value,
                    "tags": list(item.tags),
                }
            )

        task_entries.append(
            {
                "task_id": task.task_id,
                "occupation": task.occupation,
                "deliverable_formats": sorted(task.deliverable_formats),
                "items": items,
            }
        )

    content = {
        "exam_version": exam_version,
        "upstream_revision": upstream_revision,
        "source_parquet_sha256": source_parquet_sha256,
        "disclosure_policy": DISCLOSURE_POLICY,
        "tasks": task_entries,
    }

    meta = {
        "frozen_at": frozen_at if frozen_at is not None else datetime.now(UTC).isoformat(),
        "judge": judge,
        "status": "draft-pending-A2" if judge is None else "frozen",
    }

    return {"content": content, "meta": meta}


def content_fingerprint(manifest: dict) -> str:
    return hashlib.sha256(canonical_json(manifest["content"])).hexdigest()


def meta_fingerprint(manifest: dict) -> str:
    return hashlib.sha256(canonical_json(manifest["meta"])).hexdigest()


@dataclass(frozen=True)
class FreezeResult:
    enc_path: Path
    fingerprint_path: Path
    ledger_path: Path
    content_fingerprint: str
    meta_fingerprint: str


def freeze_manifest(
    manifest: dict,
    key: str,
    out_dir: Path,
    *,
    git_commit: str | None,
    refreeze: bool = False,
) -> FreezeResult:
    exam_version = manifest["content"]["exam_version"]
    out_dir.mkdir(parents=True, exist_ok=True)

    enc_path = out_dir / f"{exam_version}.manifest.enc"
    fingerprint_path = out_dir / f"{exam_version}.fingerprint.sha256"
    ledger_path = out_dir / "LEDGER.jsonl"

    if not refreeze and (enc_path.exists() or fingerprint_path.exists()):
        raise ManifestFreezeError(
            f"freeze output already exists for exam_version {exam_version!r}; "
            "pass refreeze=True to overwrite"
        )

    content_fp = content_fingerprint(manifest)
    meta_fp = meta_fingerprint(manifest)

    token = encrypt_bytes(canonical_json(manifest), key)
    enc_path.write_bytes(token)
    fingerprint_path.write_text(content_fp + "\n", encoding="utf-8")

    ledger_entry = {
        "exam_version": exam_version,
        "content_fingerprint": content_fp,
        "meta_fingerprint": meta_fp,
        "utc_time": datetime.now(UTC).isoformat(),
        "git_commit": git_commit,
    }
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(canonical_json(ledger_entry).decode("utf-8"))
        fh.write("\n")

    return FreezeResult(
        enc_path=enc_path,
        fingerprint_path=fingerprint_path,
        ledger_path=ledger_path,
        content_fingerprint=content_fp,
        meta_fingerprint=meta_fp,
    )


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    content_fingerprint: str
    exam_version: str
    status: str


def verify_manifest(enc_path: Path, fingerprint_path: Path, key: str) -> VerifyResult:
    plaintext = decrypt_bytes(enc_path.read_bytes(), key)
    manifest = json.loads(plaintext.decode("utf-8"))

    recomputed = content_fingerprint(manifest)
    expected = fingerprint_path.read_text(encoding="utf-8").strip()

    return VerifyResult(
        ok=recomputed == expected,
        content_fingerprint=recomputed,
        exam_version=manifest["content"]["exam_version"],
        status=manifest["meta"]["status"],
    )
