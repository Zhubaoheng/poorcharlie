"""Economic Moat Agent."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.mental_models import MoatOutput


class MoatAgent(BaseAgent):
    name: str = "moat"

    async def run(self, input_data: BaseModel) -> MoatOutput:
        raise NotImplementedError
