"""Hidden contract tests for gdpval_eval.checkers.run_check (unit U2).

Blind-graded: not visible to the implementer. All ExtractedDoc/DocFacts
fixtures are constructed directly (never parsed from a real file), with
wholly fictional sheet names, formats, and params -- nothing here is a
real deliverable, criterion, or rubric_item_id.

Contract under test (from the work-unit spec):
- Four checkers only: format_is, sheet_exists, has_chart, has_formula.
- format_is{fmt}: applies to every format. doc.fmt == fmt -> CONDITION_MET,
  else CONDITION_NOT_MET.
- sheet_exists{name}: xlsx only (other formats -> None). Exact match of
  `name` against one of doc.facts.sheet_names -> CONDITION_MET, else
  CONDITION_NOT_MET.
- has_chart{expect}: xlsx/pptx only (docx/pdf -> None). doc.facts.has_chart
  == expect -> CONDITION_MET, else CONDITION_NOT_MET.
- has_formula{min_count}: xlsx only (other formats -> None).
  doc.facts.formula_count >= min_count -> CONDITION_MET, else
  CONDITION_NOT_MET.
- Unknown check name -> ValueError. Return values and exception messages
  never echo any params value (secrets-level; asserted with sentinel
  strings that must never appear in the raised message).
- None is returned only for the "format not applicable" cases above --
  there is no other source of a None return.
"""

from __future__ import annotations

import pytest

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


# ---------------------------------------------------------------------------
# format_is: applies to all four formats
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["xlsx", "docx", "pptx", "pdf"])
def test_checkers_format_is_matches_every_format(fmt):
    """format_is is applicable to every format and yields CONDITION_MET
    when doc.fmt equals the requested fmt.
    """
    doc = _make_doc(fmt)

    assert run_check("format_is", {"fmt": fmt}, doc) == ItemState.CONDITION_MET


@pytest.mark.parametrize("fmt", ["xlsx", "docx", "pptx", "pdf"])
def test_checkers_format_is_mismatches_every_format(fmt):
    """format_is yields CONDITION_NOT_MET when doc.fmt differs from the
    requested fmt, for every format.
    """
    other = {"xlsx": "docx", "docx": "pptx", "pptx": "pdf", "pdf": "xlsx"}[fmt]
    doc = _make_doc(fmt)

    assert run_check("format_is", {"fmt": other}, doc) == ItemState.CONDITION_NOT_MET


# ---------------------------------------------------------------------------
# sheet_exists: xlsx only, exact match
# ---------------------------------------------------------------------------


def test_checkers_sheet_exists_met_exact_match():
    """An exact sheet-name match against doc.facts.sheet_names yields MET."""
    doc = _make_doc("xlsx", sheet_names=("SynSheetA", "SynSheetB", "SynSheetC"))

    result = run_check("sheet_exists", {"name": "SynSheetB"}, doc)

    assert result == ItemState.CONDITION_MET


def test_checkers_sheet_exists_not_met_no_match():
    """A name absent from sheet_names yields CONDITION_NOT_MET."""
    doc = _make_doc("xlsx", sheet_names=("SynSheetA", "SynSheetB"))

    result = run_check("sheet_exists", {"name": "SynSheetZZZ"}, doc)

    assert result == ItemState.CONDITION_NOT_MET


def test_checkers_sheet_exists_case_sensitive_mismatch():
    """Matching is exact (case-sensitive): a differently-cased name that is
    otherwise identical does not count as a match.
    """
    doc = _make_doc("xlsx", sheet_names=("SynSheetA",))

    result = run_check("sheet_exists", {"name": "synsheeta"}, doc)

    assert result == ItemState.CONDITION_NOT_MET


def test_checkers_sheet_exists_empty_sheet_names_not_met():
    """An xlsx doc with no sheet names at all is a valid NOT_MET, not None
    -- xlsx is still the applicable format even if sheet_names is empty.
    """
    doc = _make_doc("xlsx", sheet_names=())

    result = run_check("sheet_exists", {"name": "SynSheetA"}, doc)

    assert result == ItemState.CONDITION_NOT_MET


@pytest.mark.parametrize("fmt", ["docx", "pptx", "pdf"])
def test_checkers_sheet_exists_not_applicable_non_xlsx(fmt):
    """sheet_exists only applies to xlsx; every other format returns None
    so the orchestrator can record the item as incomplete.
    """
    doc = _make_doc(fmt)

    result = run_check("sheet_exists", {"name": "SynSheetA"}, doc)

    assert result is None


# ---------------------------------------------------------------------------
# has_chart: xlsx/pptx only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["xlsx", "pptx"])
def test_checkers_has_chart_met_expect_true_present(fmt):
    """expect=True and a chart present in the doc -> CONDITION_MET, for
    both applicable formats (xlsx, pptx).
    """
    doc = _make_doc(fmt, has_chart=True)

    result = run_check("has_chart", {"expect": True}, doc)

    assert result == ItemState.CONDITION_MET


