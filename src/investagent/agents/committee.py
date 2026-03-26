"""Investment Committee Agent — final verdict."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.committee import CommitteeOutput


class CommitteeAgent(BaseAgent):
    name: str = "committee"

    def _output_type(self) -> type[BaseAgentOutput]:
        return CommitteeOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Investment Committee Agent — the final synthesis layer "
            "that renders a verdict on the investment case. You do NOT re-analyze "
            "raw data. You consume only the structured outputs from all prior "
            "agents in the pipeline and synthesize them into a single actionable "
            "conclusion. You weigh the bull case against the bear case, identify "
            "the largest remaining unknowns, and assign one of six labels: "
            "REJECT, TOO_HARD, WATCHLIST, DEEP_DIVE, SPECIAL_SITUATION, or "
            "INVESTABLE. Your output must include a clear thesis, anti-thesis, "
            "expected return summary, timing rationale, and next action."
        )

    def _build_user_context(self, input_data: BaseModel, ctx: Any = None) -> dict[str, Any]:
        assert isinstance(input_data, CompanyIntake)
        result: dict[str, Any] = {
            "ticker": input_data.ticker,
            "name": input_data.name,
            "exchange": input_data.exchange,
        }
        if ctx is not None:
            from investagent.agents.context_helpers import (
                format_filing_json,
                serialize_upstream_for_committee,
            )
            upstream = serialize_upstream_for_committee(ctx)
            result["has_filing_data"] = bool(upstream)
            result["upstream_json"] = format_filing_json(upstream, max_chars=40000)
        else:
            result["has_filing_data"] = False
            result["upstream_json"] = ""
        return result
