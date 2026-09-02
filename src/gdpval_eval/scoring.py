"""Task scoring: three item states plus two protection rules (spec §4.1–4.2)."""

from __future__ import annotations

from gdpval_eval.models import JudgedItem, TaskScore


def score_task(items: list[JudgedItem]) -> TaskScore:
    raise NotImplementedError
