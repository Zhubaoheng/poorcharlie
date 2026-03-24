"""Financial Quality Agent — score financial health across six dimensions."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.financial_quality import FinancialQualityOutput


class FinancialQualityAgent(BaseAgent):
    name: str = "financial_quality"

    async def run(self, input_data: BaseModel) -> FinancialQualityOutput:
        raise NotImplementedError
