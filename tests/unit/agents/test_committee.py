"""Tests for investagent.agents.committee."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from investagent.agents.base import AgentOutputError
from investagent.agents.committee import CommitteeAgent
from investagent.llm import LLMClient
from investagent.schemas.committee import FinalLabel
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


def _committee_tool_input(label: str = "INVESTABLE") -> dict:
    return {
        "final_label": label,
        "thesis": "茅台拥有中国最强消费品牌，定价权极强，ROIC持续>30%",
        "anti_thesis": "估值溢价过高，增速放缓，政策和人口结构变化构成长期风险",
        "largest_unknowns": ["消费税改革方向", "年轻一代白酒消费趋势"],
        "expected_return_summary": "Base case: 年化回报约10-12%，略高于门槛利率",
        "why_now_or_why_not_now": "当前估值处于历史中位偏高，不具备显著安全边际",
        "next_action": "加入观察清单，等待估值回落至25x PE以下再考虑建仓",
    }


@pytest.mark.asyncio
async def test_committee_investable():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_committee_tool_input("INVESTABLE"))
    )
    agent = CommitteeAgent(llm)
    result = await agent.run(_intake())
    assert result.final_label == FinalLabel.INVESTABLE
    assert result.meta.agent_name == "committee"
    assert result.meta.token_usage == 300


@pytest.mark.asyncio
async def test_committee_reject():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_committee_tool_input("REJECT"))
    )
    agent = CommitteeAgent(llm)
    result = await agent.run(_intake())
    assert result.final_label == FinalLabel.REJECT


@pytest.mark.asyncio
async def test_committee_too_hard():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_committee_tool_input("TOO_HARD"))
    )
    agent = CommitteeAgent(llm)
    result = await agent.run(_intake())
    assert result.final_label == FinalLabel.TOO_HARD


@pytest.mark.asyncio
async def test_committee_watchlist():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_committee_tool_input("WATCHLIST"))
    )
    agent = CommitteeAgent(llm)
    result = await agent.run(_intake())
    assert result.final_label == FinalLabel.WATCHLIST


@pytest.mark.asyncio
async def test_committee_meta_is_server_generated():
    tool_input = _committee_tool_input()
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
    agent = CommitteeAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "committee"
    assert result.meta.model_used == "claude-sonnet-4-20250514"


@pytest.mark.asyncio
async def test_committee_no_tool_use_raises():
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
    agent = CommitteeAgent(llm)
    with pytest.raises(AgentOutputError, match="no tool_use block"):
        await agent.run(_intake())


@pytest.mark.asyncio
async def test_committee_malformed_output_raises():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response({"final_label": "BOGUS"})
    )
    agent = CommitteeAgent(llm)
    with pytest.raises(AgentOutputError, match="failed to validate"):
        await agent.run(_intake())
