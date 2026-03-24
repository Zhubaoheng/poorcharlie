"""Critic Agent — adversarial: find kill shots and permanent loss risks."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.critic import CriticOutput


class CriticAgent(BaseAgent):
    name: str = "critic"

    async def run(self, input_data: BaseModel) -> CriticOutput:
        raise NotImplementedError
