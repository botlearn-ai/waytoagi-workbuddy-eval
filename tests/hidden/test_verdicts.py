"""Hidden contract tests for gdpval_eval.verdicts.VerdictStore (unit U4).

Blind-graded: not visible to the implementer. All data is synthetic (fake
product/task/rubric ids like "synthetic-prod" / "task-0001" / "item-###");
nothing here is a real rubric criterion, task_id, or dataset content.

Contract under test (from the work-unit spec / risk gate "写路径"):
- VerdictStore(root): root's path must contain a directory *segment* named
  exactly "runs" or "data" (substring match on a longer name does not
  count) -> otherwise ValueError.
- Records live at root/{exam_version}/{product}_{attempt}.jsonl, one JSON
  object per line. The file name never carries task_id.
- Key = (product, task_id, rubric_item_id, attempt, exam_version).
  state is None -> incomplete; otherwise -> resolved.
- write(): if the key's latest record is already resolved, any further
  write (regardless of the new record's state) raises
  DuplicateVerdictError and does not modify the file. If the key's latest
  record is incomplete (or the key is new), writing either a resolved or
  an incomplete record is allowed (re-judging); load() must reflect only
  the latest record per key.
- write() is a single "write + flush" of one JSON line, and the whole
  check-existing + append sequence holds an exclusive fcntl.flock on the
  file; if the lock can't be acquired (LOCK_NB), it raises rather than
  blocking.
- resolved_ids() / pending() partition rubric_item_ids by the latest
  record's resolved/incomplete status.
- load() on a file that doesn't exist yet returns [].
- A corrupted trailing line (crash mid-write) is silently ignored by
  load()/resolved_ids()/pending(); earlier valid lines still work.
- state serializes via ItemState.value; None serializes as JSON null and
  deserializes back to None (not the enum, not a string).
"""

from __future__ import annotations

import fcntl
import json
import threading
from pathlib import Path

import pytest

from gdpval_eval.models import ItemState
from gdpval_eval.verdicts import DuplicateVerdictError, VerdictRecord, VerdictStore


def _record(**overrides) -> VerdictRecord:
    """Build a synthetic VerdictRecord with sensible defaults, overridable
    per test.
    """
    fields = {
        "product": "synthetic-prod",
        "task_id": "task-0001",
        "rubric_item_id": "item-001",
        "attempt": 1,
        "exam_version": "exam_v1",
        "channel": "text",
        "state": ItemState.CONDITION_MET,
        "reason": None,
        "raw": "synthetic raw model output",
        "cost": 0.01,
        "served_model": "synthetic-model",
        "served_provider": "synthetic-provider",
        "judged_at": "2026-01-01T00:00:00Z",
    }
    fields.update(overrides)
    return VerdictRecord(**fields)


# ---------------------------------------------------------------------------
# Root path validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "make_root",
    [
        lambda tmp_path: tmp_path / "output",
        lambda tmp_path: tmp_path / "project" / "artifacts",
        lambda _tmp_path: Path("relative") / "output_dir",
    ],
)
def test_verdicts_init_rejects_root_without_runs_or_data_segment(tmp_path, make_root):
    """A root whose path has no "runs" or "data" directory segment is
    rejected before any file I/O.
    """
    with pytest.raises(ValueError):
        VerdictStore(make_root(tmp_path))


@pytest.mark.parametrize("segment", ["database", "metadata", "runsheet", "underrunsx"])
def test_verdicts_init_rejects_substring_match_not_exact_segment(tmp_path, segment):
    """A directory segment that merely *contains* "runs"/"data" as a
    substring (e.g. "database") does not satisfy the requirement -- it must
    be an exact path segment named "runs" or "data".
    """
    with pytest.raises(ValueError):
        VerdictStore(tmp_path / segment)


def test_verdicts_init_accepts_root_with_runs_segment(tmp_path):
    """A root directly named "runs" is accepted."""
    VerdictStore(tmp_path / "runs")


def test_verdicts_init_accepts_root_with_data_segment(tmp_path):
    """A root directly named "data" is accepted."""
    VerdictStore(tmp_path / "data")


