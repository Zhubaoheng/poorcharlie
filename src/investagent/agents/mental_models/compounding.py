"""Math / Compounding Agent."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.mental_models import CompoundingOutput


class CompoundingAgent(BaseAgent):
    name: str = "compounding"

    async def run(self, input_data: BaseModel) -> CompoundingOutput:
        raise NotImplementedError
