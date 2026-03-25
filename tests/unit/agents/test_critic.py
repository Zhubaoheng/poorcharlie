"""Tests for investagent.agents.critic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from investagent.agents.base import AgentOutputError
from investagent.agents.critic import CriticAgent
from investagent.llm import LLMClient
from investagent.schemas.company import CompanyIntake


def _intake() -> CompanyIntake:
    return CompanyIntake(ticker="600519", name="贵州茅台", exchange="SSE")


def _mock_llm() -> LLMClient:
    return LLMClient(client=MagicMock())


def _mock_response(tool_input: dict) -> MagicMock:
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = tool_input
    response = MagicMock()
    response.content = [tool_block]
    response.model = "claude-sonnet-4-20250514"
    response.usage = MagicMock()
    response.usage.input_tokens = 100
    response.usage.output_tokens = 200
    return response


def _critic_tool_input() -> dict:
    return {
        "kill_shots": ["政府出台全面禁酒令导致行业消亡"],
        "permanent_loss_risks": [
            "食品安全事件导致品牌永久受损",
            "消费税大幅提升侵蚀利润",
        ],
        "moat_destruction_paths": ["年轻一代消费习惯转变，白酒品类衰落"],
        "management_failure_modes": ["国企改革失败，激励机制长期失效导致效率下降"],
        "what_would_make_this_uninvestable": [
            "估值溢价过高时买入，任何负面冲击都会导致戴维斯双杀"
        ],
    }


@pytest.mark.asyncio
async def test_critic_output():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_critic_tool_input())
    )
    agent = CriticAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "critic"
    assert len(result.kill_shots) >= 1
    assert len(result.permanent_loss_risks) >= 1
    assert result.meta.token_usage == 300


@pytest.mark.asyncio
async def test_critic_meta_is_server_generated():
    tool_input = _critic_tool_input()
    tool_input["meta"] = {
        "agent_name": "hacked",
        "timestamp": "2020-01-01T00:00:00Z",
        "model_used": "fake",
        "token_usage": 0,
    }
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(tool_input)
    )
    agent = CriticAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "critic"
    assert result.meta.model_used == "claude-sonnet-4-20250514"


@pytest.mark.asyncio
async def test_critic_no_tool_use_raises():
    text_block = MagicMock()
    text_block.type = "text"
    response = MagicMock()
    response.content = [text_block]
    response.model = "claude-sonnet-4-20250514"
    response.usage = MagicMock()
    response.usage.input_tokens = 50
    response.usage.output_tokens = 100

    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=response)
    agent = CriticAgent(llm)
    with pytest.raises(AgentOutputError, match="no tool_use block"):
        await agent.run(_intake())


@pytest.mark.asyncio
async def test_critic_malformed_output_raises():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response({"invalid": "data"})
    )
    agent = CriticAgent(llm)
    with pytest.raises(AgentOutputError, match="failed to validate"):
        await agent.run(_intake())