def test_verdicts_init_accepts_runs_as_nested_segment(tmp_path):
    """"runs" need not be the immediate child of tmp_path -- any segment in
    the path satisfies the requirement.
    """
    VerdictStore(tmp_path / "project" / "runs" / "eval")


# ---------------------------------------------------------------------------
# Write + load round trip, serialization details
# ---------------------------------------------------------------------------


def test_verdicts_write_then_load_round_trip_all_fields(tmp_path):
    """Every field of a resolved VerdictRecord survives a write/load cycle,
    and the on-disk state is the ItemState's .value string.
    """
    store = VerdictStore(tmp_path / "runs")
    record = _record(
        product="synthetic-prod",
        task_id="task-0011",
        rubric_item_id="item-070",
        attempt=5,
        exam_version="exam_v3",
        channel="text",
        state=ItemState.CONDITION_NOT_MET,
        reason=None,
        raw="synthetic raw judge output text",
        cost=0.1234,
        served_model="synthetic-model-x",
        served_provider="synthetic-provider-y",
        judged_at="2026-02-02T12:00:00Z",
    )
    store.write(record)

    loaded = store.load("synthetic-prod", 5, "exam_v3")
    assert len(loaded) == 1
    got = loaded[0]
    assert got.product == "synthetic-prod"
    assert got.task_id == "task-0011"
    assert got.rubric_item_id == "item-070"
    assert got.attempt == 5
    assert got.exam_version == "exam_v3"
    assert got.channel == "text"
    assert got.state == ItemState.CONDITION_NOT_MET
    assert got.reason is None
    assert got.raw == "synthetic raw judge output text"
    assert got.cost == pytest.approx(0.1234)
    assert got.served_model == "synthetic-model-x"
    assert got.served_provider == "synthetic-provider-y"
    assert got.judged_at == "2026-02-02T12:00:00Z"

    target = tmp_path / "runs" / "exam_v3" / "synthetic-prod_5.jsonl"
    payload = json.loads(target.read_text().strip())
    assert payload["state"] == "condition_not_met"


def test_verdicts_state_none_serialized_as_null_and_read_back_none(tmp_path):
    """An unresolved (state=None) record stores JSON null on disk and
    deserializes back to Python None, not a string.
    """
    store = VerdictStore(tmp_path / "runs")
    product, attempt, exam_version = "synthetic-prod", 1, "exam_v1"
    store.write(
        _record(
            product=product,
            task_id="task-0010",
            rubric_item_id="item-060",
            attempt=attempt,
            exam_version=exam_version,
            state=None,
            reason="judge_unparseable",
        )
    )

    target = tmp_path / "runs" / exam_version / f"{product}_{attempt}.jsonl"
    payload = json.loads(target.read_text().strip())
    assert payload["state"] is None

    loaded = store.load(product, attempt, exam_version)
    assert len(loaded) == 1
    assert loaded[0].state is None
    assert loaded[0].reason == "judge_unparseable"


# ---------------------------------------------------------------------------
# incomplete/resolved re-judge semantics
# ---------------------------------------------------------------------------


def test_verdicts_incomplete_then_resolved_overwrite_load_returns_latest_resolved(tmp_path):
    """A key that starts incomplete may later be resolved; load() then
    returns only the latest (resolved) record for that key.
    """
    store = VerdictStore(tmp_path / "runs")
    key = dict(
        product="synthetic-prod",
        task_id="task-0002",
        rubric_item_id="item-010",
        attempt=1,
        exam_version="exam_v1",
    )

    store.write(_record(**key, state=None, reason="judge_unparseable"))
    assert store.pending("synthetic-prod", 1, "exam_v1") == ["item-010"]
    assert store.resolved_ids("synthetic-prod", 1, "exam_v1") == set()

    store.write(_record(**key, state=ItemState.CONDITION_MET, reason=None))

    loaded = store.load("synthetic-prod", 1, "exam_v1")
    assert len(loaded) == 1
    assert loaded[0].state == ItemState.CONDITION_MET
    assert store.resolved_ids("synthetic-prod", 1, "exam_v1") == {"item-010"}
    assert store.pending("synthetic-prod", 1, "exam_v1") == []


