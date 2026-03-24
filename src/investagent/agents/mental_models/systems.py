"""Engineering / Systems Agent."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.mental_models import SystemsOutput


class SystemsAgent(BaseAgent):
    name: str = "systems"

    async def run(self, input_data: BaseModel) -> SystemsOutput:
        raise NotImplementedError
