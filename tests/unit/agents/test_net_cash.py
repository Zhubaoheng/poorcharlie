"""Tests for investagent.agents.net_cash."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from investagent.agents.base import AgentOutputError
from investagent.agents.net_cash import NetCashAgent
from investagent.llm import LLMClient
from investagent.schemas.company import CompanyIntake
from investagent.schemas.net_cash import AttentionLevel


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


def _net_cash_tool_input(attention_level: str = "NORMAL") -> dict:
    return {
        "net_cash": 1500.0,
        "net_cash_to_market_cap": 0.08,
        "attention_level": attention_level,
        "dividend_profile": {
            "pays_dividend": True,
            "coverage_ratio": 3.5,
        },
        "buyback_profile": {
            "has_buyback": False,
            "shares_reduced": False,
        },
        "cash_quality_notes": [
            "现金质量高，无受限资金（事实）",
            "无海外留存现金问题（事实）",
        ],
    }


@pytest.mark.asyncio
async def test_net_cash_normal():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_net_cash_tool_input("NORMAL"))
    )
    agent = NetCashAgent(llm)
    result = await agent.run(_intake())
    assert result.attention_level == AttentionLevel.NORMAL
    assert result.meta.agent_name == "net_cash"
    assert result.meta.token_usage == 300


@pytest.mark.asyncio
async def test_net_cash_watch():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_net_cash_tool_input("WATCH"))
    )
    agent = NetCashAgent(llm)
    result = await agent.run(_intake())
    assert result.attention_level == AttentionLevel.WATCH


@pytest.mark.asyncio
async def test_net_cash_priority():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_net_cash_tool_input("PRIORITY"))
    )
    agent = NetCashAgent(llm)
    result = await agent.run(_intake())
    assert result.attention_level == AttentionLevel.PRIORITY


@pytest.mark.asyncio
async def test_net_cash_high_priority():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_net_cash_tool_input("HIGH_PRIORITY"))
    )
    agent = NetCashAgent(llm)
    result = await agent.run(_intake())
    assert result.attention_level == AttentionLevel.HIGH_PRIORITY


@pytest.mark.asyncio
async def test_net_cash_meta_is_server_generated():
    """Server-side meta should override anything the LLM emits."""
    tool_input = _net_cash_tool_input()
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
    agent = NetCashAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "net_cash"
    assert result.meta.model_used == "claude-sonnet-4-20250514"
    assert result.meta.token_usage == 300


@pytest.mark.asyncio
async def test_net_cash_no_tool_use_raises():
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
    agent = NetCashAgent(llm)
    with pytest.raises(AgentOutputError, match="no tool_use block"):
        await agent.run(_intake())


@pytest.mark.asyncio
async def test_net_cash_malformed_output_raises():
    """If LLM returns invalid data, raise AgentOutputError."""
    bad_input = {"attention_level": "INVALID_VALUE", "random_field": 42}
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(bad_input)
    )
    agent = NetCashAgent(llm)
    with pytest.raises(AgentOutputError, match="failed to validate"):
        await agent.run(_intake())


@pytest.mark.asyncio
async def test_net_cash_with_ctx():
    """Verify _build_user_context handles ctx properly."""
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_net_cash_tool_input())
    )
    agent = NetCashAgent(llm)

    # ctx with filing data
    mock_ctx = MagicMock()
    mock_ctx.get_result.return_value = {"some": "filing_data"}
    result = await agent.run(_intake(), ctx=mock_ctx)
    assert result.attention_level == AttentionLevel.NORMAL


@pytest.mark.asyncio
async def test_net_cash_with_ctx_no_filing():
    """Verify _build_user_context handles ctx without filing."""
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_net_cash_tool_input())
    )
    agent = NetCashAgent(llm)

    mock_ctx = MagicMock()
    mock_ctx.get_result.side_effect = KeyError("filing")
    result = await agent.run(_intake(), ctx=mock_ctx)
    assert result.attention_level == AttentionLevel.NORMAL
