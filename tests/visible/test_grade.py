"""Visible sample tests for gdpval_eval.grade.grade_deliverable (unit U5).

These are guidance examples for the implementer; the authoritative,
comprehensive contract tests live in tests/hidden/test_grade.py. All
manifests, task_ids, rubric_item_ids, and criterion strings below are
invented synthetic fixtures -- nothing here is drawn from the real GDPval
dataset, per repo secrecy rules (see AGENTS.md).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
from docx import Document as DocxDocument

from gdpval_eval.grade import grade_deliverable
from gdpval_eval.models import ItemState, JudgedItem
from gdpval_eval.scoring import score_task
from gdpval_eval.verdicts import VerdictStore

DEFAULT_MODEL = "deepseek/deepseek-v4-pro-0813"
DEFAULT_PROVIDER = "DeepSeek"


def _judge_meta() -> dict:
    return {
        "model": DEFAULT_MODEL,
        "provider_order": [DEFAULT_PROVIDER],
        "allow_fallbacks": False,
        "temperature": 0,
    }


def _item(rubric_item_id: str, score: int, channel: str) -> dict:
    return {
        "rubric_item_id": rubric_item_id,
        "content_hash": f"synthetic-hash-{rubric_item_id}",
        "score": score,
        "channel": channel,
        "tags": [],
    }


def _manifest(task_id: str, items: list[dict], exam_version: str = "exam_v1") -> dict:
    return {
        "content": {"exam_version": exam_version, "tasks": [{"task_id": task_id, "items": items}]},
        "meta": {"judge": _judge_meta()},
    }


def _or_response(content: str, *, cost: float = 0.01) -> dict:
    return {
        "id": "gen-visible-synthetic",
        "model": DEFAULT_MODEL,
        "provider": DEFAULT_PROVIDER,
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"include": True, "cost": cost},
    }


class CriterionRoutingTransport:
    """Returns a canned response chosen by which registered criterion
    substring is present in the outgoing prompt text -- robust to whatever
    order grade_deliverable's internal concurrency issues requests in.
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
        return len(self.requests)


def _make_docx(path: Path, text: str) -> Path:
    doc = DocxDocument()
    doc.add_paragraph(text)
    doc.save(path)
    return path


def test_grade_all_text_happy_path_matches_score_task(tmp_path):
    """Two text-channel items resolved in one pass produce the same
    TaskScore that calling scoring.score_task on their final states
    directly would -- and each triggers exactly one HTTP call.
    """
    criterion_met = "SYN-CRIT-A: the memo names a primary stakeholder."
    criterion_not_met = "SYN-CRIT-B: the memo includes a budget table."
    item_met = _item("item-a", 30, "text")
    item_not_met = _item("item-b", 20, "text")
    manifest = _manifest("task-visible-1", [item_met, item_not_met])
    deliverable = _make_docx(
        tmp_path / "memo.docx",
        "A synthetic memo body long enough to clear the 200-character empty "
        "text guard so both rubric items below are routed through the text "
        "judge channel instead of being marked NO_EVIDENCE for lack of "
        "material to judge against in the first place.",
    )

    recorder = CriterionRoutingTransport()
    recorder.route(criterion_met, 200, _or_response("Reasoning line.\nMET"))
    recorder.route(criterion_not_met, 200, _or_response("Reasoning line.\nNOT_MET"))

    store = VerdictStore(tmp_path / "runs")
    result = grade_deliverable(
        manifest,
        1,
        {"item-a": criterion_met, "item-b": criterion_not_met},
        "SYN-TASK-PROMPT: draft a one-page project memo.",
        deliverable,
        product="visible-prod-1",
        attempt=1,
        api_key="sk-visible-test",
        checker_params={},
        store=store,
        transport=recorder.transport,
    )

    assert recorder.calls == 2
    assert result.incomplete == 0
    assert result.resolved == 2
    assert result.no_evidence == 0
    assert result.empty_extraction is False

    expected = score_task(
        [JudgedItem(30, ItemState.CONDITION_MET), JudgedItem(20, ItemState.CONDITION_NOT_MET)]
    )
    assert result.task_score == expected


def test_grade_extract_error_all_incomplete_no_http(tmp_path):
    """A deliverable that fails to parse marks every item incomplete with
    reason "extract_error", sends no HTTP request at all, and yields
    task_score=None.
    """
    manifest = _manifest(
        "task-visible-2",
        [_item("item-x", 10, "text"), _item("item-y", 15, "deterministic")],
    )
    corrupted = tmp_path / "broken.xlsx"
    corrupted.write_bytes(b"\x00\x01not-a-real-zip-container\x02\x03" * 4)

    recorder = CriterionRoutingTransport()
    store = VerdictStore(tmp_path / "runs")

    result = grade_deliverable(
        manifest,
        1,
        {"item-x": "SYN-CRIT: irrelevant, extraction fails before judging."},
        "SYN-TASK-PROMPT: draft a workbook.",
        corrupted,
        product="visible-prod-2",
        attempt=1,
        api_key="sk-visible-test",
        checker_params={},
        store=store,
        transport=recorder.transport,
    )

    assert recorder.calls == 0
    assert result.task_score is None
    assert result.incomplete == 2

    records = {r.rubric_item_id: r for r in store.load("visible-prod-2", 1, "exam_v1")}
    assert records["item-x"].state is None
    assert records["item-x"].reason == "extract_error"
    assert records["item-y"].state is None
    assert records["item-y"].reason == "extract_error"


def test_grade_deterministic_channel_uses_run_check_state(tmp_path):
    """A configured deterministic item is resolved via checkers.run_check
    against the parsed deliverable, without any HTTP call being made.
    """
    manifest = _manifest("task-visible-3", [_item("item-d", 25, "deterministic")])
    deliverable = _make_docx(
        tmp_path / "report.docx", "Synthetic body text for a deterministic-only task."
    )

    recorder = CriterionRoutingTransport()
    store = VerdictStore(tmp_path / "runs")

    result = grade_deliverable(
        manifest,
        1,
        {},
        "SYN-TASK-PROMPT: draft a one-page report.",
        deliverable,
        product="visible-prod-3",
        attempt=1,
        api_key="sk-visible-test",
        checker_params={"item-d": {"check": "format_is", "params": {"fmt": "docx"}}},
        store=store,
        transport=recorder.transport,
    )

    assert recorder.calls == 0
    assert result.incomplete == 0
    record = store.load("visible-prod-3", 1, "exam_v1")[0]
    assert record.channel == "deterministic"
    assert record.state == ItemState.CONDITION_MET