def test_verdicts_incomplete_then_incomplete_allowed_no_duplicate_error(tmp_path):
    """Re-writing an incomplete verdict for a still-incomplete key is
    allowed (no DuplicateVerdictError); the latest incomplete record wins.
    """
    store = VerdictStore(tmp_path / "runs")
    key = dict(
        product="synthetic-prod",
        task_id="task-0003",
        rubric_item_id="item-011",
        attempt=1,
        exam_version="exam_v1",
    )

    store.write(_record(**key, state=None, reason="judge_unparseable"))
    store.write(_record(**key, state=None, reason="provider_mismatch"))

    loaded = store.load("synthetic-prod", 1, "exam_v1")
    assert len(loaded) == 1
    assert loaded[0].state is None
    assert loaded[0].reason == "provider_mismatch"
    assert store.pending("synthetic-prod", 1, "exam_v1") == ["item-011"]


def test_verdicts_resolved_then_resolved_raises_duplicate_and_file_unchanged(tmp_path):
    """A second resolved write for an already-resolved key is rejected and
    leaves the on-disk file byte-for-byte (line-for-line) unchanged.
    """
    store = VerdictStore(tmp_path / "runs")
    key = dict(
        product="synthetic-prod",
        task_id="task-0004",
        rubric_item_id="item-012",
        attempt=1,
        exam_version="exam_v1",
    )

    store.write(_record(**key, state=ItemState.CONDITION_MET))
    target = tmp_path / "runs" / "exam_v1" / "synthetic-prod_1.jsonl"
    lines_before = target.read_text().splitlines()

    with pytest.raises(DuplicateVerdictError):
        store.write(_record(**key, state=ItemState.CONDITION_NOT_MET))

    lines_after = target.read_text().splitlines()
    assert lines_after == lines_before
    assert len(lines_after) == 1


def test_verdicts_resolved_then_incomplete_write_also_raises_duplicate(tmp_path):
    """Once a key is resolved, even attempting to write an incomplete
    record for it is rejected -- resolved is terminal.
    """
    store = VerdictStore(tmp_path / "runs")
    key = dict(
        product="synthetic-prod",
        task_id="task-0005",
        rubric_item_id="item-013",
        attempt=1,
        exam_version="exam_v1",
    )

    store.write(_record(**key, state=ItemState.CONDITION_NOT_MET))

    with pytest.raises(DuplicateVerdictError):
        store.write(_record(**key, state=None, reason="judge_unparseable"))


# ---------------------------------------------------------------------------
# resolved_ids / pending grouping and isolation
# ---------------------------------------------------------------------------


def test_verdicts_resolved_ids_and_pending_partition_mixed_keys(tmp_path):
    """resolved_ids() and pending() correctly partition a mix of resolved
    and incomplete rubric items with no overlap.
    """
    store = VerdictStore(tmp_path / "runs")
    product, attempt, exam_version = "synthetic-prod", 1, "exam_v1"

    store.write(
        _record(
            product=product,
            task_id="task-0006",
            rubric_item_id="item-a",
            attempt=attempt,
            exam_version=exam_version,
            state=ItemState.CONDITION_MET,
        )
    )
    store.write(
        _record(
            product=product,
            task_id="task-0006",
            rubric_item_id="item-b",
            attempt=attempt,
            exam_version=exam_version,
            state=ItemState.CONDITION_NOT_MET,
        )
    )
    store.write(
        _record(
            product=product,
            task_id="task-0006",
            rubric_item_id="item-c",
            attempt=attempt,
            exam_version=exam_version,
            state=None,
            reason="judge_unparseable",
        )
    )
    store.write(
        _record(
            product=product,
            task_id="task-0006",
            rubric_item_id="item-d",
            attempt=attempt,
            exam_version=exam_version,
            state=None,
            reason="over_context",
        )
    )

    resolved = store.resolved_ids(product, attempt, exam_version)
    pending = set(store.pending(product, attempt, exam_version))

    assert resolved == {"item-a", "item-b"}
    assert pending == {"item-c", "item-d"}
    assert resolved.isdisjoint(pending)


