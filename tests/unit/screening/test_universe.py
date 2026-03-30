"""Tests for investagent.screening.universe."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from investagent.screening.universe import (
    apply_llm_exclusions,
    apply_rule_exclusions,
)


# ---------------------------------------------------------------------------
# Rule exclusion tests
# ---------------------------------------------------------------------------

class TestRuleExclusions:
    def test_excludes_st(self):
        stocks = [
            {"ticker": "000001", "name": "ST平安"},
            {"ticker": "000002", "name": "*ST万科"},
            {"ticker": "600519", "name": "贵州茅台"},
        ]
        result = apply_rule_exclusions(stocks)
        assert len(result) == 1
        assert result[0]["ticker"] == "600519"

    def test_excludes_financial(self):
        stocks = [
            {"ticker": "601398", "name": "工商银行", "industry": "银行"},
            {"ticker": "601318", "name": "中国平安", "industry": "非银金融"},
            {"ticker": "600519", "name": "贵州茅台", "industry": "食品饮料"},
        ]
        result = apply_rule_exclusions(stocks)
        assert len(result) == 1
        assert result[0]["ticker"] == "600519"

    def test_excludes_low_disclosure(self):
        stocks = [
            {"ticker": "300999", "name": "新股A", "annual_report_count": 2},
            {"ticker": "600519", "name": "贵州茅台", "annual_report_count": 20},
        ]
        result = apply_rule_exclusions(stocks)
        assert len(result) == 1
        assert result[0]["ticker"] == "600519"

    def test_keeps_when_no_report_count(self):
        """If annual_report_count is not populated, don't exclude."""
        stocks = [{"ticker": "600519", "name": "贵州茅台"}]
        result = apply_rule_exclusions(stocks)
        assert len(result) == 1

    def test_empty_input(self):
        assert apply_rule_exclusions([]) == []

    def test_all_excluded(self):
        stocks = [
            {"ticker": "000001", "name": "ST平安"},
            {"ticker": "601398", "name": "工商银行", "industry": "银行"},
        ]
        result = apply_rule_exclusions(stocks)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# LLM exclusion tests
# ---------------------------------------------------------------------------

def _mock_llm_response(decision: str, reason: str) -> MagicMock:
    """Create a mock LLM response with a tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.input = {"decision": decision, "reason": reason}
    response = MagicMock()
    response.content = [block]
    return response


class TestLLMExclusions:
    @pytest.mark.asyncio
    async def test_excludes_opaque_tech(self):
        llm = AsyncMock()
        llm.create_message = AsyncMock(
            return_value=_mock_llm_response("EXCLUDE", "军工企业")
        )
        stocks = [{"ticker": "600893", "name": "航发动力", "industry": "国防军工"}]
        result = await apply_llm_exclusions(stocks, llm)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_keeps_normal_company(self):
        llm = AsyncMock()
        llm.create_message = AsyncMock(
            return_value=_mock_llm_response("KEEP", "正常消费品企业")
        )
        stocks = [{"ticker": "600519", "name": "贵州茅台", "industry": "食品饮料"}]
        result = await apply_llm_exclusions(stocks, llm)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_mixed_decisions(self):
        responses = [
            _mock_llm_response("EXCLUDE", "创新药企业"),
            _mock_llm_response("KEEP", "正常制造业"),
            _mock_llm_response("EXCLUDE", "壳公司"),
        ]
        llm = AsyncMock()
        llm.create_message = AsyncMock(side_effect=responses)
        stocks = [
            {"ticker": "688001", "name": "创新药A"},
            {"ticker": "000333", "name": "美的集团"},
            {"ticker": "600999", "name": "壳公司B"},
        ]
        result = await apply_llm_exclusions(stocks, llm)
        assert len(result) == 1
        assert result[0]["ticker"] == "000333"

    @pytest.mark.asyncio
    async def test_llm_error_keeps_stock(self):
        """If LLM call fails, keep the stock (conservative)."""
        llm = AsyncMock()
        llm.create_message = AsyncMock(side_effect=Exception("API error"))
        stocks = [{"ticker": "600519", "name": "贵州茅台"}]
        result = await apply_llm_exclusions(stocks, llm)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_input(self):
        llm = AsyncMock()
        result = await apply_llm_exclusions([], llm)
        assert result == []
