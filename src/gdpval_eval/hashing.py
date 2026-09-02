"""Content-drift hash for one rubric item (spec §T1).

Secrecy: sha256(criterion + score) is reversible by enumeration against the
public dataset, so the hash is as confidential as the criterion text itself.
Callers must never log or print the return value; it is a drift detector,
not an identity (identity is the upstream rubric_item_id, see models.py).
"""

from __future__ import annotations

import hashlib


def rubric_item_hash(criterion: str, score: int) -> str:
    if not criterion.strip():
        raise ValueError("criterion must be non-empty and not all whitespace")
    if score == 0:
        raise ValueError("score must not be 0")

    preimage = criterion.encode("utf-8") + b"\x1f" + str(score).encode("utf-8")
    return hashlib.sha256(preimage).hexdigest()
