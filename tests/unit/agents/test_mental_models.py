"""Tests for the 5 mental model agents."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from investagent.agents.base import AgentOutputError
from investagent.agents.mental_models.moat import MoatAgent
from investagent.agents.mental_models.compounding import CompoundingAgent
from investagent.agents.mental_models.psychology import PsychologyAgent
from investagent.agents.mental_models.systems import SystemsAgent
from investagent.agents.mental_models.ecology import EcologyAgent
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


def _no_tool_use_response() -> MagicMock:
    text_block = MagicMock()
    text_block.type = "text"
    response = MagicMock()
    response.content = [text_block]
    response.model = "claude-sonnet-4-20250514"
    response.usage = MagicMock()
    response.usage.input_tokens = 50
    response.usage.output_tokens = 100
    return response


# ── Moat Agent ──────────────────────────────────────────────


def _moat_tool_input() -> dict:
    return {
        "industry_structure": "白酒行业高度集中，CR5超过60%",
        "moat_type": ["品牌效应", "定价权"],
        "pricing_power_position": "强定价权，茅台出厂价持续提升且供不应求",
        "moat_trend": "strengthening — 品牌心智不断强化",
    }


@pytest.mark.asyncio
async def test_moat_output():
    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=_mock_response(_moat_tool_input()))
    agent = MoatAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "moat"
    assert "品牌效应" in result.moat_type
    assert result.meta.token_usage == 300


@pytest.mark.asyncio
async def test_moat_no_tool_use_raises():
    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=_no_tool_use_response())
    agent = MoatAgent(llm)
    with pytest.raises(AgentOutputError, match="no tool_use block"):
        await agent.run(_intake())


# ── Compounding Agent ───────────────────────────────────────


def _compounding_tool_input() -> dict:
    return {
        "compounding_engine": "高ROIC+低资本再投入，通过提价驱动每股价值增长",
        "incremental_return_on_capital": "增量资本回报率>40%",
        "sustainability_period": "10年以上，品牌壁垒极深",
        "per_share_value_growth_logic": "产量稳定+持续提价=每股利润复合增长12-15%",
    }


@pytest.mark.asyncio
async def test_compounding_output():
    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=_mock_response(_compounding_tool_input()))
    agent = CompoundingAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "compounding"
    assert "ROIC" in result.compounding_engine


# ── Psychology Agent ────────────────────────────────────────


def _psychology_tool_input() -> dict:
    return {
        "management_incentive_distortion": "国企管理层薪酬与市值脱钩，存在保守经营倾向",
        "market_sentiment_bias": "白酒板块易受消费情绪和政策预期驱动",
        "narrative_vs_fact_divergence": "市场将茅台视为永续增长资产，但产能天花板客观存在",
    }


@pytest.mark.asyncio
async def test_psychology_output():
    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=_mock_response(_psychology_tool_input()))
    agent = PsychologyAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "psychology"
    assert "国企" in result.management_incentive_distortion


# ── Systems Agent ───────────────────────────────────────────


def _systems_tool_input() -> dict:
    return {
        "single_points_of_failure": ["产地依赖赤水河流域", "品牌声誉单点风险"],
        "fragility_sources": ["政策风险（限酒令/消费税）"],
        "fault_tolerance": "财务上极度稳健，无有息负债",
        "system_resilience": "高——业务模式简单，供应链短，现金流充沛",
    }


@pytest.mark.asyncio
async def test_systems_output():
    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=_mock_response(_systems_tool_input()))
    agent = SystemsAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "systems"
    assert len(result.single_points_of_failure) == 2


# ── Ecology Agent ───────────────────────────────────────────


def _ecology_tool_input() -> dict:
    return {
        "ecological_niche": "超高端白酒品类的绝对统治者",
        "adaptability_trend": "稳定——品类本身不需要频繁适应变化",
        "cyclical_vs_structural": "结构性优势为主，周期性波动影响有限",
        "long_term_survival_probability": "极高——品牌价值和文化属性构成长期护城河",
    }


@pytest.mark.asyncio
async def test_ecology_output():
    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=_mock_response(_ecology_tool_input()))
    agent = EcologyAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "ecology"
    assert "超高端" in result.ecological_niche


@pytest.mark.asyncio
async def test_ecology_malformed_raises():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response({"bad_field": 42})
    )
    agent = EcologyAgent(llm)
    with pytest.raises(AgentOutputError, match="failed to validate"):
        await agent.run(_intake())
