"""Accounting Risk Agent — detect accounting method changes."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.accounting_risk import AccountingRiskOutput


class AccountingRiskAgent(BaseAgent):
    name: str = "accounting_risk"

    async def run(self, input_data: BaseModel) -> AccountingRiskOutput:
        raise NotImplementedError
