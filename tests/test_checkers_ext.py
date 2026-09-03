"""Tests for the two architect-added checkers (text_excludes, sheet_exists_ci).

Added after U2 delivery to serve exam items whose criteria demand
substring-absence scanning and case-insensitive sheet matching. All
fixtures are synthetic.
"""

from __future__ import annotations

from gdpval_eval.checkers import run_check
from gdpval_eval.extract import DocFacts, ExtractedDoc
from gdpval_eval.models import ItemState


def _doc(fmt: str = "xlsx", text: str = "synthetic cell text", **facts) -> ExtractedDoc:
    defaults = dict(
        page_count=None,
        sheet_names=(),
        slide_count=None,
        has_chart=False,
        formula_count=0,
        image_count=0,
        text_chars=len(text),
        truncated=False,
    )
    defaults.update(facts)
    return ExtractedDoc(fmt=fmt, text=text, facts=DocFacts(**defaults))


def test_checkers_ext_text_excludes_met_when_no_needle_present():
    doc = _doc(text="SynSheet!A1: 42\nSynSheet!B2: total")
    result = run_check("text_excludes", {"needles": ["#REF!", "#VALUE!"]}, doc)
    assert result == ItemState.CONDITION_MET


def test_checkers_ext_text_excludes_not_met_when_any_needle_present():
    doc = _doc(text="SynSheet!A1: #REF!\nSynSheet!B2: 7")
    result = run_check("text_excludes", {"needles": ["#REF!", "#VALUE!"]}, doc)
    assert result == ItemState.CONDITION_NOT_MET


def test_checkers_ext_text_excludes_applies_to_all_formats():
    for fmt in ("xlsx", "docx", "pptx", "pdf"):
        doc = _doc(fmt=fmt, text="clean synthetic body")
        assert run_check("text_excludes", {"needles": ["#REF!"]}, doc) == ItemState.CONDITION_MET


def test_checkers_ext_sheet_exists_ci_matches_case_insensitively():
    doc = _doc(sheet_names=("DATA", "Sales By Brand"))
    assert run_check("sheet_exists_ci", {"name": "Data"}, doc) == ItemState.CONDITION_MET
    assert run_check("sheet_exists_ci", {"name": "sales by brand"}, doc) == (
        ItemState.CONDITION_MET
    )
    assert run_check("sheet_exists_ci", {"name": "Sales by Store"}, doc) == (
        ItemState.CONDITION_NOT_MET
    )


def test_checkers_ext_sheet_exists_ci_non_xlsx_inapplicable():
    doc = _doc(fmt="pdf", sheet_names=())
    assert run_check("sheet_exists_ci", {"name": "Data"}, doc) is None
