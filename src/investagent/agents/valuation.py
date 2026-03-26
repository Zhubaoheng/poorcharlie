"""Valuation & Look-through Return Agent — bear/base/bull expected returns."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.valuation import ValuationOutput


class ValuationAgent(BaseAgent):
    name: str = "valuation"

    def _output_type(self) -> type[BaseAgentOutput]:
        return ValuationOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Valuation & Look-through Return Agent. Your role is to "
            "estimate the expected look-through return of a company under three "
            "scenarios: bear, base, and bull. You calculate normalized earnings "
            "yield and owner earnings / FCF yield, project per-share intrinsic "
            "value growth based on ROIC reinvestment, and subtract friction "
            "(tax, transaction costs) to produce friction-adjusted returns. "
            "You then compare the base-case return against a hurdle rate "
            "(default 10%) to determine if the investment meets the bar."
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
