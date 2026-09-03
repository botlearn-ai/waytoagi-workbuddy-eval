"""Deliverable parsing across four formats (spec U1).

Secrecy: this module only ever sees a deliverable file's own bytes, which
are not confidential in the AGENTS.md sense (criterion text, prompts,
task_ids and rubric_item_ids never pass through here). Error messages
still stay format-and-number-only, never the extracted text, so nothing
about a submission's *content* leaks into logs or exception messages.

Untrusted-parsing posture: xlsx/docx/pptx are zip containers and are
pre-checked with `zipfile` before any OOXML library touches them (entry
count, per-entry size, total decompressed size). xlsx additionally goes
through openpyxl with defusedxml enabled, which is asserted at import
time — this is what stops XML entity-expansion ("billion laughs") bombs
hidden inside a small, zip-safety-check-passing xlsx file.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

import openpyxl
import pypdf
from docx import Document as DocxDocument
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from gdpval_eval.models import GdpvalEvalError

if not openpyxl.DEFUSEDXML:
    raise ImportError(
        "openpyxl.DEFUSEDXML is False. Install the 'defusedxml' package "
        "(uv add defusedxml) so openpyxl rejects XML entity-expansion "
        "attacks before gdpval_eval.extract is imported."
    )

_SUPPORTED_FORMATS = ("xlsx", "docx", "pptx", "pdf")
_ZIP_FORMATS = ("xlsx", "docx", "pptx")

_MAX_ZIP_ENTRY_BYTES = 50 * 1024 * 1024
_MAX_ZIP_TOTAL_BYTES = 500 * 1024 * 1024
_MAX_ZIP_ENTRIES = 10_000
_MAX_XLSX_CELLS = 200_000
_MAX_PDF_PAGES = 500
_MAX_PDF_TEXT_CHARS = 2_000_000


class ExtractError(GdpvalEvalError):
    """A deliverable could not be safely or successfully parsed.

    Messages carry only the format name and numeric limits/counts —
    never file content.
    """


@dataclass(frozen=True)
class DocFacts:
    page_count: int | None
    sheet_names: tuple[str, ...]
    slide_count: int | None
    has_chart: bool
    formula_count: int
    image_count: int
    text_chars: int
    truncated: bool


@dataclass(frozen=True)
class ExtractedDoc:
    fmt: str
    text: str
    facts: DocFacts


def extract_document(path: Path) -> ExtractedDoc:
    fmt = _format_of(path)
    if fmt in _ZIP_FORMATS:
        _check_zip_safety(path, fmt)
    if fmt == "xlsx":
        return _extract_xlsx(path)
    if fmt == "docx":
        return _extract_docx(path)
    if fmt == "pptx":
        return _extract_pptx(path)
    return _extract_pdf(path)


def _format_of(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext not in _SUPPORTED_FORMATS:
        raise ExtractError(f"unsupported deliverable extension: {ext!r}")
    return ext


def _check_zip_safety(path: Path, fmt: str) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ZIP_ENTRIES:
                raise ExtractError(
                    f"{fmt}: zip entry count {len(infos)} exceeds limit {_MAX_ZIP_ENTRIES}"
                )
            total = 0
            for info in infos:
                if info.file_size > _MAX_ZIP_ENTRY_BYTES:
                    raise ExtractError(
                        f"{fmt}: zip entry size {info.file_size} exceeds "
                        f"limit {_MAX_ZIP_ENTRY_BYTES}"
                    )
                total += info.file_size
                if total > _MAX_ZIP_TOTAL_BYTES:
                    raise ExtractError(
                        f"{fmt}: zip uncompressed total {total} exceeds "
                        f"limit {_MAX_ZIP_TOTAL_BYTES}"
                    )
            bad_entry = zf.testzip()
            if bad_entry is not None:
                raise ExtractError(f"{fmt}: corrupted zip entry detected")
    except ExtractError:
        raise
    except zipfile.BadZipFile as exc:
        raise ExtractError(f"{fmt}: corrupted or invalid zip container") from exc
    except RuntimeError as exc:
        raise ExtractError(f"{fmt}: zip container requires a password") from exc
    except OSError as exc:
        raise ExtractError(f"{fmt}: unable to open file ({type(exc).__name__})") from exc


def _walk_cells(worksheets, cap: int, on_cell) -> bool:
    """Call `on_cell(sheet_title, cell)` for cells up to `cap` total.

    Returns True if the worksheets together hold more than `cap` cells
    (collection was stopped early).
    """
    count = 0
    for ws in worksheets:
        for row in ws.iter_rows():
            for cell in row:
                count += 1
                if count > cap:
                    return True
                on_cell(ws.title, cell)
    return False


def _extract_xlsx(path: Path) -> ExtractedDoc:
    try:
        values_wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:
        raise ExtractError(f"xlsx: failed to parse workbook ({type(exc).__name__})") from exc

    lines: list[str] = []

    def _collect_value(sheet_title: str, cell) -> None:
        if cell.value is not None:
            lines.append(f"{sheet_title}!{cell.coordinate}: {cell.value}")

    try:
        sheet_names = tuple(values_wb.sheetnames)
        value_truncated = _walk_cells(values_wb.worksheets, _MAX_XLSX_CELLS, _collect_value)
    except Exception as exc:
        raise ExtractError(f"xlsx: failed to read worksheet cells ({type(exc).__name__})") from exc
    finally:
        values_wb.close()

    try:
        formula_wb = openpyxl.load_workbook(path, data_only=False, read_only=False)
    except Exception as exc:
        raise ExtractError(f"xlsx: failed to parse workbook ({type(exc).__name__})") from exc

    formula_count = 0

    def _collect_formula(_sheet_title: str, cell) -> None:
        nonlocal formula_count
        if cell.data_type == "f":
            formula_count += 1

    try:
        has_chart = any(getattr(ws, "_charts", None) for ws in formula_wb.worksheets)
        formula_truncated = _walk_cells(formula_wb.worksheets, _MAX_XLSX_CELLS, _collect_formula)
    except Exception as exc:
        raise ExtractError(f"xlsx: failed to read worksheet cells ({type(exc).__name__})") from exc
    finally:
        formula_wb.close()

    text = "\n".join(lines)
    facts = DocFacts(
        page_count=None,
        sheet_names=sheet_names,
        slide_count=None,
        has_chart=has_chart,
        formula_count=formula_count,
        image_count=0,
        text_chars=len(text),
        truncated=value_truncated or formula_truncated,
    )
    return ExtractedDoc(fmt="xlsx", text=text, facts=facts)


def _extract_docx(path: Path) -> ExtractedDoc:
    try:
        doc = DocxDocument(str(path))
    except Exception as exc:
        raise ExtractError(f"docx: failed to parse document ({type(exc).__name__})") from exc

    lines = [p.text for p in doc.paragraphs if p.text]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text:
                    lines.append(cell.text)

    text = "\n".join(lines)
    facts = DocFacts(
        page_count=None,
        sheet_names=(),
        slide_count=None,
        has_chart=False,
        formula_count=0,
        image_count=len(doc.inline_shapes),
        text_chars=len(text),
        truncated=False,
    )
    return ExtractedDoc(fmt="docx", text=text, facts=facts)


def _extract_pptx(path: Path) -> ExtractedDoc:
    try:
        prs = Presentation(str(path))
    except Exception as exc:
        raise ExtractError(f"pptx: failed to parse presentation ({type(exc).__name__})") from exc

    lines: list[str] = []
    has_chart = False
    image_count = 0

    def _walk_shapes(shapes, slide_index: int) -> None:
        # Tables and grouped shapes carry real deliverable content (a whole
        # slide can be one table); missing them silently starves the judge.
        nonlocal has_chart, image_count
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                _walk_shapes(shape.shapes, slide_index)
                continue
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    row_text = " | ".join(cell.text for cell in row.cells)
                    if row_text.strip():
                        lines.append(f"Slide {slide_index} table: {row_text}")
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text:
                lines.append(f"Slide {slide_index}: {shape.text_frame.text}")
            if getattr(shape, "has_chart", False):
                has_chart = True
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                image_count += 1

    for slide_index, slide in enumerate(prs.slides, start=1):
        _walk_shapes(slide.shapes, slide_index)

    text = "\n".join(lines)
    facts = DocFacts(
        page_count=None,
        sheet_names=(),
        slide_count=len(prs.slides),
        has_chart=has_chart,
        formula_count=0,
        image_count=image_count,
        text_chars=len(text),
        truncated=False,
    )
    return ExtractedDoc(fmt="pptx", text=text, facts=facts)


def _extract_pdf(path: Path) -> ExtractedDoc:
    try:
        reader = pypdf.PdfReader(str(path))
        page_count = len(reader.pages)
    except Exception as exc:
        raise ExtractError(f"pdf: failed to parse document ({type(exc).__name__})") from exc

    if page_count > _MAX_PDF_PAGES:
        raise ExtractError(f"pdf: page count {page_count} exceeds limit {_MAX_PDF_PAGES}")

    lines: list[str] = []
    total_chars = 0
    truncated = False
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            raise ExtractError(
                f"pdf: failed to extract page text ({type(exc).__name__})"
            ) from exc
        if page_text:
            lines.append(page_text)
            total_chars += len(page_text)
        # Content streams decompress without a zip-style size gate; cap the
        # total extracted text so a pdf decompression bomb cannot OOM us.
        if total_chars > _MAX_PDF_TEXT_CHARS:
            truncated = True
            break

    text = "\n".join(lines)
    facts = DocFacts(
        page_count=page_count,
        sheet_names=(),
        slide_count=None,
        has_chart=False,
        formula_count=0,
        image_count=0,
        text_chars=len(text),
        truncated=truncated,
    )
    return ExtractedDoc(fmt="pdf", text=text, facts=facts)
