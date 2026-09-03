"""Single-deliverable grading orchestration (spec U5). Blind-test unit.

Routes every manifest item down its frozen channel, persists each outcome
in the VerdictStore (skipping already-resolved keys before any external
call), and produces a TaskScore only when no item remains incomplete.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

from gdpval_eval import checkers, judge
from gdpval_eval.extract import ExtractedDoc, ExtractError, extract_document
from gdpval_eval.models import GdpvalEvalError, ItemState, JudgedItem, TaskScore
from gdpval_eval.scoring import score_task
from gdpval_eval.verdicts import VerdictRecord, VerdictStore

_EMPTY_EXTRACTION_CHARS = 200
_CIRCUIT_BREAKER_STREAK = 10
_HARNESS_NO_EVIDENCE_REASONS = ("over_context", "empty_extraction")


class GradeAbortError(GdpvalEvalError):
    """Raised when consecutive judge failures trip the circuit breaker."""


class CallBudget:
    """Bounded budget of judge_item invocations shared across one run."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self.used < self.limit:
                self.used += 1
                return True
            return False


@dataclass(frozen=True)
class GradeResult:
    task_score: TaskScore | None
    resolved: int
    incomplete: int
    no_evidence: int
    empty_extraction: bool
    harness_failure_ratio: Fraction
    cost: float


def grade_deliverable(
    manifest: dict,
    task_ordinal: int,
    criterion_by_item_id: dict[str, str],
    task_prompt: str,
    deliverable: Path,
    *,
    product: str,
    attempt: int,
    api_key: str,
    checker_params: dict[str, dict],
    store: VerdictStore,
    transport=None,
    call_budget: CallBudget | None = None,
    concurrency: int = 6,
) -> GradeResult:
    task = manifest["content"]["tasks"][task_ordinal - 1]
    exam_version = manifest["content"]["exam_version"]
    cfg = judge.load_judge_config(manifest["meta"])

    task_id = task["task_id"]
    items = task["items"]
    item_ids = {item["rubric_item_id"] for item in items}

    resolved = store.resolved_ids(product, attempt, exam_version) & item_ids
    unresolved_items = [item for item in items if item["rubric_item_id"] not in resolved]

    def _write(
        item: dict,
        *,
        state: ItemState | None,
        reason: str | None,
        raw: str = "",
        cost: float = 0.0,
        served_model: str | None = None,
        served_provider: str | None = None,
    ) -> None:
        store.write(
            VerdictRecord(
                product=product,
                task_id=task_id,
                rubric_item_id=item["rubric_item_id"],
                attempt=attempt,
                exam_version=exam_version,
                channel=item["channel"],
                state=state,
                reason=reason,
                raw=raw,
                cost=cost,
                served_model=served_model,
                served_provider=served_provider,
                judged_at=datetime.now(UTC).isoformat(),
            )
        )

    empty_extraction = False
    new_cost = 0.0

    if unresolved_items:
        try:
            doc = extract_document(deliverable)
        except ExtractError:
            for item in unresolved_items:
                _write(item, state=None, reason="extract_error")
        else:
            empty_extraction = doc.facts.text_chars < _EMPTY_EXTRACTION_CHARS
            text_items_to_judge: list[dict] = []
            for item in unresolved_items:
                channel = item["channel"]
                if channel == "vision":
                    _write(
                        item, state=ItemState.NO_EVIDENCE, reason="vision_channel_deferred"
                    )
                elif channel == "deterministic":
                    cfgi = checker_params.get(item["rubric_item_id"])
                    if cfgi is None:
                        _write(item, state=None, reason="checker_unconfigured")
                    else:
                        checked_state = checkers.run_check(cfgi["check"], cfgi["params"], doc)
                        if checked_state is None:
                            _write(item, state=None, reason="checker_inapplicable")
                        else:
                            _write(item, state=checked_state, reason=None)
                else:
                    if empty_extraction:
                        _write(item, state=ItemState.NO_EVIDENCE, reason="empty_extraction")
                    else:
                        text_items_to_judge.append(item)

            if text_items_to_judge:
                new_cost = _judge_text_channel(
                    text_items_to_judge,
                    cfg=cfg,
                    api_key=api_key,
                    criterion_by_item_id=criterion_by_item_id,
                    task_prompt=task_prompt,
                    doc=doc,
                    transport=transport,
                    call_budget=call_budget,
                    concurrency=concurrency,
                    write=_write,
                )

    records = [
        rec
        for rec in store.load(product, attempt, exam_version)
        if rec.task_id == task_id and rec.rubric_item_id in item_ids
    ]
    resolved_count = sum(1 for rec in records if rec.state is not None)
    incomplete_count = sum(1 for rec in records if rec.state is None)
    no_evidence_count = sum(1 for rec in records if rec.state == ItemState.NO_EVIDENCE)
    harness_no_evidence = sum(
        1
        for rec in records
        if rec.state == ItemState.NO_EVIDENCE and rec.reason in _HARNESS_NO_EVIDENCE_REASONS
    )
    harness_failure_ratio = (
        Fraction(incomplete_count + harness_no_evidence, len(items)) if items else Fraction(0)
    )

    task_score = None
    if incomplete_count == 0:
        state_by_id = {rec.rubric_item_id: rec.state for rec in records}
        judged_items = [
            JudgedItem(score=item["score"], state=state_by_id[item["rubric_item_id"]])
            for item in items
        ]
        task_score = score_task(judged_items)

    return GradeResult(
        task_score=task_score,
        resolved=resolved_count,
        incomplete=incomplete_count,
        no_evidence=no_evidence_count,
        empty_extraction=empty_extraction,
        harness_failure_ratio=harness_failure_ratio,
        cost=new_cost,
    )


