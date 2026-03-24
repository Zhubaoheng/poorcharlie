"""Investment Committee Agent output schema."""

from __future__ import annotations

from enum import Enum

from investagent.schemas.common import BaseAgentOutput


class FinalLabel(str, Enum):
    REJECT = "REJECT"
    TOO_HARD = "TOO_HARD"
    WATCHLIST = "WATCHLIST"
    DEEP_DIVE = "DEEP_DIVE"
    SPECIAL_SITUATION = "SPECIAL_SITUATION"
    INVESTABLE = "INVESTABLE"


class CommitteeOutput(BaseAgentOutput):
    final_label: FinalLabel
    thesis: str
    anti_thesis: str
    largest_unknowns: list[str]
    expected_return_summary: str
    why_now_or_why_not_now: str
    next_action: str
