"""Hidden contract tests for gdpval_eval.judge (unit U3: LLM judge client).

Blind-graded: not visible to the implementer. Nothing here is a real
GDPval rubric criterion, task prompt, or deliverable -- every string is an
invented synthetic fixture, per repo secrecy rules.

Contract under test (from the work-unit spec, .claude/plans/gdpval-grading-w2.md):

- load_judge_config(meta) reads meta["judge"] into a JudgeConfig(model,
  provider_order: tuple, allow_fallbacks, temperature); meta["judge"]
  missing or None -> ValueError.
- judge_item(cfg, api_key, criterion, task_prompt, doc_text, *,
  transport=None, max_doc_chars=400_000) -> JudgeOutcome:
  * len(doc_text) > max_doc_chars -> no HTTP request at all,
    state=NO_EVIDENCE, no_evidence_reason="over_context" (strictly
    greater-than: doc_text of length == max_doc_chars is not over_context).
  * POST https://openrouter.ai/api/v1/chat/completions with
    model==cfg.model, provider=={"order": [...], "allow_fallbacks": ...}
    matching cfg, temperature==cfg.temperature, usage=={"include": True},
    max_tokens=4096 on the first attempt, Authorization header carrying
    api_key.
  * Prompt: doc_text appears before criterion in the concatenated message
    text (prefix-cache friendly), and the tail of the prompt carries the
    MET/NOT_MET output whitelist instruction.
  * Parsing: take message.content's last non-empty line, strip markdown
    (**, `) and surrounding punctuation, whole-word match against MET /
    NOT_MET. Exactly one hit -> that state. Both, neither, empty content,
    or finish_reason=="length" -> one repair retry (max_tokens=32768,
    messages append a format-fix instruction). Still unresolved ->
    state=None, incomplete_reason="judge_unparseable".
  * Retry (<=3 attempts total) on HTTP 429/5xx/network errors. Other 4xx,
    missing "choices", or a non-JSON response body -> no retry,
    state=None, incomplete_reason non-empty.
  * Response validation: response "model" != cfg.model, or a top-level
    "provider" field present and not in cfg.provider_order ->
    state=None, incomplete_reason="provider_mismatch".
  * cost <- response usage.cost (missing -> 0.0); served_model /
    served_provider <- response's corresponding fields; raw <- content
    of the response actually used to resolve the verdict.
  * Negative-polarity semantics: MET always means "the described
    condition holds" -- for a criterion describing a violation, a MET
    verdict maps to CONDITION_MET (the deduction applies), never to
    CONDITION_NOT_MET.
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


def openrouter_response(
    content: str,
    *,
    model: str = "deepseek/deepseek-v4-pro-0813",
    provider: str | None = "DeepSeek",
    finish_reason: str = "stop",
    cost: float | None = 0.001,
    include_usage: bool = True,
) -> dict:
    body: dict = {
        "id": "gen-synthetic-hidden",
        "model": model,
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
    }
    if provider is not None:
        body["provider"] = provider
    if include_usage:
        usage: dict = {"include": True}
        if cost is not None:
            usage["cost"] = cost
        body["usage"] = usage
    return body


class RecordingTransport:
    """Replays a fixed sequence of responses and records every request
    that was sent through it.

    Each element of `responses` is either:
      - an (http_status, json_body_or_None) tuple (None body -> the
        response is not valid JSON, to exercise the non-JSON-response
        path), or
      - a BaseException instance, raised in place of a response (to
        simulate a network-level failure).

    If the handler is invoked more times than responses were supplied,
    the last response/exception is replayed again.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def _handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        idx = min(len(self.requests) - 1, len(self._responses) - 1)
        spec = self._responses[idx]
        if isinstance(spec, BaseException):
            raise spec
        status, payload = spec
        if payload is None:
            return httpx.Response(status, content=b"not-json-at-all{{{")
        return httpx.Response(status, json=payload)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handler)

    def body(self, index: int) -> dict:
        return json.loads(self.requests[index].content)


DEFAULT_CRITERION = "The report includes a methodology section."
DEFAULT_TASK_PROMPT = "Draft a market analysis memo."
DEFAULT_DOC_TEXT = "Synthetic deliverable body used only for these tests."


