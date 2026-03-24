"""BaseAgent ABC — all agents inherit from this."""

from abc import ABC, abstractmethod

from pydantic import BaseModel

from investagent.schemas.common import BaseAgentOutput


class BaseAgent(ABC):
    """Abstract base for all pipeline agents.

    Subclasses must implement ``run`` which takes a Pydantic input model
    and returns a validated agent output.
    """

    name: str = "base"

    @abstractmethod
    async def run(self, input_data: BaseModel) -> BaseAgentOutput:
        raise NotImplementedError
