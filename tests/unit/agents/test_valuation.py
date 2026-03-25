"""Tests for investagent.agents.valuation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from investagent.agents.base import AgentOutputError
from investagent.agents.valuation import ValuationAgent
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


def _valuation_tool_input(meets_hurdle: bool = True) -> dict:
    return {
        "valuation_method": ["正常化盈利收益率", "ROIC再投资模型"],
        "expected_lookthrough_return": {
            "bear": 0.06,
            "base": 0.12,
            "bull": 0.18,
        },
        "friction_adjusted_return": {
            "bear": 0.04,
            "base": 0.10,
            "bull": 0.16,
        },
        "meets_hurdle_rate": meets_hurdle,
        "notes": [
            "正常化盈利基于近5年平均净利润率调整",
            "ROIC维持在30%以上具有可持续性",
        ],
    }


@pytest.mark.asyncio
async def test_valuation_meets_hurdle():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_valuation_tool_input(True))
    )
    agent = ValuationAgent(llm)
    result = await agent.run(_intake())
    assert result.meets_hurdle_rate is True
    assert result.meta.agent_name == "valuation"
    assert result.meta.token_usage == 300


@pytest.mark.asyncio
async def test_valuation_below_hurdle():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_valuation_tool_input(False))
    )
    agent = ValuationAgent(llm)
    result = await agent.run(_intake())
    assert result.meets_hurdle_rate is False


@pytest.mark.asyncio
async def test_valuation_scenario_returns():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_valuation_tool_input())
    )
    agent = ValuationAgent(llm)
    result = await agent.run(_intake())
    assert result.expected_lookthrough_return.bear == 0.06
    assert result.expected_lookthrough_return.base == 0.12
    assert result.expected_lookthrough_return.bull == 0.18
    assert result.friction_adjusted_return.bear == 0.04
    assert result.friction_adjusted_return.base == 0.10
    assert result.friction_adjusted_return.bull == 0.16


@pytest.mark.asyncio
async def test_valuation_methods():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_valuation_tool_input())
    )
    agent = ValuationAgent(llm)
    result = await agent.run(_intake())
    assert len(result.valuation_method) == 2
    assert "正常化盈利收益率" in result.valuation_method


@pytest.mark.asyncio
async def test_valuation_meta_is_server_generated():
    """Server-side meta should override anything the LLM emits."""
    tool_input = _valuation_tool_input()
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
    agent = ValuationAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "valuation"
    assert result.meta.model_used == "claude-sonnet-4-20250514"
    assert result.meta.token_usage == 300


@pytest.mark.asyncio
async def test_valuation_no_tool_use_raises():
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
    agent = ValuationAgent(llm)
    with pytest.raises(AgentOutputError, match="no tool_use block"):
        await agent.run(_intake())


@pytest.mark.asyncio
async def test_valuation_malformed_output_raises():
    """If LLM returns invalid data, raise AgentOutputError."""
    bad_input = {"meets_hurdle_rate": "not_a_bool", "random_field": 42}
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(bad_input)
    )
    agent = ValuationAgent(llm)
    with pytest.raises(AgentOutputError, match="failed to validate"):
        await agent.run(_intake())


@pytest.mark.asyncio
async def test_valuation_with_ctx():
    """Verify _build_user_context handles ctx properly."""
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_valuation_tool_input())
    )
    agent = ValuationAgent(llm)

    mock_ctx = MagicMock()
    mock_ctx.get_result.return_value = {"some": "filing_data"}
    result = await agent.run(_intake(), ctx=mock_ctx)
    assert result.meets_hurdle_rate is True


@pytest.mark.asyncio
async def test_valuation_with_ctx_no_filing():
    """Verify _build_user_context handles ctx without filing."""
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_valuation_tool_input())
    )
    agent = ValuationAgent(llm)

    mock_ctx = MagicMock()
    mock_ctx.get_result.side_effect = KeyError("filing")
    result = await agent.run(_intake(), ctx=mock_ctx)
    assert result.meets_hurdle_rate is True
