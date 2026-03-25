"""Agent runner — call agent, validate output, store result."""

from __future__ import annotations

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.workflow.context import PipelineContext


async def run_agent(
    agent: BaseAgent,
    input_data: BaseModel,
    ctx: PipelineContext,
) -> BaseAgentOutput:
    """Run a single agent: call, store result in context."""
    result = await agent.run(input_data, ctx)
    ctx.set_result(agent.name, result)
    return result
