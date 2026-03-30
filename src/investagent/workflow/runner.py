"""Agent runner — call agent, validate output, store result."""

from __future__ import annotations

import logging

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.workflow.context import PipelineContext

logger = logging.getLogger(__name__)


async def run_agent(
    agent: BaseAgent,
    input_data: BaseModel,
    ctx: PipelineContext,
) -> BaseAgentOutput:
    """Run a single agent: call, store result in context.

    Also captures the agent's user context (input data) for debug logging.
    """
    # Capture input data for debug log
    try:
        user_context = agent._build_user_context(input_data, ctx)
        ctx.set_data(f"_agent_input_{agent.name}", user_context)
    except Exception:
        logger.debug("Could not capture input for %s", agent.name, exc_info=True)

    result = await agent.run(input_data, ctx)
    ctx.set_result(agent.name, result)
    return result
