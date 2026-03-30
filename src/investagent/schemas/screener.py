"""Pre-screening Agent output schema."""

from __future__ import annotations

from investagent.schemas.common import BaseAgentOutput


class ScreenerOutput(BaseAgentOutput):
    decision: str  # "SKIP" | "PROCEED" | "SPECIAL_CASE"
    reason: str
    industry_context: str = ""
