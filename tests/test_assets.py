"""Tests for src/gdpval_eval/assets.py: task file download (no real network).

All URLs use the reserved `example.invalid` domain and httpx.MockTransport —
no request ever leaves the process. Task fixtures use fictional task ids.
"""

from __future__ import annotations

import httpx
import pytest

from gdpval_eval.assets import download_task_files
from gdpval_eval.dataset import DatasetDownloadError
from gdpval_eval.models import Task

_URL_A = "https://example.invalid/files/report.docx"
_URL_B = "https://example.invalid/files/data.xlsx"


def _task(
    *,
    deliverable_files=("report.docx", "data.xlsx"),
    deliverable_file_urls=(_URL_A, _URL_B),
) -> Task:
    return Task(
        task_id="task-0001",
        sector="fictional-sector",
        occupation="fictional-occupation",
        prompt="Fictional synthetic prompt for testing.",
        reference_files=(),
        reference_file_urls=(),
        deliverable_files=deliverable_files,
        deliverable_file_urls=deliverable_file_urls,
        rubric=(),
    )


def test_assets_slashed_upstream_names_flatten_to_basename(tmp_path):
    """Upstream names can be relative paths ("deliverable_files/<hash>/x.docx");
    the local file must land flat under dest by base name."""
    body = b"fictional docx bytes"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    task = _task(
        deliverable_files=("deliverable_files/abc123hash/report.docx",),
        deliverable_file_urls=(_URL_A,),
    )
    dest = tmp_path / "out"
    result = download_task_files(
        task, dest, kind="deliverable", transport=httpx.MockTransport(handler)
    )

    assert result == [dest / "report.docx"]
    assert (dest / "report.docx").read_bytes() == body


def test_assets_download_task_files_downloads_two_files(tmp_path):
    calls = []
    bodies = {
        _URL_A: b"fictional docx bytes",
        _URL_B: b"fictional xlsx bytes, a bit longer",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        body = bodies[str(request.url)]
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    transport = httpx.MockTransport(handler)
    dest = tmp_path / "out"

    result = download_task_files(_task(), dest, kind="deliverable", transport=transport)

    assert result == [dest / "report.docx", dest / "data.xlsx"]
    assert (dest / "report.docx").read_bytes() == bodies[_URL_A]
    assert (dest / "data.xlsx").read_bytes() == bodies[_URL_B]
    assert len(calls) == 2
    # no leftover temp files
    assert sorted(p.name for p in dest.iterdir()) == ["data.xlsx", "report.docx"]


def test_assets_download_task_files_content_length_mismatch_raises_and_leaves_no_target(
    tmp_path,
):
    def handler(request: httpx.Request) -> httpx.Response:
        # actual body is shorter than the declared Content-Length
        return httpx.Response(200, content=b"short", headers={"Content-Length": "999"})

    transport = httpx.MockTransport(handler)
    dest = tmp_path / "out"

    with pytest.raises(DatasetDownloadError) as exc_info:
        download_task_files(
            _task(deliverable_files=("report.docx",), deliverable_file_urls=(_URL_A,)),
            dest,
            kind="deliverable",
            transport=transport,
        )

    message = str(exc_info.value)
    assert "report.docx" in message
    target = dest / "report.docx"
    assert not target.exists()
    # no stray files of any kind (including the temp file) remain
    remaining = list(dest.iterdir()) if dest.exists() else []
    assert remaining == []


def test_assets_download_task_files_skips_existing_target_without_calling_transport(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir(parents=True)
    (dest / "report.docx").write_bytes(b"already here")
    # a leftover partial file from a previous interrupted run must not count
    # as "existing" for the *other* file, but here only report.docx is requested
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise AssertionError("transport must not be called for an already-existing file")

    transport = httpx.MockTransport(handler)

    result = download_task_files(
        _task(deliverable_files=("report.docx",), deliverable_file_urls=(_URL_A,)),
        dest,
        kind="deliverable",
        transport=transport,
    )

    assert result == [dest / "report.docx"]
    assert (dest / "report.docx").read_bytes() == b"already here"
    assert calls == []


def test_assets_download_task_files_non_200_raises_without_url_content(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"not found")

    transport = httpx.MockTransport(handler)
    dest = tmp_path / "out"

    with pytest.raises(DatasetDownloadError) as exc_info:
        download_task_files(
            _task(deliverable_files=("report.docx",), deliverable_file_urls=(_URL_A,)),
            dest,
            kind="deliverable",
            transport=transport,
        )

    message = str(exc_info.value)
    assert "404" in message
    assert "report.docx" in message
    assert _URL_A not in message
