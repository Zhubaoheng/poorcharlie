"""Triage Agent — gate: is the company explainable from public info?"""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.triage import TriageOutput


class TriageAgent(BaseAgent):
    name: str = "triage"

    async def run(self, input_data: BaseModel) -> TriageOutput:
        raise NotImplementedError
