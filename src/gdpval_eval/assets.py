"""Task deliverable/reference file download (spec U6).

Secrecy: only file names (not criterion text or rubric_item_ids) ever
appear in exception messages, and URLs are excluded from error messages
too — the dataset is public but there is no reason to echo transport
detail beyond what identifies the failing file.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from gdpval_eval.dataset import DatasetDownloadError
from gdpval_eval.models import Task

_CHUNK_SIZE = 1024 * 1024

_KIND_FIELDS = {
    "deliverable": ("deliverable_files", "deliverable_file_urls"),
    "reference": ("reference_files", "reference_file_urls"),
}


def download_task_files(
    task: Task,
    dest: Path,
    *,
    kind: str,
    transport: httpx.BaseTransport | None = None,
) -> list[Path]:
    if kind not in _KIND_FIELDS:
        raise ValueError(f"unknown kind: {kind!r}")

    names_attr, urls_attr = _KIND_FIELDS[kind]
    names = getattr(task, names_attr)
    urls = getattr(task, urls_attr)

    dest.mkdir(parents=True, exist_ok=True)

    with httpx.Client(transport=transport) as client:
        return [
            _download_one(client, name, url, dest)
            for name, url in zip(names, urls, strict=True)
        ]


def _download_one(client: httpx.Client, name: str, url: str, dest: Path) -> Path:
    # Upstream file names can be relative paths ("deliverable_files/<hash>/x.xlsx");
    # keep only the base name so files land flat under dest.
    base_name = Path(name).name
    target = dest / base_name
    if target.exists():
        return target

    tmp_path = dest / f".{base_name}.part"
    with client.stream("GET", url, follow_redirects=True) as response:
        if response.status_code != 200:
            raise DatasetDownloadError(
                f"file download failed with HTTP status {response.status_code}: {name}"
            )

        content_length = response.headers.get("content-length")
        bytes_written = 0
        with tmp_path.open("wb") as fh:
            for chunk in response.iter_bytes(_CHUNK_SIZE):
                fh.write(chunk)
                bytes_written += len(chunk)

    if content_length is not None and int(content_length) != bytes_written:
        tmp_path.unlink(missing_ok=True)
        raise DatasetDownloadError(
            f"downloaded size mismatch for {name}: "
            f"expected {content_length} bytes, got {bytes_written}"
        )

    os.replace(tmp_path, target)
    return target
