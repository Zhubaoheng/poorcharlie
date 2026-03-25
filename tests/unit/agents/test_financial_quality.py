"""Tests for investagent.agents.financial_quality."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from investagent.agents.base import AgentOutputError
from investagent.agents.financial_quality import FinancialQualityAgent
from investagent.llm import LLMClient
from investagent.schemas.company import CompanyIntake


def _intake() -> CompanyIntake:
    return CompanyIntake(
        ticker="600519",
        name="贵州茅台",
        exchange="SSE",
    )


def _mock_llm() -> LLMClient:
    return LLMClient(client=MagicMock())


def _mock_response(tool_input: dict) -> MagicMock:
    """Create a mock Anthropic Message with a tool_use block."""
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


def _quality_tool_input(pass_standard: bool = True) -> dict:
    return {
        "pass_minimum_standard": pass_standard,
        "scores": {
            "per_share_growth": 9,
            "return_on_capital": 9,
            "cash_conversion": 8,
            "leverage_safety": 10,
            "capital_allocation": 8,
            "moat_financial_trace": 9,
        },
        "key_strengths": [
            "ROIC 持续超过 30%，远高于资本成本",
            "净现金状态，无有息负债",
        ],
        "key_failures": [],
        "should_continue": "财务质量达标，继续分析",
    }


@pytest.mark.asyncio
async def test_financial_quality_pass():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_quality_tool_input(True))
    )
    agent = FinancialQualityAgent(llm)
    result = await agent.run(_intake())
    assert result.pass_minimum_standard is True
    assert result.scores.per_share_growth == 9
    assert result.scores.return_on_capital == 9
    assert result.scores.cash_conversion == 8
    assert result.scores.leverage_safety == 10
    assert result.scores.capital_allocation == 8
    assert result.scores.moat_financial_trace == 9
    assert len(result.key_strengths) == 2
    assert result.key_failures == []
    assert result.meta.agent_name == "financial_quality"
    assert result.meta.token_usage == 300


@pytest.mark.asyncio
async def test_financial_quality_fail():
    llm = _mock_llm()
    tool_input = _quality_tool_input(False)
    tool_input["scores"] = {
        "per_share_growth": 3,
        "return_on_capital": 4,
        "cash_conversion": 2,
        "leverage_safety": 3,
        "capital_allocation": 4,
        "moat_financial_trace": 2,
    }
    tool_input["key_strengths"] = []
    tool_input["key_failures"] = [
        "现金转换极差，CFO/NI 长期低于 0.5",
        "无任何护城河财务特征，利润率持续下滑",
    ]
    tool_input["should_continue"] = "财务质量不达标，建议停止，原因：多项指标严重不达标"
    llm.create_message = AsyncMock(
        return_value=_mock_response(tool_input)
    )
    agent = FinancialQualityAgent(llm)
    result = await agent.run(_intake())
    assert result.pass_minimum_standard is False
    assert result.scores.cash_conversion == 2
    assert len(result.key_failures) == 2


@pytest.mark.asyncio
async def test_financial_quality_scores_frozen():
    """FinancialQualityScores should be immutable."""
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_quality_tool_input(True))
    )
    agent = FinancialQualityAgent(llm)
    result = await agent.run(_intake())
    with pytest.raises(Exception):
        result.scores.per_share_growth = 1  # type: ignore[misc]


@pytest.mark.asyncio
async def test_financial_quality_meta_is_server_generated():
    """Server-side meta should override anything the LLM emits."""
    tool_input = _quality_tool_input()
    # Simulate LLM sneaking in a meta (should be overwritten)
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
    agent = FinancialQualityAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "financial_quality"
    assert result.meta.model_used == "claude-sonnet-4-20250514"
    assert result.meta.token_usage == 300


@pytest.mark.asyncio
async def test_financial_quality_no_tool_use_raises():
    """If LLM returns no tool_use block, raise AgentOutputError."""
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
    agent = FinancialQualityAgent(llm)
    with pytest.raises(AgentOutputError, match="no tool_use block"):
        await agent.run(_intake())


@pytest.mark.asyncio
async def test_financial_quality_malformed_output_raises():
    """If LLM returns invalid data, raise AgentOutputError."""
    bad_input = {"pass_minimum_standard": "not_a_bool", "random_field": 42}
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(bad_input)
    )
    agent = FinancialQualityAgent(llm)
    with pytest.raises(AgentOutputError, match="failed to validate"):
        await agent.run(_intake())
