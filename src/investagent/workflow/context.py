"""PipelineContext — carries structured outputs between pipeline stages."""

from __future__ import annotations

from pydantic import BaseModel

from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake


class PipelineContext:
    """Central data bus for the pipeline.

    Each agent writes its output here; downstream agents read from here.
    """

    def __init__(self, intake: CompanyIntake) -> None:
        self.intake = intake
        self._results: dict[str, BaseAgentOutput] = {}
        self.stopped: bool = False
        self.stop_reason: str | None = None

    def set_result(self, agent_name: str, output: BaseAgentOutput) -> None:
        raise NotImplementedError

    def get_result(self, agent_name: str) -> BaseAgentOutput:
        raise NotImplementedError

    def is_stopped(self) -> bool:
        raise NotImplementedError