def call_judge(cfg, recorder, **kwargs):
    return judge_item(
        cfg,
        "sk-hidden-test-key",
        kwargs.pop("criterion", DEFAULT_CRITERION),
        kwargs.pop("task_prompt", DEFAULT_TASK_PROMPT),
        kwargs.pop("doc_text", DEFAULT_DOC_TEXT),
        transport=recorder.transport,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# load_judge_config
# ---------------------------------------------------------------------------


def test_judge_load_judge_config_happy_path():
    """meta["judge"] fields map 1:1 onto JudgeConfig, provider_order
    becoming a tuple.
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


def test_judge_load_judge_config_multi_provider_order_preserved():
    """provider_order keeps its given ordering and length; allow_fallbacks
    is read verbatim (not hardcoded).
    """
    meta = {
        "judge": {
            "platform": "openrouter",
            "model": "vendor/model-x",
            "provider_order": ["ProviderA", "ProviderB", "ProviderC"],
            "allow_fallbacks": True,
            "temperature": 0,
        },
    }

    cfg = load_judge_config(meta)

    assert cfg.provider_order == ("ProviderA", "ProviderB", "ProviderC")
    assert cfg.allow_fallbacks is True
    assert cfg.model == "vendor/model-x"


@pytest.mark.parametrize(
    "meta",
    [
        {},
        {"judge": None},
        {"other_key": {"model": "x"}},
    ],
)
def test_judge_load_judge_config_missing_or_none_judge_key_raises(meta):
    """meta["judge"] missing entirely, or explicitly None, is invalid."""
    with pytest.raises(ValueError):
        load_judge_config(meta)


def test_judge_config_to_item_end_to_end_uses_meta_derived_values():
    """End-to-end: a JudgeConfig built by load_judge_config drives the
    actual outgoing request fields in judge_item.
    """
    meta = {
        "judge": {
            "platform": "openrouter",
            "model": "deepseek/deepseek-v4-pro-0813",
            "provider_order": ["DeepSeek"],
            "allow_fallbacks": False,
            "temperature": 0,
        },
    }
    cfg = load_judge_config(meta)
    recorder = RecordingTransport([(200, openrouter_response("Analysis line.\nMET"))])

    outcome = call_judge(cfg, recorder)

    body = recorder.body(0)
    assert body["model"] == meta["judge"]["model"]
    assert body["provider"] == {"order": ["DeepSeek"], "allow_fallbacks": False}
    assert outcome.state == ItemState.CONDITION_MET


# ---------------------------------------------------------------------------
# Outgoing request shape
# ---------------------------------------------------------------------------


def test_judge_item_request_body_fields_exact():
    """The first request's body carries exactly the fields the spec
    mandates, and the endpoint/method/auth header are correct.
    """
    cfg = make_cfg(provider_order=("DeepSeek", "Fireworks"), allow_fallbacks=False, temperature=0)
    recorder = RecordingTransport([(200, openrouter_response("Reasoning...\nMET"))])

    call_judge(cfg, recorder)

    assert len(recorder.requests) == 1
    req = recorder.requests[0]
    assert req.method == "POST"
    assert req.url == httpx.URL(CHAT_COMPLETIONS_URL)
    assert "sk-hidden-test-key" in req.headers.get("authorization", "")

    body = recorder.body(0)
    assert body["model"] == "deepseek/deepseek-v4-pro-0813"
    assert body["provider"] == {"order": ["DeepSeek", "Fireworks"], "allow_fallbacks": False}
    assert body["temperature"] == 0
    assert body["usage"] == {"include": True}
    assert body["max_tokens"] == 4096


def test_judge_item_doc_precedes_criterion_and_whitelist_present():
    """doc_text appears before criterion in the concatenated prompt text
    (prefix-cache friendly), and both the MET and NOT_MET whitelist
    tokens are present for the model to choose between.
    """
    cfg = make_cfg()
    recorder = RecordingTransport([(200, openrouter_response("Verdict follows.\nNOT_MET"))])
    criterion = "SYN-CRITERION-9f2: the deliverable includes a title page."
    doc_text = "SYN-DOC-BODY-7ac: the quick brown fox jumps over the lazy dog."
    task_prompt = "SYN-TASK-3d1: draft a one-page business memo."

    call_judge(cfg, recorder, criterion=criterion, task_prompt=task_prompt, doc_text=doc_text)

    body = recorder.body(0)
    combined = "\n".join(m["content"] for m in body["messages"])
    assert doc_text in combined
    assert criterion in combined
    assert combined.index(doc_text) < combined.index(criterion)
    assert "MET" in combined
    assert "NOT_MET" in combined


# ---------------------------------------------------------------------------
# over_context short-circuit
# ---------------------------------------------------------------------------


def test_judge_item_over_context_sends_no_request():
    """doc_text longer than max_doc_chars must not trigger any HTTP call."""
    cfg = make_cfg()
    recorder = RecordingTransport([(200, openrouter_response("irrelevant\nMET"))])

    outcome = call_judge(cfg, recorder, doc_text="x" * 51, max_doc_chars=50)

    assert recorder.requests == []
    assert outcome.state == ItemState.NO_EVIDENCE
    assert outcome.no_evidence_reason == "over_context"
    assert outcome.incomplete_reason is None


def test_judge_item_over_context_boundary_exact_length_not_triggered():
    """doc_text exactly at max_doc_chars is not over_context: the check
    is strictly greater-than, not greater-or-equal.
    """
    cfg = make_cfg()
    recorder = RecordingTransport([(200, openrouter_response("Verdict.\nMET"))])

    outcome = call_judge(cfg, recorder, doc_text="x" * 50, max_doc_chars=50)

    assert len(recorder.requests) == 1
    assert outcome.no_evidence_reason != "over_context"
    assert outcome.state == ItemState.CONDITION_MET


# ---------------------------------------------------------------------------
# Parsing: whole-word whitelist match on the cleaned last non-empty line
# ---------------------------------------------------------------------------


def test_judge_item_parses_markdown_and_punctuation_wrapped_tail_line():
    """'**NOT_MET**.' -- markdown emphasis and trailing punctuation are
    stripped before the whole-word match.
    """
    cfg = make_cfg()
    recorder = RecordingTransport(
        [(200, openrouter_response("The document was reviewed carefully.\n**NOT_MET**."))]
    )

    outcome = call_judge(cfg, recorder)

    assert outcome.state == ItemState.CONDITION_NOT_MET
    assert len(recorder.requests) == 1


def test_judge_item_whole_word_match_with_surrounding_text_accepted():
    """'I think MET' contains a whole-word MET and no NOT_MET, so it is
    accepted (not rejected for carrying extra text).
    """
    cfg = make_cfg()
    recorder = RecordingTransport(
        [(200, openrouter_response("Looking at the evidence.\nI think MET"))]
    )

    outcome = call_judge(cfg, recorder)

    assert outcome.state == ItemState.CONDITION_MET
    assert len(recorder.requests) == 1


def test_judge_item_raw_stores_content_verbatim():
    """raw is the actual response content used to resolve the verdict,
    not the cleaned/stripped token.
    """
    content = "Detailed reasoning line one.\nDetailed reasoning line two.\n**MET**"
    cfg = make_cfg()
    recorder = RecordingTransport([(200, openrouter_response(content))])

    outcome = call_judge(cfg, recorder)

    assert outcome.raw == content
    assert outcome.state == ItemState.CONDITION_MET


# ---------------------------------------------------------------------------
# Parsing failure -> one repair retry
# ---------------------------------------------------------------------------


def test_judge_item_dual_token_tail_line_triggers_repair_then_unparseable():
    """Both MET and NOT_MET present on the tail line is ambiguous: it
    triggers exactly one repair retry (max_tokens=32768, extra message
    appended); still ambiguous the second time -> judge_unparseable.
    """
    ambiguous = "Final answer: MET or NOT_MET"
    cfg = make_cfg()
    recorder = RecordingTransport(
        [
            (200, openrouter_response(ambiguous)),
            (200, openrouter_response(ambiguous)),
        ]
    )

    outcome = call_judge(cfg, recorder)

    assert len(recorder.requests) == 2
    assert outcome.state is None
    assert outcome.incomplete_reason == "judge_unparseable"

    first_body = recorder.body(0)
    second_body = recorder.body(1)
    assert first_body["max_tokens"] == 4096
    assert second_body["max_tokens"] == 32768
    assert len(second_body["messages"]) > len(first_body["messages"])


def test_judge_item_empty_content_triggers_repair_then_succeeds():
    """Empty content on the first attempt triggers the repair retry; a
    parseable second response resolves the verdict.
    """
    cfg = make_cfg()
    second_content = "After reconsidering the material:\nNOT_MET"
    recorder = RecordingTransport(
        [
            (200, openrouter_response("")),
            (200, openrouter_response(second_content)),
        ]
    )

    outcome = call_judge(cfg, recorder)

    assert len(recorder.requests) == 2
    assert outcome.state == ItemState.CONDITION_NOT_MET
    assert outcome.incomplete_reason is None
    assert outcome.raw == second_content
    assert recorder.body(1)["max_tokens"] == 32768


def test_judge_item_finish_reason_length_forces_repair_even_if_parseable():
    """finish_reason == 'length' forces a repair retry even when the tail
    line would otherwise parse cleanly (the answer may have been cut off
    mid-generation and cannot be trusted).
    """
    cfg = make_cfg()
    truncated = openrouter_response("Reasoning that got cut off...\nMET", finish_reason="length")
    recorder = RecordingTransport(
        [
            (200, truncated),
            (200, openrouter_response("Final verdict:\nNOT_MET", finish_reason="stop")),
        ]
    )

    outcome = call_judge(cfg, recorder)

    assert len(recorder.requests) == 2
    # The final state must come from the second (retried) attempt, not
    # the first, truncated one.
    assert outcome.state == ItemState.CONDITION_NOT_MET


# ---------------------------------------------------------------------------
# Retry on transient HTTP / network failures (<=3 attempts)
# ---------------------------------------------------------------------------


def test_judge_item_retries_429_twice_then_succeeds():
    """Two 429s followed by a 200 succeed within the retry budget."""
    cfg = make_cfg()
    recorder = RecordingTransport(
        [
            (429, {"error": "rate_limited"}),
            (429, {"error": "rate_limited"}),
            (200, openrouter_response("Verdict:\nMET")),
        ]
    )

    outcome = call_judge(cfg, recorder)

    assert len(recorder.requests) == 3
    assert outcome.state == ItemState.CONDITION_MET
    assert outcome.incomplete_reason is None


def test_judge_item_retries_5xx_then_succeeds():
    """A 503 followed by a 200 succeeds -- 5xx is retryable like 429."""
    cfg = make_cfg()
    recorder = RecordingTransport(
        [
            (503, {"error": "unavailable"}),
            (200, openrouter_response("Verdict:\nNOT_MET")),
        ]
    )

    outcome = call_judge(cfg, recorder)

    assert len(recorder.requests) == 2
    assert outcome.state == ItemState.CONDITION_NOT_MET


def test_judge_item_network_error_retries_then_succeeds():
    """httpx network-level errors (e.g. connection failures) are retried
    like 429/5xx.
    """
    cfg = make_cfg()
    recorder = RecordingTransport(
        [
            httpx.ConnectError("synthetic connection failure"),
            httpx.ConnectError("synthetic connection failure"),
            (200, openrouter_response("Verdict:\nNOT_MET")),
        ]
    )

    outcome = call_judge(cfg, recorder)

    assert len(recorder.requests) == 3
    assert outcome.state == ItemState.CONDITION_NOT_MET


def test_judge_item_retries_exhausted_after_repeated_5xx():
    """Persistent 5xx failures eventually give up: state=None with a
    non-empty incomplete_reason, after a bounded number of attempts.
    """
    cfg = make_cfg()
    recorder = RecordingTransport([(500, {"error": "boom"})] * 6)

    outcome = call_judge(cfg, recorder)

    assert outcome.state is None
    assert outcome.incomplete_reason
    # More than one attempt (retries happened) but bounded (no infinite loop).
    assert 1 < len(recorder.requests) <= 6


# ---------------------------------------------------------------------------
# Non-retryable failures
# ---------------------------------------------------------------------------


def test_judge_item_non_retryable_4xx_no_retry():
    """A non-429 4xx (e.g. 400 Bad Request) is not retried."""
    cfg = make_cfg()
    recorder = RecordingTransport([(400, {"error": "bad_request"})])

    outcome = call_judge(cfg, recorder)

    assert len(recorder.requests) == 1
    assert outcome.state is None
    assert outcome.incomplete_reason


def test_judge_item_missing_choices_no_retry():
    """A 200 response with no "choices" key is a structural failure, not
    retried.
    """
    cfg = make_cfg()
    recorder = RecordingTransport([(200, {"id": "gen-x", "model": cfg.model})])

    outcome = call_judge(cfg, recorder)

    assert len(recorder.requests) == 1
    assert outcome.state is None
    assert outcome.incomplete_reason


def test_judge_item_non_json_response_no_retry():
    """A response body that isn't valid JSON is not retried."""
    cfg = make_cfg()
    recorder = RecordingTransport([(200, None)])

    outcome = call_judge(cfg, recorder)

    assert len(recorder.requests) == 1
    assert outcome.state is None
    assert outcome.incomplete_reason


# ---------------------------------------------------------------------------
# Response validation: served model/provider must match cfg
# ---------------------------------------------------------------------------


def test_judge_item_provider_mismatch_wrong_provider():
    """A top-level response "provider" not in cfg.provider_order is
    rejected with incomplete_reason=="provider_mismatch", not retried.
    """
    cfg = make_cfg(provider_order=("DeepSeek",))
    body = openrouter_response("Verdict:\nMET", provider="SomeOtherProvider")
    recorder = RecordingTransport([(200, body)])

    outcome = call_judge(cfg, recorder)

    assert len(recorder.requests) == 1
    assert outcome.state is None
    assert outcome.incomplete_reason == "provider_mismatch"


def test_judge_item_provider_mismatch_wrong_model():
    """A response "model" different from cfg.model is also a
    provider_mismatch, even with a cleanly parseable verdict.
    """
    cfg = make_cfg(model="deepseek/deepseek-v4-pro-0813")
    body = openrouter_response("Verdict:\nMET", model="some-other-vendor/other-model")
    recorder = RecordingTransport([(200, body)])

    outcome = call_judge(cfg, recorder)

    assert len(recorder.requests) == 1
    assert outcome.state is None
    assert outcome.incomplete_reason == "provider_mismatch"


def test_judge_item_provider_field_absent_is_not_a_mismatch():
    """When the response carries no top-level "provider" field at all,
    that is not a mismatch (the check only fires when the field is
    present), and served_provider is None.
    """
    cfg = make_cfg(provider_order=("DeepSeek",))
    body = openrouter_response("Verdict:\nMET", provider=None)
    recorder = RecordingTransport([(200, body)])

    outcome = call_judge(cfg, recorder)

    assert outcome.state == ItemState.CONDITION_MET
    assert outcome.served_provider is None


# ---------------------------------------------------------------------------
# cost / served_model / served_provider extraction
# ---------------------------------------------------------------------------


def test_judge_item_cost_defaults_to_zero_when_missing():
    """usage.cost missing from the response -> outcome.cost defaults to 0.0."""
    cfg = make_cfg()
    body = openrouter_response("Verdict:\nMET", cost=None)
    recorder = RecordingTransport([(200, body)])

    outcome = call_judge(cfg, recorder)

    assert outcome.cost == 0.0


def test_judge_item_cost_taken_from_usage_field():
    """outcome.cost is read straight from the response's usage.cost."""
    cfg = make_cfg()
    body = openrouter_response("Verdict:\nMET", cost=0.0042)
    recorder = RecordingTransport([(200, body)])

    outcome = call_judge(cfg, recorder)

    assert outcome.cost == 0.0042


def test_judge_item_served_model_and_provider_captured():
    """served_model/served_provider are read from the response's own
    model/provider fields.
    """
    cfg = make_cfg(provider_order=("DeepSeek",))
    body = openrouter_response("Verdict:\nNOT_MET", model=cfg.model, provider="DeepSeek")
    recorder = RecordingTransport([(200, body)])

    outcome = call_judge(cfg, recorder)

    assert outcome.served_model == cfg.model
    assert outcome.served_provider == "DeepSeek"


# ---------------------------------------------------------------------------
# Negative-polarity semantics: MET == "condition holds", not "looks good"
# ---------------------------------------------------------------------------


def test_judge_item_negative_polarity_violation_met_state():
    """For a criterion describing a violation, a MET verdict must map to
    CONDITION_MET (the deduction applies) -- never CONDITION_NOT_MET.
    This locks in "MET = the condition holds", independent of whether the
    condition is phrased as a positive requirement or a prohibited defect.
    """
    cfg = make_cfg()
    criterion = "The deliverable contains placeholder text."
    doc_text = "Section 3: [TODO fill in later] remains unfinished."
    recorder = RecordingTransport(
        [(200, openrouter_response("The material shows unresolved placeholders.\nMET"))]
    )

    outcome = call_judge(cfg, recorder, criterion=criterion, doc_text=doc_text)

    assert outcome.state == ItemState.CONDITION_MET


def test_judge_item_negative_polarity_no_violation_not_met_state():
    """The same negative-polarity criterion with a NOT_MET verdict (no
    violation found) maps to CONDITION_NOT_MET (no deduction), showing
    the mapping is symmetric and not hardcoded to always deduct.
    """
    cfg = make_cfg()
    criterion = "The deliverable contains placeholder text."
    doc_text = "Section 3 is fully written with no placeholder markers."
    recorder = RecordingTransport(
        [(200, openrouter_response("No unresolved placeholders were found.\nNOT_MET"))]
    )

    outcome = call_judge(cfg, recorder, criterion=criterion, doc_text=doc_text)

    assert outcome.state == ItemState.CONDITION_NOT_MET