def test_verdicts_cross_task_id_independence_same_file(tmp_path):
    """Two different task_ids sharing the same product/attempt/exam_version
    file don't interfere with each other's load/resolve state."""
    store = VerdictStore(tmp_path / "runs")
    product, attempt, exam_version = "synthetic-prod", 1, "exam_v1"

    store.write(
        _record(
            product=product,
            task_id="task-A",
            rubric_item_id="item-x1",
            attempt=attempt,
            exam_version=exam_version,
            state=ItemState.CONDITION_MET,
        )
    )
    store.write(
        _record(
            product=product,
            task_id="task-B",
            rubric_item_id="item-y1",
            attempt=attempt,
            exam_version=exam_version,
            state=None,
            reason="judge_unparseable",
        )
    )

    loaded = {r.rubric_item_id: r for r in store.load(product, attempt, exam_version)}
    assert loaded["item-x1"].task_id == "task-A"
    assert loaded["item-x1"].state == ItemState.CONDITION_MET
    assert loaded["item-y1"].task_id == "task-B"
    assert loaded["item-y1"].state is None

    # Resolving task-B's item must not disturb task-A's already-resolved item.
    store.write(
        _record(
            product=product,
            task_id="task-B",
            rubric_item_id="item-y1",
            attempt=attempt,
            exam_version=exam_version,
            state=ItemState.CONDITION_NOT_MET,
        )
    )

    loaded_after = {r.rubric_item_id: r for r in store.load(product, attempt, exam_version)}
    assert loaded_after["item-x1"].state == ItemState.CONDITION_MET
    assert loaded_after["item-y1"].state == ItemState.CONDITION_NOT_MET


def test_verdicts_different_attempts_isolated(tmp_path):
    """The same rubric_item_id under two different attempts is tracked as
    two independent keys, each in its own file."""
    store = VerdictStore(tmp_path / "runs")
    common = dict(
        product="synthetic-prod",
        task_id="task-0008",
        rubric_item_id="item-040",
        exam_version="exam_v1",
    )

    store.write(_record(**common, attempt=1, state=ItemState.CONDITION_MET))
    store.write(_record(**common, attempt=2, state=None, reason="judge_unparseable"))

    assert store.resolved_ids("synthetic-prod", 1, "exam_v1") == {"item-040"}
    assert store.resolved_ids("synthetic-prod", 2, "exam_v1") == set()
    assert store.pending("synthetic-prod", 2, "exam_v1") == ["item-040"]
    assert store.pending("synthetic-prod", 1, "exam_v1") == []


def test_verdicts_different_exam_versions_isolated(tmp_path):
    """The same rubric_item_id under two different exam_versions is tracked
    as two independent keys, each in its own file."""
    store = VerdictStore(tmp_path / "runs")
    common = dict(
        product="synthetic-prod",
        task_id="task-0009",
        rubric_item_id="item-050",
        attempt=1,
    )

    store.write(_record(**common, exam_version="exam_v1", state=ItemState.CONDITION_MET))
    store.write(_record(**common, exam_version="exam_v2", state=None, reason="over_context"))

    assert store.resolved_ids("synthetic-prod", 1, "exam_v1") == {"item-050"}
    assert store.resolved_ids("synthetic-prod", 1, "exam_v2") == set()
    assert store.pending("synthetic-prod", 1, "exam_v2") == ["item-050"]


def test_verdicts_load_resolved_ids_pending_empty_for_missing_file(tmp_path):
    """Querying a product/attempt/exam_version combination that was never
    written returns empty results, not an error."""
    store = VerdictStore(tmp_path / "runs")

    assert store.load("nonexistent-prod", 1, "exam_v1") == []
    assert store.resolved_ids("nonexistent-prod", 1, "exam_v1") == set()
    assert store.pending("nonexistent-prod", 1, "exam_v1") == []


# ---------------------------------------------------------------------------
# Crash tolerance: corrupted trailing line
# ---------------------------------------------------------------------------


