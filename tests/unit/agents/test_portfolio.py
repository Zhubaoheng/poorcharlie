"""Tests for investagent.agents.portfolio."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from investagent.agents.portfolio import (
    CandidateInfo,
    HoldingInfo,
    PortfolioAgent,
    PortfolioInput,
)
from investagent.llm import LLMClient


def _mock_llm() -> LLMClient:
    return LLMClient(client=MagicMock())


def _mock_response(
    allocations: list[dict],
    cash_weight: float,
    industry_distribution: dict | None = None,
    rebalance_actions: list[str] | None = None,
) -> MagicMock:
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = {
        "allocations": allocations,
        "cash_weight": cash_weight,
        "industry_distribution": industry_distribution or {},
        "rebalance_actions": rebalance_actions or [],
    }
    resp = MagicMock()
    resp.content = [tool_block]
    resp.model = "test-model"
    resp.usage = MagicMock(input_tokens=200, output_tokens=100)
    return resp


def _sample_candidates() -> list[CandidateInfo]:
    return [
        CandidateInfo(
            ticker="600519", name="贵州茅台", industry="食品饮料",
            enterprise_quality="GREAT", price_vs_value="FAIR",
            margin_of_safety_pct=0.15, meets_hurdle_rate=True,
            thesis="高ROE消费品龙头，护城河深厚",
        ),
        CandidateInfo(
            ticker="000858", name="五粮液", industry="食品饮料",
            enterprise_quality="GREAT", price_vs_value="CHEAP",
            margin_of_safety_pct=0.30, meets_hurdle_rate=True,
            thesis="白酒行业第二，估值有吸引力",
        ),
        CandidateInfo(
            ticker="000333", name="美的集团", industry="家用电器",
            enterprise_quality="GREAT", price_vs_value="FAIR",
            margin_of_safety_pct=0.10, meets_hurdle_rate=True,
            thesis="家电龙头，海外扩张顺利",
        ),
    ]


class TestPortfolioAgent:
    def test_instantiation(self):
        agent = PortfolioAgent(llm=_mock_llm())
        assert agent.name == "portfolio"

    def test_build_context_with_candidates(self):
        agent = PortfolioAgent(llm=_mock_llm())
        inp = PortfolioInput(
            candidates=_sample_candidates(),
            available_cash_pct=1.0,
        )
        ctx = agent._build_user_context(inp)
        assert len(ctx["candidates"]) == 3
        assert ctx["candidates"][0]["ticker"] == "600519"
        assert ctx["candidates"][0]["enterprise_quality"] == "GREAT"
        assert ctx["available_cash_pct"] == "100%"

    def test_build_context_with_holdings(self):
        agent = PortfolioAgent(llm=_mock_llm())
        inp = PortfolioInput(
            candidates=[],
            current_holdings=[
                HoldingInfo(ticker="600519", name="贵州茅台", weight=0.25, industry="食品饮料"),
            ],
            available_cash_pct=0.75,
        )
        ctx = agent._build_user_context(inp)
        assert len(ctx["current_holdings"]) == 1
        assert ctx["current_holdings"][0]["weight"] == "25%"
        assert ctx["available_cash_pct"] == "75%"

    def test_build_context_empty(self):
        agent = PortfolioAgent(llm=_mock_llm())
        inp = PortfolioInput()
        ctx = agent._build_user_context(inp)
        assert ctx["candidates"] == []
        assert ctx["current_holdings"] == []

    @pytest.mark.asyncio
    async def test_run_builds_portfolio(self):
        llm = _mock_llm()
        llm.create_message = AsyncMock(return_value=_mock_response(
            allocations=[
                {"ticker": "600519", "name": "贵州茅台", "target_weight": 0.25, "reason": "GREAT+FAIR"},
                {"ticker": "000333", "name": "美的集团", "target_weight": 0.15, "reason": "GREAT+FAIR行业分散"},
            ],
            cash_weight=0.60,
            industry_distribution={"食品饮料": 0.25, "家用电器": 0.15},
            rebalance_actions=["买入 600519 贵州茅台 25%", "买入 000333 美的集团 15%"],
        ))

        agent = PortfolioAgent(llm=llm)
        inp = PortfolioInput(candidates=_sample_candidates())
        result = await agent.run(inp)

        assert len(result.allocations) == 2
        assert result.allocations[0].ticker == "600519"
        assert result.allocations[0].target_weight == 0.25
        assert result.cash_weight == 0.60
        assert len(result.rebalance_actions) == 2

    @pytest.mark.asyncio
    async def test_run_all_cash(self):
        llm = _mock_llm()
        llm.create_message = AsyncMock(return_value=_mock_response(
            allocations=[],
            cash_weight=1.0,
            rebalance_actions=["无合适标的，全部持有现金"],
        ))

        agent = PortfolioAgent(llm=llm)
        inp = PortfolioInput(candidates=[])
        result = await agent.run(inp)

        assert len(result.allocations) == 0
        assert result.cash_weight == 1.0
