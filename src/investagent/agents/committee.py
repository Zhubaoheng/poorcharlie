"""Investment Committee Agent — final verdict."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.committee import CommitteeOutput


class CommitteeAgent(BaseAgent):
    name: str = "committee"

    async def run(self, input_data: BaseModel) -> CommitteeOutput:
        raise NotImplementedError
