"""Economic Moat Agent — industry structure, moat types, pricing power, moat trend."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.mental_models import MoatOutput


class MoatAgent(BaseAgent):
    name: str = "moat"

    def _output_type(self) -> type[BaseAgentOutput]:
        return MoatOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Economic Moat Agent in a Munger-style value investing system. "
            "Your role is to evaluate the competitive advantages (or lack thereof) of a "
            "company by analyzing industry concentration, scale/network/brand/switching-cost/"
            "low-cost advantages, pricing power dynamics, and supplier/customer bargaining "
            "power. You determine whether the company is a price taker or price maker, "
            "identify moat types, and assess whether the moat is strengthening or weakening "
            "over time. You must clearly distinguish facts from inferences and flag unknowns."
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
                serialize_filing_for_prompt,
            )
            filing_data = serialize_filing_for_prompt(ctx)
            result["has_filing_data"] = filing_data.get("has_filing", False)
            result["filing_json"] = format_filing_json(filing_data)
        else:
            result["has_filing_data"] = False
            result["filing_json"] = ""
        return result
