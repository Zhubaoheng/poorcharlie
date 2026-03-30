"""Tests for investagent.screening.screener."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from investagent.llm import LLMClient
from investagent.screening.screener import ScreenerAgent, ScreenerInput, _fmt_ratio_list


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_llm() -> LLMClient:
    return LLMClient(client=MagicMock())


def _mock_response(decision: str, reason: str, industry_context: str = "") -> MagicMock:
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = {
        "decision": decision,
        "reason": reason,
        "industry_context": industry_context,
    }
    resp = MagicMock()
    resp.content = [tool_block]
    resp.model = "test-model"
    resp.usage = MagicMock(input_tokens=100, output_tokens=50)
    return resp


def _sample_input() -> ScreenerInput:
    return ScreenerInput(
        ticker="600519",
        name="贵州茅台",
        industry="食品饮料",
        main_business="酱香型白酒生产与销售",
        listing_date="2001-08-27",
        market_cap="2.1万亿",
        ratios={
            "fiscal_years": ["2019", "2020", "2021"],
            "roe": [0.328, 0.317, 0.306],
            "roic": [0.28, 0.27, 0.26],
            "gross_margin": [0.909, 0.910, 0.913],
            "net_margin": [0.466, 0.495, 0.477],
            "revenue_growth": [None, 0.0795, 0.1474],
            "net_income_growth": [None, 0.1463, 0.1063],
            "eps_growth": [None, 0.133, 0.123],
            "ocf_to_ni": [1.098, 1.064, 1.058],
            "fcf_to_ni": [1.024, 0.989, 0.981],
            "capex_to_revenue": [0.034, 0.037, 0.037],
            "debt_to_assets": [0.219, 0.221, 0.227],
            "net_debt_to_ebit": [-1.525, -1.719, -1.757],
            "interest_coverage": [None, None, None],
            "pe_ttm": [None, None, None],
            "pb": [None, None, None],
            "dividend_yield": [None, None, None],
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFmtRatioList:
    def test_percentages(self):
        result = _fmt_ratio_list([0.328, 0.317, 0.306])
        assert "32.80%" in result
        assert "→" in result

    def test_none_values(self):
        result = _fmt_ratio_list([None, 0.1, None])
        assert "N/A" in result
        assert "10.00%" in result

    def test_large_values(self):
        result = _fmt_ratio_list([15.5, 20.3])
        assert "15.5" in result

    def test_empty(self):
        assert _fmt_ratio_list([]) == ""


class TestScreenerAgent:
    def test_instantiation(self):
        agent = ScreenerAgent(llm=_mock_llm())
        assert agent.name == "screener"

    def test_build_user_context(self):
        agent = ScreenerAgent(llm=_mock_llm())
        ctx = agent._build_user_context(_sample_input())
        assert ctx["ticker"] == "600519"
        assert ctx["name"] == "贵州茅台"
        assert ctx["industry"] == "食品饮料"
        assert "32.80%" in ctx["roe"]
        assert ctx["num_years"] == 3

    def test_build_context_missing_ratios(self):
        agent = ScreenerAgent(llm=_mock_llm())
        inp = ScreenerInput(ticker="000001", name="测试", ratios={})
        ctx = agent._build_user_context(inp)
        assert ctx["roe"] == "N/A"
        assert ctx["industry"] == "未知"

    @pytest.mark.asyncio
    async def test_run_proceed(self):
        from unittest.mock import AsyncMock
        llm = _mock_llm()
        llm.create_message = AsyncMock(
            return_value=_mock_response("PROCEED", "高ROE消费品龙头", "白酒行业高毛利高ROE")
        )

        agent = ScreenerAgent(llm=llm)
        result = await agent.run(_sample_input())
        assert result.decision == "PROCEED"
        assert "高ROE" in result.reason

    @pytest.mark.asyncio
    async def test_run_skip(self):
        from unittest.mock import AsyncMock
        llm = _mock_llm()
        llm.create_message = AsyncMock(
            return_value=_mock_response("SKIP", "连续亏损无改善迹象")
        )

        agent = ScreenerAgent(llm=llm)
        inp = ScreenerInput(
            ticker="000999", name="亏损公司",
            ratios={
                "fiscal_years": ["2021", "2022", "2023"],
                "roe": [-0.15, -0.22, -0.30],
            },
        )
        result = await agent.run(inp)
        assert result.decision == "SKIP"
