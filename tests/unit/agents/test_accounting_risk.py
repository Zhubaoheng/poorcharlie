"""Tests for investagent.agents.accounting_risk."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from investagent.agents.base import AgentOutputError
from investagent.agents.accounting_risk import AccountingRiskAgent
from investagent.llm import LLMClient
from investagent.schemas.company import CompanyIntake
from investagent.schemas.accounting_risk import RiskLevel


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


def _accounting_risk_tool_input(risk_level: str = "GREEN") -> dict:
    return {
        "risk_level": risk_level,
        "major_accounting_changes": [],
        "comparability_impact": "会计政策保持一致，可比性良好",
        "credibility_concern": "未发现管理层操纵迹象，财务数据可信度高",
        "stop_or_continue": "继续分析",
    }


@pytest.mark.asyncio
async def test_accounting_risk_green():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_accounting_risk_tool_input("GREEN"))
    )
    agent = AccountingRiskAgent(llm)
    result = await agent.run(_intake())
    assert result.risk_level == RiskLevel.GREEN
    assert result.major_accounting_changes == []
    assert result.meta.agent_name == "accounting_risk"
    assert result.meta.token_usage == 300


@pytest.mark.asyncio
async def test_accounting_risk_yellow():
    llm = _mock_llm()
    tool_input = _accounting_risk_tool_input("YELLOW")
    tool_input["major_accounting_changes"] = [
        "2023年变更了收入确认政策，从完工百分比法改为时点法"
    ]
    tool_input["comparability_impact"] = "收入确认变更导致2023年收入口径与前期不完全可比，但影响可量化"
    tool_input["stop_or_continue"] = "继续分析，但需在估值时调整收入可比性"
    llm.create_message = AsyncMock(
        return_value=_mock_response(tool_input)
    )
    agent = AccountingRiskAgent(llm)
    result = await agent.run(_intake())
    assert result.risk_level == RiskLevel.YELLOW
    assert len(result.major_accounting_changes) == 1


@pytest.mark.asyncio
async def test_accounting_risk_red():
    llm = _mock_llm()
    tool_input = _accounting_risk_tool_input("RED")
    tool_input["major_accounting_changes"] = [
        "连续两年对历史财务数据进行重述",
        "审计意见从标准无保留变为保留意见",
    ]
    tool_input["credibility_concern"] = "频繁重述叠加审计意见恶化，财务数据可信度严重受损"
    tool_input["stop_or_continue"] = "建议停止，原因：财务数据可信度不足以支撑有意义的分析"
    llm.create_message = AsyncMock(
        return_value=_mock_response(tool_input)
    )
    agent = AccountingRiskAgent(llm)
    result = await agent.run(_intake())
    assert result.risk_level == RiskLevel.RED
    assert len(result.major_accounting_changes) == 2


@pytest.mark.asyncio
async def test_accounting_risk_meta_is_server_generated():
    """Server-side meta should override anything the LLM emits."""
    tool_input = _accounting_risk_tool_input()
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
    agent = AccountingRiskAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "accounting_risk"
    assert result.meta.model_used == "claude-sonnet-4-20250514"
    assert result.meta.token_usage == 300


@pytest.mark.asyncio
async def test_accounting_risk_no_tool_use_raises():
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
    agent = AccountingRiskAgent(llm)
    with pytest.raises(AgentOutputError, match="no tool_use block"):
        await agent.run(_intake())


@pytest.mark.asyncio
async def test_accounting_risk_malformed_output_raises():
    """If LLM returns invalid data, raise AgentOutputError."""
    bad_input = {"risk_level": "INVALID_VALUE", "random_field": 42}
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(bad_input)
    )
    agent = AccountingRiskAgent(llm)
    with pytest.raises(AgentOutputError, match="failed to validate"):
        await agent.run(_intake())
