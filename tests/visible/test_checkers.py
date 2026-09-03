"""Visible sample tests for gdpval_eval.checkers.run_check (unit U2).

These are guidance examples for the implementer; the authoritative,
comprehensive contract tests live in tests/hidden/test_checkers.py.

All ExtractedDoc/DocFacts fixtures below are constructed directly (never
parsed from a real file) with wholly fictional sheet names and params, per
the U2 spec's blind-test posture.
"""

from __future__ import annotations

from gdpval_eval.checkers import run_check
from gdpval_eval.extract import DocFacts, ExtractedDoc
from gdpval_eval.models import ItemState


def _make_doc(
    fmt: str,
    *,
    sheet_names: tuple[str, ...] = (),
    has_chart: bool = False,
    formula_count: int = 0,
    page_count: int | None = None,
    slide_count: int | None = None,
    image_count: int = 0,
    text_chars: int = 0,
    truncated: bool = False,
    text: str = "",
) -> ExtractedDoc:
    """Build an ExtractedDoc fixture without ever parsing a real file."""
    facts = DocFacts(
        page_count=page_count,
        sheet_names=sheet_names,
        slide_count=slide_count,
        has_chart=has_chart,
        formula_count=formula_count,
        image_count=image_count,
        text_chars=text_chars,
        truncated=truncated,
    )
    return ExtractedDoc(fmt=fmt, text=text, facts=facts)


def test_checkers_format_is_two_states():
    """format_is applies to every format: doc.fmt == params['fmt'] yields
    CONDITION_MET, any mismatch yields CONDITION_NOT_MET.
    """
    doc = _make_doc("xlsx")

    assert run_check("format_is", {"fmt": "xlsx"}, doc) == ItemState.CONDITION_MET
    assert run_check("format_is", {"fmt": "docx"}, doc) == ItemState.CONDITION_NOT_MET


def test_checkers_sheet_exists_hit():
    """sheet_exists on an xlsx doc matches when the exact sheet name is
    present among doc.facts.sheet_names.
    """
    doc = _make_doc("xlsx", sheet_names=("SynSheetA", "SynSheetB"))

    result = run_check("sheet_exists", {"name": "SynSheetA"}, doc)

    assert result == ItemState.CONDITION_MET


def test_checkers_has_formula_boundary_met():
    """has_formula is MET when formula_count is exactly min_count (>=, not
    strictly greater than).
    """
    doc = _make_doc("xlsx", formula_count=3)

    result = run_check("has_formula", {"min_count": 3}, doc)

    assert result == ItemState.CONDITION_MET
