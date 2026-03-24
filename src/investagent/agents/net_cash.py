"""Net Cash & Capital Return Agent — net cash / market cap analysis."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.net_cash import NetCashOutput


class NetCashAgent(BaseAgent):
    name: str = "net_cash"

    async def run(self, input_data: BaseModel) -> NetCashOutput:
        raise NotImplementedError
