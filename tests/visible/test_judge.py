"""Visible sample tests for gdpval_eval.judge (unit U3: LLM judge client).

Guidance examples for the implementer. The authoritative, comprehensive
contract tests (retry paths, parsing edge cases, provider/model validation,
negative-polarity semantics, ...) live in tests/hidden/test_judge.py.

All prompts, criteria, and document text below are synthetic fixtures
invented for these tests -- none of it is real GDPval rubric or task
content.
"""

from __future__ import annotations

import json

import httpx
import pytest

from gdpval_eval.judge import JudgeConfig, judge_item, load_judge_config
from gdpval_eval.models import ItemState

CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


def make_cfg(**overrides) -> JudgeConfig:
    defaults = dict(
        model="deepseek/deepseek-v4-pro-0813",
        provider_order=("DeepSeek",),
        allow_fallbacks=False,
        temperature=0,
    )
    defaults.update(overrides)
    return JudgeConfig(**defaults)


def openrouter_success_body(content: str, *, finish_reason: str = "stop") -> dict:
    return {
        "id": "gen-synthetic-visible",
        "model": "deepseek/deepseek-v4-pro-0813",
        "provider": "DeepSeek",
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"include": True, "cost": 0.001},
    }


class RecordingTransport:
    """Replays a fixed sequence of (status, json_body) responses and
    records every request that was sent through it.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def _handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        idx = min(len(self.requests) - 1, len(self._responses) - 1)
        status, payload = self._responses[idx]
        return httpx.Response(status, json=payload)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handler)

    def body(self, index: int) -> dict:
        return json.loads(self.requests[index].content)


def test_judge_load_judge_config_happy_path():
    """load_judge_config extracts model/provider_order/allow_fallbacks/
    temperature from meta['judge'], turning provider_order into a tuple.
    """
    meta = {
        "judge": {
            "platform": "openrouter",
            "model": "deepseek/deepseek-v4-pro-0813",
            "provider_order": ["DeepSeek"],
            "allow_fallbacks": False,
            "temperature": 0,
        },
        "exam_version": "exam_v1",
    }

    cfg = load_judge_config(meta)

    assert cfg.model == "deepseek/deepseek-v4-pro-0813"
    assert cfg.provider_order == ("DeepSeek",)
    assert isinstance(cfg.provider_order, tuple)
    assert cfg.allow_fallbacks is False
    assert cfg.temperature == 0


@pytest.mark.parametrize(
    "tail_line, expected_state",
    [
        ("MET", ItemState.CONDITION_MET),
        ("NOT_MET", ItemState.CONDITION_NOT_MET),
    ],
)
def test_judge_item_whitelist_two_states(tail_line, expected_state):
    """A response whose last non-empty line is exactly MET or NOT_MET is
    parsed into the corresponding ItemState, hitting the OpenRouter chat
    completions endpoint exactly once.
    """
    cfg = make_cfg()
    recorder = RecordingTransport(
        [(200, openrouter_success_body(f"Reasoning about the material.\n{tail_line}"))]
    )

    outcome = judge_item(
        cfg,
        "sk-visible-test",
        "The report includes an executive summary.",
        "Draft a quarterly business report.",
        "Synthetic deliverable body text.",
        transport=recorder.transport,
    )

    assert len(recorder.requests) == 1
    assert recorder.requests[0].url == httpx.URL(CHAT_COMPLETIONS_URL)
    assert outcome.state == expected_state
    assert outcome.incomplete_reason is None


def test_judge_item_over_context_sends_no_request():
    """When doc_text exceeds max_doc_chars, judge_item must not send any
    HTTP request at all -- it short-circuits straight to NO_EVIDENCE.
    """
    cfg = make_cfg()
    recorder = RecordingTransport([(200, openrouter_success_body("irrelevant\nMET"))])

    outcome = judge_item(
        cfg,
        "sk-visible-test",
        "criterion text",
        "task prompt",
        "x" * 21,
        transport=recorder.transport,
        max_doc_chars=20,
    )

    assert recorder.requests == []
    assert outcome.state == ItemState.NO_EVIDENCE
    assert outcome.no_evidence_reason == "over_context"
