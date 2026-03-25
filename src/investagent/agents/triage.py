"""Triage Agent — gate: is the company explainable from public info?"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.triage import TriageOutput


class TriageAgent(BaseAgent):
    name: str = "triage"

    def _output_type(self) -> type[BaseAgentOutput]:
        return TriageOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Triage Agent. Your role is to evaluate whether a company "
            "can be meaningfully analyzed using publicly available information. "
            "You assess explainability across four dimensions: business model clarity, "
            "competition structure visibility, financial mapping fidelity, and key driver "
            "identifiability. You default to skepticism — only companies with clear, "
            "explainable businesses should pass through to deep analysis."
        )

    def _build_user_context(self, input_data: BaseModel, ctx: Any = None) -> dict[str, Any]:
        assert isinstance(input_data, CompanyIntake)
        return {
            "ticker": input_data.ticker,
            "name": input_data.name,
            "exchange": input_data.exchange,
            "sector": input_data.sector,
            "notes": input_data.notes,
        }
