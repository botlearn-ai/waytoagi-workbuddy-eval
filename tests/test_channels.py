"""Tests for assign_channel (spec T4).

All criterion fixtures below are synthetic sentences invented for this test
file; none originate from the real GDPval dataset (see AGENTS.md secrecy
rules).
"""

from __future__ import annotations

import pytest

from gdpval_eval.channels import assign_channel
from gdpval_eval.models import Channel, ChannelReason


def test_channels_vision_chart_word_still_routes_to_vision():
    # Mixed case on purpose: matching must be case-insensitive. The word
    # "chart" appears, but the sentence is about a *visual* attribute of the
    # chart (axis labels / font legibility), so VISION must win even though
    # a later DETERMINISTIC keyword check would also see "chart".
    criterion = "CHART axis LABELS use a Legible FONT size."

    decision = assign_channel(criterion)

    assert decision.channel is Channel.VISION
    assert decision.reason is ChannelReason.LAYOUT_KEYWORD


def test_channels_vision_slide_design_and_alignment():
    criterion = "Slide design keeps text alignment consistent across bullets."

    decision = assign_channel(criterion)

    assert decision.channel is Channel.VISION
    assert decision.reason is ChannelReason.LAYOUT_KEYWORD


def test_channels_deterministic_page_count():
    criterion = "The exported PDF has a page count of 12."

    decision = assign_channel(criterion)

    assert decision.channel is Channel.DETERMINISTIC
    assert decision.reason is ChannelReason.DETERMINISTIC_KEYWORD


def test_channels_deterministic_worksheet_named_and_formula():
    criterion = "The workbook includes a worksheet named Summary with a SUM formula."

    decision = assign_channel(criterion)

    assert decision.channel is Channel.DETERMINISTIC
    assert decision.reason is ChannelReason.DETERMINISTIC_KEYWORD


def test_channels_default_text():
    criterion = "The response directly answers the client's question about pricing."

    decision = assign_channel(criterion)

    assert decision.channel is Channel.TEXT
    assert decision.reason is ChannelReason.DEFAULT


def test_channels_blank_criterion_raises_value_error():
    for blank in ("", "   ", "\n\t "):
        with pytest.raises(ValueError):
            assign_channel(blank)
