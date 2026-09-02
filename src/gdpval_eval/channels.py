"""Judging channel coarse assignment from criterion keywords (spec T4).

This is a coarse, keyword-based pass meant for human review afterwards, not
a precise classifier. Order matters: VISION is checked before
DETERMINISTIC so that a criterion about a chart's *visual* attributes
(e.g. "chart axis labels are legible") lands on VISION even though it also
contains the word "chart", which would otherwise read as a DETERMINISTIC
existence check ("contains a chart").
"""

from __future__ import annotations

from gdpval_eval.models import Channel, ChannelDecision, ChannelReason

_VISION_KEYWORDS: tuple[str, ...] = (
    "axis label",
    "text overflow",
    "font",
    "alignment",
    "aligned",
    "layout",
    "legible",
    "readable",
    "color",
    "slide design",
    "visual",
    "formatting",
)

_DETERMINISTIC_KEYWORDS: tuple[str, ...] = (
    "page count",
    "number of pages",
    "worksheet named",
    "tab named",
    "formula",
    "file format",
    "file opens",
    "contains a chart",
    "includes a chart",
)


def assign_channel(criterion: str) -> ChannelDecision:
    if not criterion.strip():
        raise ValueError("criterion must not be empty or whitespace-only")

    lowered = criterion.lower()

    if any(keyword in lowered for keyword in _VISION_KEYWORDS):
        return ChannelDecision(channel=Channel.VISION, reason=ChannelReason.LAYOUT_KEYWORD)

    if any(keyword in lowered for keyword in _DETERMINISTIC_KEYWORDS):
        return ChannelDecision(
            channel=Channel.DETERMINISTIC, reason=ChannelReason.DETERMINISTIC_KEYWORD
        )

    return ChannelDecision(channel=Channel.TEXT, reason=ChannelReason.DEFAULT)
