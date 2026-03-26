"""Psychology Agent — management incentives, market sentiment, narrative divergence."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.mental_models import PsychologyOutput


class PsychologyAgent(BaseAgent):
    name: str = "psychology"

    def _output_type(self) -> type[BaseAgentOutput]:
        return PsychologyOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Psychology Agent in a Munger-style value investing system. "
            "Your role is to identify behavioral and psychological biases that may distort "
            "the investment picture. You evaluate whether management incentives are misaligned "
            "with shareholder interests, whether market sentiment is driven by short-term "
            "narratives, herd behavior, or panic, and whether investors are confusing "
            "familiarity with safety. You must clearly distinguish facts from inferences "
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
