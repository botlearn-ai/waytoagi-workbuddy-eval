"""CLI assembly for the three W1 entry points (spec T8).

Argument parsing and wiring only — all business logic lives in dataset.py,
exam.py, and manifest.py. Each ``*_main`` function returns an int exit code
(0 pass, 1 validation failure, 2 environment/argument error); the
``scripts/`` shims just forward ``sys.argv`` and call ``sys.exit`` on the
result. ``argparse`` itself raises ``SystemExit(0)`` for ``--help`` and
``SystemExit(2)`` for malformed arguments, which already matches this
convention.

Secrecy: none of these functions may print task_id, rubric_item_id,
criterion/prompt text, or a per-item content hash to stdout/stderr — see
AGENTS.md. The single exception is the parquet file's own sha256 (an
aggregate digest of the whole file, not a per-item content hash) and the
manifest's content fingerprint (an aggregate digest of all 20 tasks),
both of which the spec explicitly allows to be printed. Every message
below is built from ordinals, file paths, and aggregate digests only —
callers never pass task_id or criterion text into a print/error string.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from gdpval_eval.crypto import ManifestCryptoError
from gdpval_eval.dataset import download_parquet, load_tasks, sha256_file
from gdpval_eval.exam import ExamReport, load_exam_task_ids, validate_exam
from gdpval_eval.manifest import (
    ManifestFreezeError,
    build_manifest,
    canonical_json,
    freeze_manifest,
)
from gdpval_eval.manifest import verify_manifest as _verify_manifest
from gdpval_eval.models import GdpvalEvalError

_KEY_ENV_VAR = "GDPVAL_MANIFEST_KEY"
_MANIFEST_CRYPTO_ERROR_MESSAGE = (
    "manifest verification failed: could not decrypt or parse the manifest artifact"
)


def _read_manifest_key() -> str | None:
    key = os.environ.get(_KEY_ENV_VAR, "")
    return key or None


def _git_commit_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    sha = result.stdout.strip()
    return sha or None


# --------------------------------------------------------------------------
# verify_dataset
# --------------------------------------------------------------------------


def _build_verify_dataset_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_dataset",
        description="Download (if needed) and validate the GDPval exam parquet "
        "against a frozen per-task expectation file.",
    )
    parser.add_argument(
        "--task-ids", type=Path, required=True, help="path to the 20-line exam task id list"
    )
    parser.add_argument(
        "--expect", type=Path, required=True, help="path to the expected-stats JSON file"
    )
    parser.add_argument(
        "--parquet",
        type=Path,
        default=Path("data/gdpval.parquet"),
        help="path to the GDPval parquet (downloaded here if missing)",
    )
    parser.add_argument(
        "--revision",
        type=str,
        default=None,
        help="upstream commit sha to download; required if --parquet does not already exist",
    )
    parser.add_argument("--verbose", action="store_true", help="print per-task statistics")
    return parser


def _compare_report_to_expected(
    report: ExamReport, task_ids: list[str], expected: dict
) -> list[tuple[str, str]]:
    """Return a list of (label, field) mismatches. label is 'totals' or 'task N'."""
    mismatches: list[tuple[str, str]] = []

    totals = expected.get("totals", {})
    if totals.get("rubric_item_count") != report.totals.total_items:
        mismatches.append(("totals", "total_items"))

    expected_distribution = totals.get("score_distribution", {})
    actual_distribution = {
        str(score): count for score, count in report.totals.score_distribution.items()
    }
    if expected_distribution != actual_distribution:
        mismatches.append(("totals", "score_distribution"))

    expected_tasks = expected.get("tasks", {})
    for stats, task_id in zip(report.per_task, task_ids, strict=True):
        expected_task = expected_tasks.get(task_id, {})
        label = f"task {stats.ordinal}"
        if expected_task.get("rubric_count") != stats.rubric_count:
            mismatches.append((label, "rubric_count"))
        if expected_task.get("full_base") != stats.full_base:
            mismatches.append((label, "full_base"))
        if expected_task.get("format") not in stats.deliverable_formats:
            mismatches.append((label, "format"))

    return mismatches


def verify_dataset_main(argv: list[str]) -> int:
    parser = _build_verify_dataset_parser()
    args = parser.parse_args(argv)

    if not args.parquet.exists() and not args.revision:
        print(
            "error: --revision is required when --parquet does not already exist",
            file=sys.stderr,
        )
        return 2

    try:
        expected = json.loads(args.expect.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"error: cannot read --expect file: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"error: --expect file is not valid JSON: {exc}", file=sys.stderr)
        return 1

    try:
        task_ids = load_exam_task_ids(args.task_ids)
    except GdpvalEvalError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: cannot read --task-ids file: {exc}", file=sys.stderr)
        return 1

    if not args.parquet.exists():
        try:
            download_parquet(args.parquet, args.revision)
        except GdpvalEvalError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    expected_sha = expected.get("source_parquet_sha256")
    if expected_sha:
        actual_sha = sha256_file(args.parquet)
        if actual_sha != expected_sha:
            print(
                f"parquet sha256 mismatch: expected {expected_sha}, got {actual_sha}",
                file=sys.stderr,
            )
            return 1

    try:
        tasks = load_tasks(args.parquet)
        report = validate_exam(tasks, task_ids)
    except GdpvalEvalError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.verbose:
        print("CONFIDENTIAL — per-task stats; do not paste publicly")
        for stats in report.per_task:
            print(
                f"  task {stats.ordinal}: rubric_count={stats.rubric_count} "
                f"full_base={stats.full_base} formats={sorted(stats.deliverable_formats)}"
            )

    mismatches = _compare_report_to_expected(report, task_ids, expected)

    if mismatches:
        print(
            f"verify_dataset: FAIL total_items={report.totals.total_items} "
            f"mismatches={len(mismatches)}"
        )
        for label, field in mismatches:
            print(f"  mismatch: {label} field={field}")
        return 1

    print(f"verify_dataset: PASS total_items={report.totals.total_items} mismatches=0")
    return 0


# --------------------------------------------------------------------------
# freeze_manifest
# --------------------------------------------------------------------------


def _build_freeze_manifest_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="freeze_manifest", description="Build and encrypt-freeze the exam manifest."
    )
    parser.add_argument(
        "--task-ids", type=Path, required=True, help="path to the 20-line exam task id list"
    )
    parser.add_argument(
        "--parquet",
        type=Path,
        default=Path("data/gdpval.parquet"),
        help="path to the already-downloaded GDPval parquet",
    )
    parser.add_argument(
        "--revision",
        type=str,
        required=True,
        help="upstream commit sha, recorded as upstream_revision",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("manifests/"), help="directory for frozen artifacts"
    )
    parser.add_argument("--exam-version", type=str, default="exam_v1")
    parser.add_argument("--judge-model", type=str, default=None)
    parser.add_argument("--judge-provider", type=str, default=None)
    parser.add_argument(
        "--judge-json",
        type=Path,
        default=None,
        help="path to a JSON object recorded verbatim as meta.judge "
        "(mutually exclusive with --judge-model/--judge-provider)",
    )
    parser.add_argument(
        "--plaintext-out",
        type=Path,
        default=Path("secrets/"),
        help="directory for the plaintext manifest copy",
    )
    parser.add_argument(
        "--refreeze", action="store_true", help="overwrite an existing frozen artifact"
    )
    return parser


def freeze_manifest_main(argv: list[str]) -> int:
    parser = _build_freeze_manifest_parser()
    args = parser.parse_args(argv)

    if (args.judge_model is None) != (args.judge_provider is None):
        print(
            "error: --judge-model and --judge-provider must be given together or not at all",
            file=sys.stderr,
        )
        return 2

    if args.judge_json is not None and args.judge_model is not None:
        print(
            "error: --judge-json is mutually exclusive with --judge-model/--judge-provider",
            file=sys.stderr,
        )
        return 2

    key = _read_manifest_key()
    if key is None:
        print(
            f"error: {_KEY_ENV_VAR} environment variable is required and must not be empty",
            file=sys.stderr,
        )
        return 2

    if not args.parquet.exists():
        print(f"error: --parquet file does not exist: {args.parquet}", file=sys.stderr)
        return 2

    try:
        task_ids = load_exam_task_ids(args.task_ids)
        tasks = load_tasks(args.parquet)
        validate_exam(tasks, task_ids)
    except GdpvalEvalError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: cannot read --task-ids file: {exc}", file=sys.stderr)
        return 1

    source_parquet_sha256 = sha256_file(args.parquet)
    git_commit = _git_commit_sha()

    judge: dict | None = None
    if args.judge_json is not None:
        try:
            judge = json.loads(args.judge_json.read_text(encoding="utf-8"))
        except OSError as exc:
            print(f"error: cannot read --judge-json file: {exc}", file=sys.stderr)
            return 2
        except json.JSONDecodeError as exc:
            print(f"error: --judge-json file is not valid JSON: {exc}", file=sys.stderr)
            return 2
        if not isinstance(judge, dict):
            print("error: --judge-json must contain a JSON object", file=sys.stderr)
            return 2
    elif args.judge_model is not None:
        judge = {"model": args.judge_model, "provider": args.judge_provider, "temperature": 0}

    try:
        manifest = build_manifest(
            tasks,
            task_ids,
            exam_version=args.exam_version,
            judge=judge,
            upstream_revision=args.revision,
            source_parquet_sha256=source_parquet_sha256,
        )
    except GdpvalEvalError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        freeze_result = freeze_manifest(
            manifest, key, args.out, git_commit=git_commit, refreeze=args.refreeze
        )
    except ManifestFreezeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    plaintext_dir = args.plaintext_out
    plaintext_dir.mkdir(parents=True, exist_ok=True)
    plaintext_path = plaintext_dir / f"{args.exam_version}.manifest.json"
    plaintext_path.write_bytes(canonical_json(manifest))

    print(f"content_fingerprint={freeze_result.content_fingerprint}")
    print(f"enc_path={freeze_result.enc_path}")
    print(f"fingerprint_path={freeze_result.fingerprint_path}")
    print(f"ledger_path={freeze_result.ledger_path}")
    print(f"plaintext_path={plaintext_path}")
    print(f"status={manifest['meta']['status']}")
    return 0


# --------------------------------------------------------------------------
# verify_manifest
# --------------------------------------------------------------------------


def _build_verify_manifest_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_manifest", description="Decrypt and fingerprint-verify a frozen manifest."
    )
    parser.add_argument("--enc", type=Path, required=True, help="path to the encrypted manifest")
    parser.add_argument(
        "--fingerprint", type=Path, required=True, help="path to the content fingerprint file"
    )
    return parser


def verify_manifest_main(argv: list[str]) -> int:
    parser = _build_verify_manifest_parser()
    args = parser.parse_args(argv)

    key = _read_manifest_key()
    if key is None:
        print(
            f"error: {_KEY_ENV_VAR} environment variable is required and must not be empty",
            file=sys.stderr,
        )
        return 2

    try:
        result = _verify_manifest(args.enc, args.fingerprint, key)
    except ManifestCryptoError:
        print(_MANIFEST_CRYPTO_ERROR_MESSAGE, file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: cannot read manifest artifact: {exc}", file=sys.stderr)
        return 1

    print(
        f"ok={result.ok} exam_version={result.exam_version} "
        f"status={result.status} content_fingerprint={result.content_fingerprint}"
    )
    return 0 if result.ok else 1
