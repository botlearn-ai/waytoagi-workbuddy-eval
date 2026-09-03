"""Hidden contract tests for gdpval_eval.grade.grade_deliverable (unit U5).

Blind-graded: not visible to the implementer. All manifests, task_ids,
rubric_item_ids, criterion strings, and deliverable content below are
invented synthetic fixtures -- nothing here is drawn from the real GDPval
dataset, per repo secrecy rules (see AGENTS.md).

Contract under test (from .claude/plans/gdpval-grading-w2.md unit U5, and
the grade.py stub's GradeResult/GradeAbortError/CallBudget shapes):

- Resume: store.resolved_ids(product, attempt, exam_version) is consulted
  first; already-resolved rubric_item_ids are never reprocessed (and, for
  the text channel, never trigger an HTTP call), for every channel.
- extract.extract_document(deliverable) failing (ExtractError) marks every
  *unresolved* item incomplete(state=None, reason="extract_error"), sends
  no HTTP request, and yields task_score=None.
- Empty-text guard: a successfully parsed deliverable with
  facts.text_chars < 200 marks every unresolved TEXT-channel item
  NO_EVIDENCE(reason="empty_extraction") without any HTTP call;
  deterministic/vision items are unaffected; GradeResult.empty_extraction
  is True. Strictly "<": exactly 200 chars does not trigger it.
- Channel routing (for items not already resolved):
    * vision -> NO_EVIDENCE(reason="vision_channel_deferred"), no HTTP.
    * deterministic -> checker_params.get(rubric_item_id) missing ->
      incomplete(reason="checker_unconfigured"); checkers.run_check(...)
      returning None -> incomplete(reason="checker_inapplicable");
      otherwise the returned ItemState is stored verbatim.
    * text -> judge.judge_item(...); outcome.state is None ->
      incomplete(reason=outcome.incomplete_reason); NO_EVIDENCE / MET /
      NOT_MET states are stored as-is; any exception raised out of
      judge_item is caught at the item boundary -> incomplete(reason=
      "judge_exception"), never propagated out of grade_deliverable.
- call_budget: judge_item is only invoked after CallBudget.try_acquire()
  returns True; once the budget is exhausted, that item and every
  remaining text item become incomplete(reason="budget_exhausted") with
  no further HTTP calls for them.
- Circuit breaker: 10 consecutive failed text judgements (incomplete-class
  outcomes, including exceptions) raise GradeAbortError; verdicts already
  written before the trip remain in the store.
- Every newly-executed item (not resume-skipped) is written to the
  VerdictStore exactly once.
- Summary is read back from store.load(product, attempt, exam_version),
  scoped to this task's task_id/items: resolved counts MET/NOT_MET/
  NO_EVIDENCE; incomplete counts state=None; task_score is computed via
  scoring.score_task(...) over all of the task's items in manifest order,
  only when incomplete == 0 (else None).
- harness_failure_ratio = Fraction(incomplete_count + count of NO_EVIDENCE
  items whose reason is "over_context" or "empty_extraction", total item
  count for the task).
- cost = sum of .cost from judge_item calls actually made *this run*
  (a resume-skipped item's previously stored cost is excluded).

Deterministic-channel configuration shape: checker_params is typed
`dict[str, dict]` (rubric_item_id -> dict). Since checkers.run_check(check,
params, doc) needs both a check name and a params dict, and neither is
available anywhere else in grade_deliverable's inputs (not on the manifest
item, not in criterion_by_item_id), each per-item entry here is built as
`{"check": "<name>", "params": {...}}`, mirroring run_check's own argument
names -- the only shape that lets the orchestrator drive run_check at all.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import httpx
import pytest
from docx import Document as DocxDocument
from pypdf import PdfWriter

from gdpval_eval.grade import CallBudget, GradeAbortError, grade_deliverable
from gdpval_eval.models import ItemState, JudgedItem
from gdpval_eval.scoring import score_task
from gdpval_eval.verdicts import VerdictRecord, VerdictStore

DEFAULT_MODEL = "deepseek/deepseek-v4-pro-0813"
DEFAULT_PROVIDER = "DeepSeek"


# ---------------------------------------------------------------------------
# Fixtures / helpers (all synthetic)
# ---------------------------------------------------------------------------


def judge_meta() -> dict:
    return {
        "model": DEFAULT_MODEL,
        "provider_order": [DEFAULT_PROVIDER],
        "allow_fallbacks": False,
        "temperature": 0,
    }


def item(rubric_item_id: str, score: int, channel: str) -> dict:
    return {
        "rubric_item_id": rubric_item_id,
        "content_hash": f"synthetic-hash-{rubric_item_id}",
        "score": score,
        "channel": channel,
        "tags": [],
    }


def manifest_with_tasks(tasks: list[dict], exam_version: str = "exam_v1") -> dict:
    return {
        "content": {"exam_version": exam_version, "tasks": tasks},
        "meta": {"judge": judge_meta()},
    }


def manifest(task_id: str, items: list[dict], exam_version: str = "exam_v1") -> dict:
    return manifest_with_tasks([{"task_id": task_id, "items": items}], exam_version=exam_version)


def or_response(content: str, *, cost: float = 0.01, finish_reason: str = "stop") -> dict:
    return {
        "id": "gen-hidden-synthetic",
        "model": DEFAULT_MODEL,
        "provider": DEFAULT_PROVIDER,
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
        ],
        "usage": {"include": True, "cost": cost},
    }


class CriterionRoutingTransport:
    """Returns a canned response chosen by which registered criterion
    substring is present in the outgoing prompt text, so tests are robust
    to whatever order grade_deliverable's internal concurrency issues
    requests in (matching is content-based, not arrival-order-based).
    """

    def __init__(self) -> None:
        self._routes: dict[str, tuple[int, dict]] = {}
        self.requests: list[httpx.Request] = []
        self._lock = threading.Lock()

    def route(self, criterion: str, status: int, body: dict) -> None:
        self._routes[criterion] = (status, body)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        with self._lock:
            self.requests.append(request)
        payload = json.loads(request.content)
        combined = "\n".join(m["content"] for m in payload["messages"])
        for criterion, (status, body) in self._routes.items():
            if criterion in combined:
                return httpx.Response(status, json=body)
        raise AssertionError("CriterionRoutingTransport: no route matched request body")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handler)

    @property
    def calls(self) -> int:
        with self._lock:
            return len(self.requests)


class FixedTransport:
    """Returns the same canned (status, json body) response -- or raises
    the same exception -- for every request, regardless of content.
    Thread-safe call counting.
    """

    def __init__(
        self,
        *,
        status: int | None = None,
        body: dict | None = None,
        exc: BaseException | None = None,
    ) -> None:
        self._status = status
        self._body = body
        self._exc = exc
        self._count = 0
        self._lock = threading.Lock()

    def _handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        with self._lock:
            self._count += 1
        if self._exc is not None:
            raise self._exc
        return httpx.Response(self._status, json=self._body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handler)

    @property
    def calls(self) -> int:
        with self._lock:
            return self._count


def make_docx(path: Path, text: str) -> Path:
    doc = DocxDocument()
    doc.add_paragraph(text)
    doc.save(path)
    return path


def make_empty_pdf(path: Path) -> Path:
    writer = PdfWriter()
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def make_corrupted_xlsx(path: Path) -> Path:
    path.write_bytes(b"\x00\x01not-a-real-zip-container\x02\x03" * 8)
    return path


LONG_BODY = (
    "Synthetic deliverable body text used only for these tests, repeated "
    "to comfortably clear the 200-character empty-extraction guard so the "
    "text-channel items below are actually routed through the judge "
    "instead of being short-circuited to NO_EVIDENCE for lack of material."
)


def store_at(tmp_path: Path) -> VerdictStore:
    return VerdictStore(tmp_path / "runs")


def seed_record(store: VerdictStore, **overrides) -> None:
    fields = dict(
        product="synthetic-prod",
        task_id="task-seed",
        rubric_item_id="item-seed",
        attempt=1,
        exam_version="exam_v1",
        channel="text",
        state=ItemState.CONDITION_MET,
        reason=None,
        raw="synthetic previously-judged raw output",
        cost=0.05,
        served_model=DEFAULT_MODEL,
        served_provider=DEFAULT_PROVIDER,
        judged_at="2026-01-01T00:00:00+00:00",
    )
    fields.update(overrides)
    store.write(VerdictRecord(**fields))


# ---------------------------------------------------------------------------
# Resume: skip already-resolved items, isolate their stored cost
# ---------------------------------------------------------------------------


def test_grade_resume_skips_resolved_text_item_no_http_and_cost_isolated(tmp_path):
    """An already-resolved text item is never re-dispatched to the judge
    (no HTTP call for it), and its previously stored cost does not leak
    into this run's GradeResult.cost.
    """
    store = store_at(tmp_path)
    seed_record(
        store,
        product="prod-resume-1",
        task_id="task-resume-1",
        rubric_item_id="item-a",
        channel="text",
        state=ItemState.CONDITION_MET,
        cost=0.05,
    )

    criterion_b = "SYN-CRIT-RESUME-B: the report cites at least one source."
    manifest_ = manifest(
        "task-resume-1", [item("item-a", 10, "text"), item("item-b", 20, "text")]
    )
    deliverable = make_docx(tmp_path / "resume.docx", LONG_BODY)

    recorder = CriterionRoutingTransport()
    recorder.route(criterion_b, 200, or_response("Reasoning.\nMET", cost=0.02))

    result = grade_deliverable(
        manifest_,
        1,
        {"item-a": "SYN-CRIT-RESUME-A: unused, already resolved.", "item-b": criterion_b},
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-resume-1",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={},
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    assert recorder.calls == 1
    assert result.cost == pytest.approx(0.02)
    assert result.resolved == 2
    assert result.incomplete == 0

    loaded = store.load("prod-resume-1", 1, "exam_v1")
    assert len(loaded) == 2
    by_id = {r.rubric_item_id: r for r in loaded}
    assert by_id["item-a"].cost == pytest.approx(0.05)
    assert by_id["item-a"].state == ItemState.CONDITION_MET

    expected = score_task(
        [JudgedItem(10, ItemState.CONDITION_MET), JudgedItem(20, ItemState.CONDITION_MET)]
    )
    assert result.task_score == expected


def test_grade_resume_skips_resolved_non_text_item_without_reprocessing(tmp_path):
    """An already-resolved deterministic item is skipped even though it
    has no checker_params entry -- which would otherwise mark it
    incomplete("checker_unconfigured") if it were (incorrectly)
    reprocessed.
    """
    store = store_at(tmp_path)
    seed_record(
        store,
        product="prod-resume-2",
        task_id="task-resume-2",
        rubric_item_id="item-c",
        channel="deterministic",
        state=ItemState.CONDITION_NOT_MET,
        reason=None,
        cost=0.0,
    )

    manifest_ = manifest("task-resume-2", [item("item-c", 10, "deterministic")])
    deliverable = make_docx(tmp_path / "resume2.docx", LONG_BODY)
    recorder = CriterionRoutingTransport()

    result = grade_deliverable(
        manifest_,
        1,
        {},
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-resume-2",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={},  # deliberately unconfigured -- must not matter
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    assert recorder.calls == 0
    assert result.incomplete == 0
    assert result.resolved == 1

    loaded = store.load("prod-resume-2", 1, "exam_v1")
    assert len(loaded) == 1
    assert loaded[0].state == ItemState.CONDITION_NOT_MET

    expected = score_task([JudgedItem(10, ItemState.CONDITION_NOT_MET)])
    assert result.task_score == expected


# ---------------------------------------------------------------------------
# extract_document failure
# ---------------------------------------------------------------------------


def test_grade_extract_error_marks_all_unresolved_incomplete(tmp_path):
    """A corrupted deliverable that fails extract_document marks every
    unresolved item incomplete(reason="extract_error"), sends zero HTTP
    requests, and yields task_score=None and harness_failure_ratio == 1.
    """
    store = store_at(tmp_path)
    manifest_ = manifest(
        "task-extracterr",
        [
            item("item-1", 10, "text"),
            item("item-2", 20, "deterministic"),
            item("item-3", 5, "vision"),
        ],
    )
    corrupted = make_corrupted_xlsx(tmp_path / "broken.xlsx")
    recorder = CriterionRoutingTransport()

    result = grade_deliverable(
        manifest_,
        1,
        {"item-1": "SYN-CRIT: unreachable, extraction fails first."},
        "SYN-TASK-PROMPT",
        corrupted,
        product="prod-extracterr",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={"item-2": {"check": "format_is", "params": {"fmt": "xlsx"}}},
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    assert recorder.calls == 0
    assert result.task_score is None
    assert result.incomplete == 3
    assert result.resolved == 0
    assert result.harness_failure_ratio == Fraction(3, 3)

    for rec in store.load("prod-extracterr", 1, "exam_v1"):
        assert rec.state is None
        assert rec.reason == "extract_error"


# ---------------------------------------------------------------------------
# Empty-text guard
# ---------------------------------------------------------------------------


def test_grade_empty_extraction_guard_text_no_evidence_other_channels_normal(tmp_path):
    """facts.text_chars < 200 marks unresolved text items NO_EVIDENCE
    (reason="empty_extraction") without an HTTP call; deterministic and
    vision items are processed normally in the same run.
    """
    store = store_at(tmp_path)
    manifest_ = manifest(
        "task-emptyguard",
        [
            item("item-text", 10, "text"),
            item("item-det", 20, "deterministic"),
            item("item-vision", 15, "vision"),
        ],
    )
    empty_pdf = make_empty_pdf(tmp_path / "empty.pdf")
    recorder = CriterionRoutingTransport()

    result = grade_deliverable(
        manifest_,
        1,
        {"item-text": "SYN-CRIT: unreachable, empty-text guard fires first."},
        "SYN-TASK-PROMPT",
        empty_pdf,
        product="prod-emptyguard",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={"item-det": {"check": "format_is", "params": {"fmt": "pdf"}}},
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    assert recorder.calls == 0
    assert result.empty_extraction is True
    assert result.incomplete == 0
    assert result.resolved == 3
    assert result.no_evidence == 2  # empty-text item + deferred vision item
    assert result.harness_failure_ratio == Fraction(1, 3)  # only empty_extraction counts

    by_id = {r.rubric_item_id: r for r in store.load("prod-emptyguard", 1, "exam_v1")}
    assert by_id["item-text"].state == ItemState.NO_EVIDENCE
    assert by_id["item-text"].reason == "empty_extraction"
    assert by_id["item-det"].state == ItemState.CONDITION_MET
    assert by_id["item-vision"].state == ItemState.NO_EVIDENCE
    assert by_id["item-vision"].reason == "vision_channel_deferred"


def test_grade_empty_extraction_guard_boundary_199_chars_triggers(tmp_path):
    """A document with exactly 199 extracted characters is under the
    200-character threshold: the guard fires.
    """
    store = store_at(tmp_path)
    manifest_ = manifest("task-boundary-199", [item("item-1", 10, "text")])
    deliverable = make_docx(tmp_path / "short199.docx", "x" * 199)
    recorder = CriterionRoutingTransport()

    result = grade_deliverable(
        manifest_,
        1,
        {"item-1": "SYN-CRIT: unreachable."},
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-boundary-199",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={},
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    assert recorder.calls == 0
    assert result.empty_extraction is True
    record = store.load("prod-boundary-199", 1, "exam_v1")[0]
    assert record.state == ItemState.NO_EVIDENCE
    assert record.reason == "empty_extraction"


def test_grade_empty_extraction_guard_boundary_200_chars_does_not_trigger(tmp_path):
    """A document with exactly 200 extracted characters is at the
    threshold, not under it: the guard does not fire and the text item is
    dispatched to the judge normally.
    """
    store = store_at(tmp_path)
    criterion = "SYN-CRIT-BOUNDARY-200: exact-threshold document."
    manifest_ = manifest("task-boundary-200", [item("item-1", 10, "text")])
    deliverable = make_docx(tmp_path / "exact200.docx", "x" * 200)
    recorder = CriterionRoutingTransport()
    recorder.route(criterion, 200, or_response("Reasoning.\nMET"))

    result = grade_deliverable(
        manifest_,
        1,
        {"item-1": criterion},
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-boundary-200",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={},
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    assert recorder.calls == 1
    assert result.empty_extraction is False
    record = store.load("prod-boundary-200", 1, "exam_v1")[0]
    assert record.state == ItemState.CONDITION_MET


# ---------------------------------------------------------------------------
# Channel routing: vision
# ---------------------------------------------------------------------------


def test_grade_vision_channel_always_no_evidence_deferred_no_http(tmp_path):
    """Every vision-channel item is recorded NO_EVIDENCE(reason=
    "vision_channel_deferred") without ever calling the transport.
    """
    store = store_at(tmp_path)
    manifest_ = manifest(
        "task-vision", [item("item-v1", 10, "vision"), item("item-v2", 5, "vision")]
    )
    deliverable = make_docx(tmp_path / "vision.docx", LONG_BODY)
    recorder = CriterionRoutingTransport()

    result = grade_deliverable(
        manifest_,
        1,
        {},
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-vision",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={},
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    assert recorder.calls == 0
    assert result.no_evidence == 2
    assert result.incomplete == 0
    for rec in store.load("prod-vision", 1, "exam_v1"):
        assert rec.channel == "vision"
        assert rec.state == ItemState.NO_EVIDENCE
        assert rec.reason == "vision_channel_deferred"


# ---------------------------------------------------------------------------
# Channel routing: deterministic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("configured_fmt", "expected_state"),
    [
        ("docx", ItemState.CONDITION_MET),
        ("pdf", ItemState.CONDITION_NOT_MET),
    ],
)
def test_grade_deterministic_configured_uses_run_check_state(
    tmp_path, configured_fmt, expected_state
):
    """A deterministic item with a checker_params entry is resolved via
    checkers.run_check's returned ItemState, verbatim -- both the matching
    and non-matching cases.
    """
    store = store_at(tmp_path)
    manifest_ = manifest("task-det-configured", [item("item-d", 10, "deterministic")])
    deliverable = make_docx(tmp_path / "det.docx", LONG_BODY)
    recorder = CriterionRoutingTransport()

    result = grade_deliverable(
        manifest_,
        1,
        {},
        "SYN-TASK-PROMPT",
        deliverable,
        product=f"prod-det-{configured_fmt}",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={"item-d": {"check": "format_is", "params": {"fmt": configured_fmt}}},
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    assert recorder.calls == 0
    record = store.load(f"prod-det-{configured_fmt}", 1, "exam_v1")[0]
    assert record.channel == "deterministic"
    assert record.state == expected_state
    assert result.incomplete == 0


def test_grade_deterministic_missing_checker_params_is_incomplete(tmp_path):
    """A deterministic item absent from checker_params is incomplete with
    reason="checker_unconfigured" -- never silently skipped or scored.
    """
    store = store_at(tmp_path)
    manifest_ = manifest("task-det-unconfigured", [item("item-d", 10, "deterministic")])
    deliverable = make_docx(tmp_path / "det2.docx", LONG_BODY)
    recorder = CriterionRoutingTransport()

    result = grade_deliverable(
        manifest_,
        1,
        {},
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-det-unconfigured",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={},
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    assert recorder.calls == 0
    assert result.incomplete == 1
    assert result.task_score is None
    record = store.load("prod-det-unconfigured", 1, "exam_v1")[0]
    assert record.state is None
    assert record.reason == "checker_unconfigured"


def test_grade_deterministic_checker_inapplicable_returns_incomplete(tmp_path):
    """checkers.run_check returning None (the checker doesn't apply to
    this document's format) is incomplete(reason="checker_inapplicable"),
    distinct from an unconfigured item.
    """
    store = store_at(tmp_path)
    manifest_ = manifest("task-det-inapplicable", [item("item-d", 10, "deterministic")])
    # has_formula only applies to xlsx; this deliverable is docx -> None.
    deliverable = make_docx(tmp_path / "det3.docx", LONG_BODY)
    recorder = CriterionRoutingTransport()

    result = grade_deliverable(
        manifest_,
        1,
        {},
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-det-inapplicable",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={"item-d": {"check": "has_formula", "params": {"min_count": 1}}},
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    assert recorder.calls == 0
    assert result.incomplete == 1
    record = store.load("prod-det-inapplicable", 1, "exam_v1")[0]
    assert record.state is None
    assert record.reason == "checker_inapplicable"


# ---------------------------------------------------------------------------
# call_budget exhaustion
# ---------------------------------------------------------------------------


def test_grade_call_budget_exhausted_marks_remaining_text_items_incomplete(tmp_path):
    """Once the call budget is used up, the item that fails to acquire it
    and every remaining text item become incomplete(reason=
    "budget_exhausted") without any further HTTP call.
    """
    store = store_at(tmp_path)
    items = [item(f"item-{i}", 10, "text") for i in range(4)]
    manifest_ = manifest("task-budget", items)
    deliverable = make_docx(tmp_path / "budget.docx", LONG_BODY)
    criteria = {f"item-{i}": f"SYN-CRIT-BUDGET-{i}: distinct criterion text." for i in range(4)}

    always_met = FixedTransport(status=200, body=or_response("Reasoning.\nMET", cost=0.01))
    budget = CallBudget(limit=2)

    result = grade_deliverable(
        manifest_,
        1,
        criteria,
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-budget",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={},
        store=store,
        transport=always_met.transport,
        call_budget=budget,
        concurrency=1,
    )

    assert always_met.calls == 2
    assert result.incomplete == 2
    assert result.resolved == 2
    assert result.task_score is None
    assert result.harness_failure_ratio == Fraction(2, 4)

    records = store.load("prod-budget", 1, "exam_v1")
    assert len(records) == 4
    budget_exhausted = [r for r in records if r.reason == "budget_exhausted"]
    resolved = [r for r in records if r.state == ItemState.CONDITION_MET]
    assert len(budget_exhausted) == 2
    assert all(r.state is None for r in budget_exhausted)
    assert len(resolved) == 2


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def test_grade_circuit_breaker_trips_after_ten_consecutive_text_failures(tmp_path):
    """10 consecutive failed text judgements abort the whole run with
    GradeAbortError; items already judged before the trip remain
    persisted in the store.
    """
    store = store_at(tmp_path)
    items = [item(f"item-{i}", 10, "text") for i in range(10)]
    manifest_ = manifest("task-breaker-trip", items)
    deliverable = make_docx(tmp_path / "breaker.docx", LONG_BODY)
    criteria = {f"item-{i}": f"SYN-CRIT-BREAKER-{i}: distinct criterion text." for i in range(10)}

    always_bad_request = FixedTransport(status=400, body={"error": "bad_request"})

    with pytest.raises(GradeAbortError):
        grade_deliverable(
            manifest_,
            1,
            criteria,
            "SYN-TASK-PROMPT",
            deliverable,
            product="prod-breaker-trip",
            attempt=1,
            api_key="sk-hidden-test",
            checker_params={},
            store=store,
            transport=always_bad_request.transport,
            concurrency=1,
        )

    assert always_bad_request.calls == 10
    records = store.load("prod-breaker-trip", 1, "exam_v1")
    assert len(records) == 10
    assert all(r.state is None for r in records)


def test_grade_circuit_breaker_not_tripped_at_nine_consecutive_failures(tmp_path):
    """9 consecutive failed text judgements (one short of the breaker
    threshold) complete the run normally -- no GradeAbortError, and every
    item is recorded incomplete.
    """
    store = store_at(tmp_path)
    items = [item(f"item-{i}", 10, "text") for i in range(9)]
    manifest_ = manifest("task-breaker-safe", items)
    deliverable = make_docx(tmp_path / "breaker2.docx", LONG_BODY)
    criteria = {f"item-{i}": f"SYN-CRIT-BREAKERSAFE-{i}: distinct text." for i in range(9)}

    always_bad_request = FixedTransport(status=400, body={"error": "bad_request"})

    result = grade_deliverable(
        manifest_,
        1,
        criteria,
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-breaker-safe",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={},
        store=store,
        transport=always_bad_request.transport,
        concurrency=1,
    )

    assert always_bad_request.calls == 9
    assert result.incomplete == 9
    assert result.task_score is None


# ---------------------------------------------------------------------------
# judge_item exceptions converge to incomplete, never propagate
# ---------------------------------------------------------------------------


def test_grade_judge_item_exception_converges_to_incomplete_not_propagated(tmp_path):
    """A transport that raises a plain (non-httpx) exception makes
    judge_item itself raise; grade_deliverable must catch that at the
    item boundary and record incomplete(reason="judge_exception") instead
    of letting the exception escape.
    """
    store = store_at(tmp_path)
    manifest_ = manifest("task-judge-exc", [item("item-1", 10, "text")])
    deliverable = make_docx(tmp_path / "judgeexc.docx", LONG_BODY)
    raising_transport = FixedTransport(exc=RuntimeError("synthetic non-http failure"))

    result = grade_deliverable(
        manifest_,
        1,
        {"item-1": "SYN-CRIT-JUDGEEXC: distinct criterion text."},
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-judge-exc",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={},
        store=store,
        transport=raising_transport.transport,
        concurrency=1,
    )

    assert result.incomplete == 1
    assert result.task_score is None
    record = store.load("prod-judge-exc", 1, "exam_v1")[0]
    assert record.state is None
    assert record.reason == "judge_exception"


# ---------------------------------------------------------------------------
# Mixed-channel task scoring and harness_failure_ratio precision
# ---------------------------------------------------------------------------


def test_grade_mixed_channels_task_score_exact_fraction_matches_score_task(tmp_path):
    """A task spanning all three channels, with a capped positive item and
    a deduction, produces exactly the Fraction that scoring.score_task
    computes for the same final states in manifest item order.
    """
    store = store_at(tmp_path)
    items = [
        item("item-det", 20, "deterministic"),
        item("item-text-pos", 30, "text"),
        item("item-text-neg", -10, "text"),
        item("item-vision", 15, "vision"),
        item("item-text-notmet", 25, "text"),
    ]
    manifest_ = manifest("task-mixed", items)
    deliverable = make_docx(tmp_path / "mixed.docx", LONG_BODY)

    crit_pos = "SYN-CRIT-MIXED-POS: the plan includes a timeline."
    crit_neg = "SYN-CRIT-MIXED-NEG: the plan contains an unresolved placeholder."
    crit_notmet = "SYN-CRIT-MIXED-NOTMET: the plan cites external benchmarks."
    criteria = {
        "item-text-pos": crit_pos,
        "item-text-neg": crit_neg,
        "item-text-notmet": crit_notmet,
    }

    recorder = CriterionRoutingTransport()
    recorder.route(crit_pos, 200, or_response("Reasoning.\nMET", cost=0.01))
    recorder.route(crit_neg, 200, or_response("Reasoning.\nMET", cost=0.02))
    recorder.route(crit_notmet, 200, or_response("Reasoning.\nNOT_MET", cost=0.03))

    result = grade_deliverable(
        manifest_,
        1,
        criteria,
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-mixed",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={"item-det": {"check": "format_is", "params": {"fmt": "docx"}}},
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    assert result.incomplete == 0
    assert recorder.calls == 3
    assert result.cost == pytest.approx(0.06)

    expected = score_task(
        [
            JudgedItem(20, ItemState.CONDITION_MET),
            JudgedItem(30, ItemState.CONDITION_MET),
            JudgedItem(-10, ItemState.CONDITION_MET),
            JudgedItem(15, ItemState.NO_EVIDENCE),
            JudgedItem(25, ItemState.CONDITION_NOT_MET),
        ]
    )
    assert result.task_score == expected
    assert result.task_score.score_5 > Fraction(0)


def test_grade_harness_failure_ratio_counts_incomplete_not_vision_deferred(tmp_path):
    """harness_failure_ratio's numerator includes every incomplete item
    regardless of reason, but NO_EVIDENCE items only count when their
    reason is "over_context" or "empty_extraction" -- a vision-deferred
    NO_EVIDENCE item must not inflate the ratio.
    """
    store = store_at(tmp_path)
    items = [
        item("item-unparseable", 10, "text"),
        item("item-vision", 10, "vision"),
        item("item-det", 10, "deterministic"),
        item("item-ok", 10, "text"),
    ]
    manifest_ = manifest("task-ratio", items)
    deliverable = make_docx(tmp_path / "ratio.docx", LONG_BODY)

    crit_unparseable = "SYN-CRIT-RATIO-AMBIG: distinct ambiguous criterion."
    crit_ok = "SYN-CRIT-RATIO-OK: distinct resolvable criterion."
    ambiguous_content = "Final answer: MET or NOT_MET"

    recorder = CriterionRoutingTransport()
    recorder.route(crit_unparseable, 200, or_response(ambiguous_content))
    recorder.route(crit_ok, 200, or_response("Reasoning.\nMET"))

    result = grade_deliverable(
        manifest_,
        1,
        {"item-unparseable": crit_unparseable, "item-ok": crit_ok},
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-ratio",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={"item-det": {"check": "format_is", "params": {"fmt": "docx"}}},
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    assert result.incomplete == 1
    assert result.resolved == 3
    assert result.no_evidence == 1
    assert result.harness_failure_ratio == Fraction(1, 4)
    assert result.task_score is None

    by_id = {r.rubric_item_id: r for r in store.load("prod-ratio", 1, "exam_v1")}
    assert by_id["item-unparseable"].state is None
    assert by_id["item-unparseable"].reason == "judge_unparseable"
    assert by_id["item-vision"].state == ItemState.NO_EVIDENCE
    assert by_id["item-vision"].reason == "vision_channel_deferred"


# ---------------------------------------------------------------------------
# Verdict record field completeness across channels
# ---------------------------------------------------------------------------


def test_grade_verdict_record_fields_complete_across_channels(tmp_path):
    """store.load's records carry channel/state/reason/raw correctly for
    each channel, and served_model/served_provider/cost are populated for
    the text channel from the judge response.
    """
    store = store_at(tmp_path)
    items = [
        item("item-det", 10, "deterministic"),
        item("item-text", 10, "text"),
        item("item-vision", 10, "vision"),
    ]
    manifest_ = manifest("task-fields", items)
    deliverable = make_docx(tmp_path / "fields.docx", LONG_BODY)
    criterion = "SYN-CRIT-FIELDS: distinct criterion text."
    content = "Careful reasoning about the material.\nMET"

    recorder = CriterionRoutingTransport()
    recorder.route(criterion, 200, or_response(content, cost=0.0321))

    grade_deliverable(
        manifest_,
        1,
        {"item-text": criterion},
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-fields",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={"item-det": {"check": "format_is", "params": {"fmt": "docx"}}},
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    by_id = {r.rubric_item_id: r for r in store.load("prod-fields", 1, "exam_v1")}

    det = by_id["item-det"]
    assert det.channel == "deterministic"
    assert det.state == ItemState.CONDITION_MET
    assert det.reason is None
    datetime.fromisoformat(det.judged_at)

    text = by_id["item-text"]
    assert text.channel == "text"
    assert text.state == ItemState.CONDITION_MET
    assert text.reason is None
    assert text.raw == content
    assert text.cost == pytest.approx(0.0321)
    assert text.served_model == DEFAULT_MODEL
    assert text.served_provider == DEFAULT_PROVIDER
    datetime.fromisoformat(text.judged_at)

    vision = by_id["item-vision"]
    assert vision.channel == "vision"
    assert vision.state == ItemState.NO_EVIDENCE
    assert vision.reason == "vision_channel_deferred"
    datetime.fromisoformat(vision.judged_at)


# ---------------------------------------------------------------------------
# task_ordinal selection and per-task_id summary isolation
# ---------------------------------------------------------------------------


def test_grade_task_ordinal_selects_correct_task_and_summary_isolated_by_task_id(tmp_path):
    """Grading task_ordinal=2 reads the second task in
    manifest["content"]["tasks"], and the resolved/incomplete summary
    reflects only that task's items -- pre-existing records for a
    different task_id sharing the same (product, attempt, exam_version)
    verdict file are not counted and are not touched.
    """
    store = store_at(tmp_path)
    seed_record(
        store,
        product="prod-multitask",
        task_id="task-alpha",
        rubric_item_id="item-shadow",
        channel="text",
        state=ItemState.CONDITION_MET,
        cost=0.0,
    )

    manifest_ = manifest_with_tasks(
        [
            {"task_id": "task-alpha", "items": [item("item-shadow", 10, "text")]},
            {
                "task_id": "task-beta",
                "items": [item("item-1", 10, "text"), item("item-2", 20, "deterministic")],
            },
        ]
    )
    deliverable = make_docx(tmp_path / "multitask.docx", LONG_BODY)
    criterion = "SYN-CRIT-MULTITASK: distinct criterion text."

    recorder = CriterionRoutingTransport()
    recorder.route(criterion, 200, or_response("Reasoning.\nMET"))

    result = grade_deliverable(
        manifest_,
        2,
        {"item-1": criterion},
        "SYN-TASK-PROMPT",
        deliverable,
        product="prod-multitask",
        attempt=1,
        api_key="sk-hidden-test",
        checker_params={"item-2": {"check": "format_is", "params": {"fmt": "docx"}}},
        store=store,
        transport=recorder.transport,
        concurrency=1,
    )

    assert result.resolved == 2
    assert result.incomplete == 0

    expected = score_task(
        [JudgedItem(10, ItemState.CONDITION_MET), JudgedItem(20, ItemState.CONDITION_MET)]
    )
    assert result.task_score == expected

    all_records = store.load("prod-multitask", 1, "exam_v1")
    assert len(all_records) == 3
    shadow = next(r for r in all_records if r.rubric_item_id == "item-shadow")
    assert shadow.task_id == "task-alpha"
    assert shadow.state == ItemState.CONDITION_MET
