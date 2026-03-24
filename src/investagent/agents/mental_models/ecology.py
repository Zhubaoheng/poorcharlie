"""Ecology / Evolution Agent."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.mental_models import EcologyOutput


class EcologyAgent(BaseAgent):
    name: str = "ecology"

    async def run(self, input_data: BaseModel) -> EcologyOutput:
        raise NotImplementedError
