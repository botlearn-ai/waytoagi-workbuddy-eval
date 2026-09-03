"""Append-only verdict store with write-once semantics (spec U4). Blind-test unit.

Records live under a gitignored root (runs/ or data/): they carry the raw
model response, which may quote criterion text. File names carry only
product and attempt — never task_id.
"""

from __future__ import annotations

import fcntl
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from gdpval_eval.models import GdpvalEvalError, ItemState


class DuplicateVerdictError(GdpvalEvalError):
    """A resolved verdict already exists for this key."""


@dataclass(frozen=True)
class VerdictRecord:
    product: str
    task_id: str
    rubric_item_id: str
    attempt: int
    exam_version: str
    channel: str
    state: ItemState | None
    reason: str | None
    raw: str
    cost: float
    served_model: str | None
    served_provider: str | None
    judged_at: str


class VerdictStore:
    def __init__(self, root: Path) -> None:
        root = Path(root)
        if not {"runs", "data"} & set(root.parts):
            raise ValueError(
                "store root must contain a path segment named 'runs' or 'data'"
            )
        self._root = root

    def _path(self, product: str, attempt: int, exam_version: str) -> Path:
        return self._root / exam_version / f"{product}_{attempt}.jsonl"

    @staticmethod
    def _to_line(record: VerdictRecord) -> str:
        data = asdict(record)
        data["state"] = record.state.value if record.state is not None else None
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def _from_dict(data: dict) -> VerdictRecord:
        data = dict(data)
        state = data.get("state")
        data["state"] = ItemState(state) if state is not None else None
        return VerdictRecord(**data)

    @staticmethod
    def _read_records(fh) -> list[VerdictRecord]:
        fh.seek(0)
        records: list[VerdictRecord] = []
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                records.append(VerdictStore._from_dict(data))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return records

    @staticmethod
    def _latest_by_key(
        records: list[VerdictRecord],
    ) -> dict[tuple[str, str], VerdictRecord]:
        latest: dict[tuple[str, str], VerdictRecord] = {}
        for rec in records:
            latest[(rec.task_id, rec.rubric_item_id)] = rec
        return latest

    def write(self, record: VerdictRecord) -> None:
        path = self._path(record.product, record.attempt, record.exam_version)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+", encoding="utf-8") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise GdpvalEvalError(
                    "could not acquire exclusive lock on verdict store file"
                ) from exc
            try:
                latest = self._latest_by_key(self._read_records(fh))
                existing = latest.get((record.task_id, record.rubric_item_id))
                if existing is not None and existing.state is not None:
                    raise DuplicateVerdictError(
                        "a resolved verdict already exists for this key"
                    )
                fh.seek(0, 2)
                fh.write(self._to_line(record) + "\n")
                fh.flush()
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _load_latest(
        self, product: str, attempt: int, exam_version: str
    ) -> list[VerdictRecord]:
        path = self._path(product, attempt, exam_version)
        if not path.exists():
            return []
        with open(path, encoding="utf-8") as fh:
            records = self._read_records(fh)
        return list(self._latest_by_key(records).values())

    def resolved_ids(self, product: str, attempt: int, exam_version: str) -> set[str]:
        return {
            rec.rubric_item_id
            for rec in self._load_latest(product, attempt, exam_version)
            if rec.state is not None
        }

    def load(self, product: str, attempt: int, exam_version: str) -> list[VerdictRecord]:
        return self._load_latest(product, attempt, exam_version)

    def pending(self, product: str, attempt: int, exam_version: str) -> list[str]:
        return [
            rec.rubric_item_id
            for rec in self._load_latest(product, attempt, exam_version)
            if rec.state is None
        ]
