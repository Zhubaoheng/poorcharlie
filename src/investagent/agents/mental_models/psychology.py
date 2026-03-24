"""Psychology Agent."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.mental_models import PsychologyOutput


class PsychologyAgent(BaseAgent):
    name: str = "psychology"

    async def run(self, input_data: BaseModel) -> PsychologyOutput:
        raise NotImplementedError
