"""Agent runner — call agent, validate output, store result."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.workflow.context import PipelineContext


async def run_agent(
    agent: BaseAgent,
    input_data: BaseModel,
    ctx: PipelineContext,
) -> BaseAgentOutput:
    """Run a single agent: inject soul prompt, call, validate, store."""
    raise NotImplementedError
