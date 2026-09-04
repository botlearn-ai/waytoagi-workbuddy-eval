"""Inbox preparation for human product submission (spec W2b U1).

Lays down `submissions/inbox/{product}_{attempt}/t{NN}/{prompt.txt,
materials/, spec.json}` for every task in a frozen manifest, downloading
each task's reference materials (the input attachments given to the
product — never the gold deliverable) and self-healing them across reruns.

Secrecy: this module's return values, exception messages, and any future
logging never carry task_id, prompt text, material file names, or material
sha256 — those live only inside the gitignored `submissions/` tree itself.
Callers key everything off task ordinal (`tNN`, 1-based).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from gdpval_eval.assets import download_task_files
from gdpval_eval.dataset import sha256_file
from gdpval_eval.models import GdpvalEvalError, Task

_CHUNK_SIZE = 1024 * 1024

# Protocol-level defaults shared by every task in the exam. The submission
# protocol manual (U5) documents these to the human operator; there is no
# per-task source for them upstream (manifest and parquet carry neither).
DEFAULT_TIME_LIMIT_MINUTES = 30
DEFAULT_FOLLOWUP_ALLOWANCE = 1


class SubmissionError(GdpvalEvalError):
    """Raised when inbox preparation cannot proceed.

    Messages carry only task ordinals and counts — never task_id, prompt
    text, material file names, or material hashes.
    """


@dataclass(frozen=True)
class InboxReport:
    """Summary of one `build_inbox` call, all counts task/material scoped.

    `prepared` counts tasks whose inbox directory did not exist before this
    call; `skipped` counts tasks whose directory already existed (prompt.txt
    and spec.json are still refreshed for those). `materials_downloaded`
    counts materials that were absent from disk; `materials_repaired`
    counts materials that were present but failed sha256 verification
    against the previous run's recorded hash and had to be re-fetched.
    """

    prepared: int
    skipped: int
    materials_downloaded: int
    materials_repaired: int


def build_inbox(
    manifest: dict,
    rows_by_task_id: dict,
    dest: Path,
    *,
    product: str,
    attempt: int,
    transport: httpx.BaseTransport | None = None,
) -> InboxReport:
    if manifest.get("meta", {}).get("status") != "frozen":
        raise SubmissionError("manifest is not frozen")

    task_entries = manifest["content"]["tasks"]
    product_dir = dest / f"{product}_{attempt}"

    prepared = 0
    skipped = 0
    materials_downloaded = 0
    materials_repaired = 0

    for ordinal, entry in enumerate(task_entries, start=1):
        task_id = entry["task_id"]
        row = rows_by_task_id.get(task_id)
        if row is None:
            raise SubmissionError(f"task ordinal {ordinal}: no matching parquet row")

        task_dir = product_dir / f"t{ordinal:02d}"
        already_existed = task_dir.exists()
        materials_dir = task_dir / "materials"
        materials_dir.mkdir(parents=True, exist_ok=True)

        task = _task_from_row(row)
        spec_path = task_dir / "spec.json"

        downloaded, repaired = _sync_materials(task, materials_dir, spec_path, transport=transport)
        materials_downloaded += downloaded
        materials_repaired += repaired

        (task_dir / "prompt.txt").write_text(task.prompt, encoding="utf-8")

        formats = entry.get("deliverable_formats") or []
        required_format = formats[0] if formats else None

        material_names = [Path(name).name for name in task.reference_files]
        material_hashes = [sha256_file(materials_dir / name) for name in material_names]

        spec = {
            "ordinal": ordinal,
            "required_format": required_format,
            "material_count": len(material_names),
            "material_sha256": material_hashes,
            "time_limit_minutes": DEFAULT_TIME_LIMIT_MINUTES,
            "followup_allowance": DEFAULT_FOLLOWUP_ALLOWANCE,
        }
        _write_json_atomic(spec_path, spec)

        if already_existed:
            skipped += 1
        else:
            prepared += 1

    return InboxReport(
        prepared=prepared,
        skipped=skipped,
        materials_downloaded=materials_downloaded,
        materials_repaired=materials_repaired,
    )




def _task_from_row(row: dict) -> Task:
    return Task(
        task_id=row["task_id"],
        sector=row["sector"],
        occupation=row["occupation"],
        prompt=row["prompt"],
        reference_files=tuple(row["reference_files"] or ()),
        reference_file_urls=tuple(row["reference_file_urls"] or ()),
        deliverable_files=tuple(row["deliverable_files"] or ()),
        deliverable_file_urls=tuple(row["deliverable_file_urls"] or ()),
        rubric=(),
    )


def _sync_materials(
    task: Task,
    materials_dir: Path,
    spec_path: Path,
    *,
    transport: httpx.BaseTransport | None,
) -> tuple[int, int]:
    """Ensure every reference material for `task` is present under
    materials_dir, self-healing corruption. Returns (downloaded, repaired).

    The verification baseline is the `material_sha256` list recorded in
    this task's *previous* spec.json (if any) — assets.py's own existence
    check has no way to distinguish a valid cached file from a corrupted
    one, so that layer is added here. A material missing from disk counts
    as `downloaded`; one present but hash-mismatched counts as `repaired`.
    """
    prev_hashes = _load_prev_material_hashes(spec_path)

    downloaded = 0
    repaired = 0
    for index, name in enumerate(task.reference_files):
        target = materials_dir / Path(name).name
        if not target.exists():
            downloaded += 1
            continue

        if prev_hashes is None:
            # No trustworthy baseline: an on-disk material cannot be
            # verified, so redownload rather than promote unverified
            # bytes into the new baseline.
            target.unlink()
            repaired += 1
            continue

        expected = prev_hashes[index] if index < len(prev_hashes) else None
        if expected is not None and sha256_file(target) != expected:
            target.unlink()
            repaired += 1

    download_task_files(task, materials_dir, kind="reference", transport=transport)

    return downloaded, repaired


def _write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON via temp file + os.replace.

    spec.json doubles as the corruption baseline for this task's
    materials, so a half-written file would leave the next run with no
    way to verify what is on disk.
    """
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def _load_prev_material_hashes(spec_path: Path) -> list[str] | None:
    """Return the recorded baseline, or None when there is none to trust."""
    if not spec_path.exists():
        return None
    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    hashes = data.get("material_sha256")
    if not isinstance(hashes, list):
        return None
    return hashes
