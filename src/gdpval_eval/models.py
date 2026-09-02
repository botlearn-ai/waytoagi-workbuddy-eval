"""Shared types for the GDPval evaluation harness.

Secrecy: criterion text, prompts, task_ids, rubric_item_ids and per-item
content hashes are all confidential (the dataset is public, so a content
hash is reversible by enumeration). None of them may appear in tracked
files, stdout, log lines, or exception messages. Refer to items by task
ordinal + in-task ordinal instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction


class GdpvalEvalError(Exception):
    """Base class for all errors raised by this package."""


class Channel(Enum):
    """Which judging path a rubric item takes (frozen into the manifest)."""

    DETERMINISTIC = "deterministic"
    TEXT = "text"
    VISION = "vision"


class ChannelReason(Enum):
    """Why a rubric item was assigned its channel. Enum only — a free-text
    reason would end up carrying criterion-derived text into the manifest
    and CLI reports."""

    LAYOUT_KEYWORD = "layout_keyword"
    DETERMINISTIC_KEYWORD = "deterministic_keyword"
    DEFAULT = "default"


@dataclass(frozen=True)
class ChannelDecision:
    channel: Channel
    reason: ChannelReason


class ItemState(Enum):
    """Judgement outcome of one rubric item for one product's deliverable.

    CONDITION_MET means the item's scoring condition holds. For a
    positive-score item that is "the requirement is satisfied"; for a
    negative-score item it is "the described violation occurred", so the
    penalty applies. NO_EVIDENCE is reserved for harness-side failures
    (page render missing, content beyond judge context, …): missing work
    in the deliverable is CONDITION_NOT_MET, never NO_EVIDENCE. An
    unfinished judgement is not a state — such items are re-judged and
    must be resolved before a task can be scored.
    """

    CONDITION_MET = "condition_met"
    CONDITION_NOT_MET = "condition_not_met"
    NO_EVIDENCE = "no_evidence"


@dataclass(frozen=True)
class RubricItem:
    """One rubric item as read from the upstream dataset.

    Identity is the upstream `rubric_item_id` (globally unique — verified
    over all 10,453 items). The content hash (hashing.py) is a drift
    detector, not an identity: identical (criterion, score) pairs occur
    within single tasks upstream. `tags` semantics are undocumented
    upstream; archived verbatim. `score` may be negative, never 0.
    """

    rubric_item_id: str
    criterion: str
    score: int
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Task:
    """One GDPval task ("题")."""

    task_id: str
    sector: str
    occupation: str
    prompt: str
    reference_files: tuple[str, ...]
    reference_file_urls: tuple[str, ...]
    deliverable_files: tuple[str, ...]
    deliverable_file_urls: tuple[str, ...]
    rubric: tuple[RubricItem, ...]

    @property
    def deliverable_formats(self) -> set[str]:
        """Lower-cased extensions of the gold deliverable files, without dots."""
        return {
            name.rsplit(".", 1)[-1].lower()
            for name in self.deliverable_files
            if "." in name
        }


@dataclass(frozen=True)
class JudgedItem:
    """A rubric item's score together with its judgement state for one product."""

    score: int
    state: ItemState


class ScoreStatus(Enum):
    SCORED = "scored"
    NO_DENOMINATOR = "no_denominator"


@dataclass(frozen=True)
class TaskScore:
    """Result of scoring one task for one product (5-point scale).

    Exact arithmetic end to end: `earned` and `score_5` are Fractions so
    that anyone can recompute the published numbers bit-for-bit; rounding
    happens only at the report/display layer. NO_DENOMINATOR tasks count
    as 0 toward the 100-point total and are annotated in the report.
    """

    status: ScoreStatus
    score_5: Fraction
    earned: Fraction
    denominator: int
    full_base: int
    no_evidence_points: int
    no_evidence_ratio: Fraction
    capped_item_count: int
    deduction_capped: bool
    needs_review: bool
