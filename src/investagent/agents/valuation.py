"""Valuation & Look-through Return Agent — bear/base/bull expected returns."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.valuation import ValuationOutput


class ValuationAgent(BaseAgent):
    name: str = "valuation"

    async def run(self, input_data: BaseModel) -> ValuationOutput:
        raise NotImplementedError
