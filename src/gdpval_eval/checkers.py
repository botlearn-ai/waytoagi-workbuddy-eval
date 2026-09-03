"""Parameterized deterministic checkers (spec U2). Blind-test unit.

A checker inspects the ExtractedDoc facts only — never the raw file. It
returns an ItemState, or None when the required fact does not apply to the
document's format (the orchestrator records such items as incomplete for
human review — never NO_EVIDENCE, which would silently inflate scores).
Return values and exception messages never echo `params` (checker params
are criterion-derived text, secrets-level).
"""

from __future__ import annotations

from gdpval_eval.extract import ExtractedDoc
from gdpval_eval.models import ItemState


def run_check(check: str, params: dict, doc: ExtractedDoc) -> ItemState | None:
    if check == "format_is":
        return (
            ItemState.CONDITION_MET
            if doc.fmt == params["fmt"]
            else ItemState.CONDITION_NOT_MET
        )

    if check == "sheet_exists":
        if doc.fmt != "xlsx":
            return None
        return (
            ItemState.CONDITION_MET
            if params["name"] in doc.facts.sheet_names
            else ItemState.CONDITION_NOT_MET
        )

    if check == "has_chart":
        if doc.fmt not in ("xlsx", "pptx"):
            return None
        return (
            ItemState.CONDITION_MET
            if doc.facts.has_chart == params["expect"]
            else ItemState.CONDITION_NOT_MET
        )

    if check == "has_formula":
        if doc.fmt != "xlsx":
            return None
        return (
            ItemState.CONDITION_MET
            if doc.facts.formula_count >= params["min_count"]
            else ItemState.CONDITION_NOT_MET
        )

    if check == "text_excludes":
        return (
            ItemState.CONDITION_NOT_MET
            if any(needle in doc.text for needle in params["needles"])
            else ItemState.CONDITION_MET
        )

    if check == "sheet_exists_ci":
        if doc.fmt != "xlsx":
            return None
        wanted = params["name"].casefold()
        return (
            ItemState.CONDITION_MET
            if any(name.casefold() == wanted for name in doc.facts.sheet_names)
            else ItemState.CONDITION_NOT_MET
        )

    raise ValueError(f"unknown check: {check!r}")
