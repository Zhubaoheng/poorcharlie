"""Engineering / Systems Agent — single points of failure, fragility, resilience."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.mental_models import SystemsOutput


class SystemsAgent(BaseAgent):
    name: str = "systems"

    def _output_type(self) -> type[BaseAgentOutput]:
        return SystemsOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Systems Agent in a Munger-style value investing system. "
            "Your role is to evaluate the structural resilience of a company by analyzing "
            "system redundancy, safety margins, single points of failure, and systemic "
            "fragility sources. You assess whether supply chain concentration, financing "
            "dependencies, regulatory exposure, or customer concentration could cause "
            "catastrophic failure. You must clearly distinguish facts from inferences "
            "and flag unknowns."
        )

    def _build_user_context(self, input_data: BaseModel, ctx: Any = None) -> dict[str, Any]:
        assert isinstance(input_data, CompanyIntake)
        result: dict[str, Any] = {
            "ticker": input_data.ticker,
            "name": input_data.name,
            "exchange": input_data.exchange,
        }
        if ctx is not None:
            try:
                ctx.get_result("filing")
                result["has_filing_data"] = True
            except KeyError:
                result["has_filing_data"] = False
        else:
            result["has_filing_data"] = False
        return result
