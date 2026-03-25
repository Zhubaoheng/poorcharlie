"""Ecology / Evolution Agent — ecological niche, adaptability, survival probability."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.mental_models import EcologyOutput


class EcologyAgent(BaseAgent):
    name: str = "ecology"

    def _output_type(self) -> type[BaseAgentOutput]:
        return EcologyOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Ecology Agent in a Munger-style value investing system. "
            "Your role is to evaluate a company's position within its competitive ecosystem "
            "using an evolutionary lens. You assess the company's ecological niche, whether "
            "its adaptability is strengthening or eroding, whether its performance is "
            "cyclical luck or structural advantage, and its long-term survival probability. "
            "You must clearly distinguish facts from inferences and flag unknowns."
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