def test_verdicts_tolerates_corrupted_trailing_line(tmp_path):
    """A half-written JSON line left behind by a crashed process (no
    trailing newline, invalid JSON) is silently ignored; earlier valid
    records still load/resolve/pend correctly.
    """
    store = VerdictStore(tmp_path / "runs")
    product, attempt, exam_version = "synthetic-prod", 1, "exam_v1"

    store.write(
        _record(
            product=product,
            task_id="task-0007",
            rubric_item_id="item-good-1",
            attempt=attempt,
            exam_version=exam_version,
            state=ItemState.CONDITION_MET,
        )
    )
    store.write(
        _record(
            product=product,
            task_id="task-0007",
            rubric_item_id="item-good-2",
            attempt=attempt,
            exam_version=exam_version,
            state=None,
            reason="judge_unparseable",
        )
    )

    target = tmp_path / "runs" / exam_version / f"{product}_{attempt}.jsonl"
    with open(target, "a", encoding="utf-8") as fh:
        fh.write('{"product": "synthetic-prod", "task_id": "task-0007", "rubric_i')

    loaded = store.load(product, attempt, exam_version)
    by_item = {r.rubric_item_id: r for r in loaded}
    assert set(by_item) == {"item-good-1", "item-good-2"}
    assert by_item["item-good-1"].state == ItemState.CONDITION_MET
    assert by_item["item-good-2"].state is None

    assert store.resolved_ids(product, attempt, exam_version) == {"item-good-1"}
    assert store.pending(product, attempt, exam_version) == ["item-good-2"]


# ---------------------------------------------------------------------------
# File naming / layout (no task_id leakage)
# ---------------------------------------------------------------------------


def test_verdicts_filenames_never_contain_task_id(tmp_path):
    """Written file (and directory) names never carry the task_id, even
    when it's distinctive enough to be obviously identifiable."""
    root = tmp_path / "runs"
    store = VerdictStore(root)
    secret_task_id = "task-should-not-leak-9999"

    store.write(_record(task_id=secret_task_id, rubric_item_id="item-020"))

    all_paths = list(root.rglob("*"))
    assert all_paths, "expected at least one file to be created"
    for path in all_paths:
        assert secret_task_id not in path.name


def test_verdicts_file_path_layout_matches_spec(tmp_path):
    """The verdict file lives exactly at
    root/{exam_version}/{product}_{attempt}.jsonl."""
    root = tmp_path / "runs"
    store = VerdictStore(root)
    store.write(
        _record(
            product="acme-suite",
            attempt=3,
            exam_version="exam_v7",
            rubric_item_id="item-030",
        )
    )

    expected = root / "exam_v7" / "acme-suite_3.jsonl"
    assert expected.exists()
    assert expected.is_file()


# ---------------------------------------------------------------------------
# Locking: write() must fail fast, not block, when the file lock is held
# ---------------------------------------------------------------------------


def test_verdicts_write_lock_mutual_exclusion_fails_fast(tmp_path):
    """If another file handle holds an exclusive flock on the target
    verdict file, write() must raise promptly (LOCK_NB semantics) rather
    than blocking until the lock is released.
    """
    root = tmp_path / "runs"
    store = VerdictStore(root)
    exam_version, product, attempt = "exam_v1", "synthetic-prod", 1

    # Create the target file via a normal write for an unrelated key.
    store.write(
        _record(
            product=product,
            task_id="task-lock-0",
            rubric_item_id="item-lock-seed",
            attempt=attempt,
            exam_version=exam_version,
            state=ItemState.CONDITION_MET,
        )
    )

    target = root / exam_version / f"{product}_{attempt}.jsonl"
    assert target.exists()

    lock_handle = open(target, "r+", encoding="utf-8")
    fcntl.flock(lock_handle, fcntl.LOCK_EX)
    try:
        result: dict = {}

        def attempt_write() -> None:
            try:
                store.write(
                    _record(
                        product=product,
                        task_id="task-lock-1",
                        rubric_item_id="item-lock-contended",
                        attempt=attempt,
                        exam_version=exam_version,
                        state=ItemState.CONDITION_NOT_MET,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - capture any raised type
                result["error"] = exc
            else:
                result["ok"] = True

        thread = threading.Thread(target=attempt_write, daemon=True)
        thread.start()
        thread.join(timeout=5)

        assert not thread.is_alive(), (
            "write() blocked instead of failing fast when the file lock "
            "was held elsewhere"
        )
        assert "error" in result, "write() should raise when LOCK_NB fails"
    finally:
        fcntl.flock(lock_handle, fcntl.LOCK_UN)
        lock_handle.close()
