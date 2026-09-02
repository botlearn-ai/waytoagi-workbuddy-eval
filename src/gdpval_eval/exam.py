"""Exam spec validation: task id list shape + per-task rubric statistics.

Secrecy: task_id, rubric_item_id, content hashes and criterion text never
appear in ExamReport fields or ExamSpecError messages — items are located
by task ordinal (position in the exam's task_ids list, 1-based) and
in-task item ordinal (position in that task's rubric, 1-based) only.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from gdpval_eval.models import GdpvalEvalError, Task

ALLOWED_FORMATS = frozenset({"xlsx", "docx", "pptx", "pdf"})


class ExamSpecError(GdpvalEvalError):
    """Raised when the exam spec or its task id list fails structural
    validation. Messages carry only counts and ordinals — never task_id,
    rubric_item_id, content hashes, or criterion text."""


@dataclass(frozen=True)
class TaskStats:
    """Per-task rubric statistics, keyed by ordinal rather than task_id."""

    ordinal: int
    rubric_count: int
    full_base: int
    max_positive_share: Fraction
    deliverable_formats: frozenset[str]


@dataclass(frozen=True)
class ExamTotals:
    """Exam-wide aggregates."""

    total_items: int
    score_distribution: dict[int, int]


@dataclass(frozen=True)
class ExamReport:
    """Full validation report for one exam. No task_id or criterion."""

    per_task: list[TaskStats]
    totals: ExamTotals


def load_exam_task_ids(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    ids = [line.strip() for line in lines if line.strip()]

    if len(ids) != 20:
        raise ExamSpecError(f"expected exactly 20 exam task ids, found {len(ids)}")

    duplicate_count = len(ids) - len(set(ids))
    if duplicate_count:
        raise ExamSpecError(f"exam task id list contains {duplicate_count} duplicate id(s)")

    return ids


def validate_exam(tasks: list[Task], task_ids: list[str]) -> ExamReport:
    tasks_by_id = {task.task_id: task for task in tasks}
    seen_rubric_item_ids: set[str] = set()
    per_task: list[TaskStats] = []
    score_distribution: dict[int, int] = {}
    total_items = 0

    for ordinal, task_id in enumerate(task_ids, start=1):
        task = tasks_by_id.get(task_id)
        if task is None:
            raise ExamSpecError(
                f"exam task ordinal {ordinal} references a task_id absent from the loaded dataset"
            )

        formats = frozenset(task.deliverable_formats)
        if not formats or not formats.issubset(ALLOWED_FORMATS):
            raise ExamSpecError(
                f"exam task ordinal {ordinal} has an empty or invalid deliverable format set"
            )

        full_base = sum(item.score for item in task.rubric if item.score > 0)
        max_positive_score = 0

        for item_ordinal, item in enumerate(task.rubric, start=1):
            if item.rubric_item_id in seen_rubric_item_ids:
                raise ExamSpecError(
                    f"exam task ordinal {ordinal} item ordinal {item_ordinal} duplicates a "
                    "rubric_item_id already seen elsewhere in the exam"
                )
            seen_rubric_item_ids.add(item.rubric_item_id)

            score_distribution[item.score] = score_distribution.get(item.score, 0) + 1
            total_items += 1

            if item.score > 0:
                if 100 * item.score > 15 * full_base:
                    raise ExamSpecError(
                        f"exam task ordinal {ordinal} item ordinal {item_ordinal} exceeds 15% "
                        "of the task's positive score base"
                    )
                max_positive_score = max(max_positive_score, item.score)

        max_positive_share = (
            Fraction(max_positive_score, full_base) if full_base else Fraction(0)
        )

        per_task.append(
            TaskStats(
                ordinal=ordinal,
                rubric_count=len(task.rubric),
                full_base=full_base,
                max_positive_share=max_positive_share,
                deliverable_formats=formats,
            )
        )

    totals = ExamTotals(total_items=total_items, score_distribution=score_distribution)
    return ExamReport(per_task=per_task, totals=totals)
