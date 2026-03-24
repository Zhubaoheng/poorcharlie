"""Filing Structuring Skill — standardize financials into structured tables."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.filing import FilingOutput


class FilingAgent(BaseAgent):
    name: str = "filing"

    async def run(self, input_data: BaseModel) -> FilingOutput:
        raise NotImplementedError