def _judge_text_channel(
    items: list[dict],
    *,
    cfg: judge.JudgeConfig,
    api_key: str,
    criterion_by_item_id: dict[str, str],
    task_prompt: str,
    doc: ExtractedDoc,
    transport,
    call_budget: CallBudget | None,
    concurrency: int,
    write: Callable[..., None],
) -> float:
    """Judge text-channel items under the shared call budget and the
    consecutive-failure circuit breaker.

    HTTP calls run concurrently (bounded by `concurrency`); outcomes are
    harvested and written to the store strictly in item order so the
    budget and circuit-breaker semantics stay deterministic regardless of
    completion order. Returns the summed cost of outcomes produced here.
    """

    def _run(item: dict):
        rid = item["rubric_item_id"]
        try:
            return judge.judge_item(
                cfg,
                api_key,
                criterion_by_item_id[rid],
                task_prompt,
                doc.text,
                transport=transport,
            )
        except Exception:
            return None

    budget_exhausted_hit = False
    total_cost = 0.0
    consecutive_failures = 0
    window = max(1, concurrency)
    # index -> Future | None (None = budget-exhausted, never dispatched).
    futures: dict[int, object] = {}
    next_to_submit = 0

    def _submit_window(pool: ThreadPoolExecutor, harvested: int) -> None:
        """Keep at most `window` dispatched calls beyond the harvest point,
        so the circuit breaker bounds external calls even under
        concurrency: once it trips, nothing new is ever dispatched."""
        nonlocal next_to_submit, budget_exhausted_hit
        while next_to_submit < len(items) and next_to_submit - harvested < window:
            if budget_exhausted_hit or (
                call_budget is not None and not call_budget.try_acquire()
            ):
                budget_exhausted_hit = True
                futures[next_to_submit] = None
            else:
                futures[next_to_submit] = pool.submit(_run, items[next_to_submit])
            next_to_submit += 1

    with ThreadPoolExecutor(max_workers=window) as pool:
        for index, item in enumerate(items):
            _submit_window(pool, index)
            future = futures.pop(index)
            if future is None:
                # Deliberate throttling, not a judge failure: the breaker
                # streak is left untouched.
                write(item, state=None, reason="budget_exhausted")
                continue

            outcome = future.result()
            if outcome is None:
                write(item, state=None, reason="judge_exception")
                consecutive_failures += 1
            elif outcome.state is None:
                total_cost += outcome.cost
                write(
                    item,
                    state=None,
                    reason=outcome.incomplete_reason,
                    raw=outcome.raw,
                    cost=outcome.cost,
                    served_model=outcome.served_model,
                    served_provider=outcome.served_provider,
                )
                consecutive_failures += 1
            else:
                total_cost += outcome.cost
                reason = (
                    outcome.no_evidence_reason
                    if outcome.state == ItemState.NO_EVIDENCE
                    else None
                )
                write(
                    item,
                    state=outcome.state,
                    reason=reason,
                    raw=outcome.raw,
                    cost=outcome.cost,
                    served_model=outcome.served_model,
                    served_provider=outcome.served_provider,
                )
                consecutive_failures = 0

            if consecutive_failures >= _CIRCUIT_BREAKER_STREAK:
                for pending in futures.values():
                    if pending is not None:
                        pending.cancel()
                raise GradeAbortError(
                    "text channel judging aborted after "
                    f"{_CIRCUIT_BREAKER_STREAK} consecutive failures"
                )

    return total_cost
