"""Tests for src/gdpval_eval/exam.py: exam spec validation (spec T6).

All task ids, rubric item ids, and criterion text below are synthetic,
invented for this test file only (see AGENTS.md secrecy rules).
"""

from __future__ import annotations

import dataclasses
from fractions import Fraction

import pytest

from gdpval_eval.exam import ExamSpecError, load_exam_task_ids, validate_exam
from gdpval_eval.models import RubricItem, Task

# Seven positive items of score 1 (full_base=7, max share = 1/7 ~= 14.3%,
# under the 15% cap) plus one negative item (doesn't count toward full_base).
DEFAULT_SCORES = [1, 1, 1, 1, 1, 1, 1, -2]


def _task(task_idx: int, scores: list[int], *, formats: tuple[str, ...] = ("docx",)) -> Task:
    rubric = tuple(
        RubricItem(
            rubric_item_id=f"synthetic-item-{task_idx:02d}-{item_idx:02d}",
            criterion=f"Fictional synthetic criterion {task_idx}-{item_idx}.",
            score=score,
        )
        for item_idx, score in enumerate(scores, start=1)
    )
    return Task(
        task_id=f"synthetic-task-{task_idx:02d}",
        sector="fictional-sector",
        occupation="fictional-occupation",
        prompt="Fictional synthetic prompt for testing.",
        reference_files=(),
        reference_file_urls=(),
        deliverable_files=tuple(f"deliverable-{task_idx:02d}.{fmt}" for fmt in formats),
        deliverable_file_urls=tuple(
            f"https://example.invalid/deliverable-{task_idx:02d}.{fmt}" for fmt in formats
        ),
        rubric=rubric,
    )


def _default_exam() -> tuple[list[Task], list[str]]:
    tasks = [_task(i, DEFAULT_SCORES) for i in range(1, 21)]
    task_ids = [t.task_id for t in tasks]
    return tasks, task_ids


def test_exam_validate_computes_stats():
    tasks, task_ids = _default_exam()

    report = validate_exam(tasks, task_ids)

    assert len(report.per_task) == 20

    first = report.per_task[0]
    assert first.ordinal == 1
    assert first.rubric_count == 8
    assert first.full_base == 7
    assert first.max_positive_share == Fraction(1, 7)
    assert first.deliverable_formats == frozenset({"docx"})

    assert report.per_task[-1].ordinal == 20
    assert report.totals.total_items == 160
    assert report.totals.score_distribution == {1: 140, -2: 20}


def test_exam_validate_missing_task_id_raises():
    tasks, task_ids = _default_exam()
    task_ids[10] = "synthetic-task-does-not-exist"  # ordinal 11

    with pytest.raises(ExamSpecError) as exc_info:
        validate_exam(tasks, task_ids)

    message = str(exc_info.value)
    assert "11" in message
    for t in tasks:
        assert t.task_id not in message


def test_exam_validate_score_exceeds_fifteen_percent_raises():
    tasks, task_ids = _default_exam()
    # full_base = 6*1 + 5 = 11; 100*5=500 > 15*11=165 -> exceeds 15% cap.
    tasks[3] = _task(4, [1, 1, 1, 1, 1, 1, 5])

    with pytest.raises(ExamSpecError) as exc_info:
        validate_exam(tasks, task_ids)

    message = str(exc_info.value)
    assert "4" in message
    for t in tasks:
        assert t.task_id not in message
    for item in tasks[3].rubric:
        assert item.rubric_item_id not in message


def test_exam_validate_duplicate_rubric_item_id_raises():
    tasks, task_ids = _default_exam()
    duplicate_id = tasks[2].rubric[0].rubric_item_id  # task ordinal 3, item ordinal 1
    items = list(tasks[5].rubric)
    items[1] = dataclasses.replace(items[1], rubric_item_id=duplicate_id)  # ordinal 6, item 2
    tasks[5] = dataclasses.replace(tasks[5], rubric=tuple(items))

    with pytest.raises(ExamSpecError) as exc_info:
        validate_exam(tasks, task_ids)

    message = str(exc_info.value)
    assert "6" in message
    assert "2" in message
    for t in tasks:
        assert t.task_id not in message
    assert duplicate_id not in message


def test_exam_validate_invalid_format_raises():
    tasks, task_ids = _default_exam()
    tasks[7] = dataclasses.replace(tasks[7], deliverable_files=(), deliverable_file_urls=())

    with pytest.raises(ExamSpecError) as exc_info:
        validate_exam(tasks, task_ids)

    message = str(exc_info.value)
    assert "8" in message
    for t in tasks:
        assert t.task_id not in message


def test_exam_load_task_ids_reads_twenty_ids(tmp_path):
    ids = [f"synthetic-task-{i:02d}" for i in range(1, 21)]
    content = f"\n{ids[0]}\n  \n" + "\n".join(f"  {i}  " for i in ids[1:]) + "\n\n"
    path = tmp_path / "task_ids.txt"
    path.write_text(content)

    result = load_exam_task_ids(path)

    assert result == ids


def test_exam_load_task_ids_wrong_count_raises(tmp_path):
    ids = [f"synthetic-task-{i:02d}" for i in range(1, 20)]  # only 19
    path = tmp_path / "task_ids.txt"
    path.write_text("\n".join(ids) + "\n")

    with pytest.raises(ExamSpecError) as exc_info:
        load_exam_task_ids(path)

    message = str(exc_info.value)
    assert "19" in message
    for i in ids:
        assert i not in message


def test_exam_load_task_ids_duplicate_raises(tmp_path):
    ids = [f"synthetic-task-{i:02d}" for i in range(1, 20)]
    ids.append(ids[0])  # 20 lines total, one id repeated
    path = tmp_path / "task_ids.txt"
    path.write_text("\n".join(ids) + "\n")

    with pytest.raises(ExamSpecError) as exc_info:
        load_exam_task_ids(path)

    message = str(exc_info.value)
    assert "1" in message
    for i in set(ids):
        assert i not in message