@pytest.mark.parametrize("fmt", ["xlsx", "pptx"])
def test_checkers_has_chart_not_met_expect_true_absent(fmt):
    """expect=True but no chart present -> CONDITION_NOT_MET."""
    doc = _make_doc(fmt, has_chart=False)

    result = run_check("has_chart", {"expect": True}, doc)

    assert result == ItemState.CONDITION_NOT_MET


def test_checkers_has_chart_met_expect_false_absent():
    """Negative expectation: expect=False and no chart present is also a
    match -> CONDITION_MET (not just expect=True is testable).
    """
    doc = _make_doc("xlsx", has_chart=False)

    result = run_check("has_chart", {"expect": False}, doc)

    assert result == ItemState.CONDITION_MET


def test_checkers_has_chart_not_met_expect_false_present():
    """expect=False but a chart is present -> CONDITION_NOT_MET."""
    doc = _make_doc("pptx", has_chart=True)

    result = run_check("has_chart", {"expect": False}, doc)

    assert result == ItemState.CONDITION_NOT_MET


@pytest.mark.parametrize("fmt", ["docx", "pdf"])
def test_checkers_has_chart_not_applicable_docx_pdf(fmt):
    """has_chart only applies to xlsx/pptx; docx and pdf return None."""
    doc = _make_doc(fmt, has_chart=True)

    result = run_check("has_chart", {"expect": True}, doc)

    assert result is None


# ---------------------------------------------------------------------------
# has_formula: xlsx only
# ---------------------------------------------------------------------------


def test_checkers_has_formula_met_exceeds_min():
    """formula_count strictly greater than min_count -> CONDITION_MET."""
    doc = _make_doc("xlsx", formula_count=10)

    result = run_check("has_formula", {"min_count": 3}, doc)

    assert result == ItemState.CONDITION_MET


def test_checkers_has_formula_boundary_equals_min_is_met():
    """formula_count exactly equal to min_count is MET (>=, inclusive)."""
    doc = _make_doc("xlsx", formula_count=5)

    result = run_check("has_formula", {"min_count": 5}, doc)

    assert result == ItemState.CONDITION_MET


def test_checkers_has_formula_not_met_below_min():
    """formula_count below min_count -> CONDITION_NOT_MET."""
    doc = _make_doc("xlsx", formula_count=1)

    result = run_check("has_formula", {"min_count": 5}, doc)

    assert result == ItemState.CONDITION_NOT_MET


def test_checkers_has_formula_zero_min_count_always_met():
    """min_count=0 is always satisfied, even with zero formulas."""
    doc = _make_doc("xlsx", formula_count=0)

    result = run_check("has_formula", {"min_count": 0}, doc)

    assert result == ItemState.CONDITION_MET


@pytest.mark.parametrize("fmt", ["docx", "pptx", "pdf"])
def test_checkers_has_formula_not_applicable_non_xlsx(fmt):
    """has_formula only applies to xlsx; every other format returns None."""
    doc = _make_doc(fmt, formula_count=99)

    result = run_check("has_formula", {"min_count": 1}, doc)

    assert result is None


# ---------------------------------------------------------------------------
# Unknown check name -> ValueError, params never echoed
# ---------------------------------------------------------------------------


def test_checkers_unknown_check_raises_valueerror():
    """A check name outside the four known checkers raises ValueError."""
    doc = _make_doc("xlsx")

    with pytest.raises(ValueError):
        run_check("has_synthetic_unicorn", {}, doc)


def test_checkers_unknown_check_does_not_leak_string_param():
    """The ValueError message must never contain a params value -- checker
    params are criterion-derived, secrets-level text.
    """
    doc = _make_doc("xlsx")
    sentinel = "SYN_SENTINEL_STRVAL_7f3c9a"

    with pytest.raises(ValueError) as exc_info:
        run_check("not_a_real_check", {"fmt": sentinel}, doc)

    assert sentinel not in str(exc_info.value)


def test_checkers_unknown_check_does_not_leak_numeric_or_other_params():
    """Same non-leak guarantee for numeric and boolean params values, and
    for multiple params keys at once.
    """
    doc = _make_doc("xlsx")
    numeric_sentinel = 424242
    name_sentinel = "SynSheetSentinelUnique"

    with pytest.raises(ValueError) as exc_info:
        run_check(
            "definitely_unknown_check",
            {"min_count": numeric_sentinel, "name": name_sentinel, "expect": True},
            doc,
        )

    message = str(exc_info.value)
    assert str(numeric_sentinel) not in message
    assert name_sentinel not in message


def test_checkers_known_check_result_does_not_echo_params():
    """A successful (non-error) checker call also never echoes params back
    in its return value -- the return is strictly an ItemState or None.
    """
    doc = _make_doc("xlsx", sheet_names=("SynSheetUniqueMarker",))

    result = run_check("sheet_exists", {"name": "SynSheetUniqueMarker"}, doc)

    assert isinstance(result, ItemState)
    assert not isinstance(result, str)
