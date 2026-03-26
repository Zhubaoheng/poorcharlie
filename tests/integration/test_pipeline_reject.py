"""Integration test: pipeline stops at triage (now after InfoCapture + Filing)."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from investagent.datasources.base import FilingDocument, MarketQuote
from investagent.llm import LLMClient
from investagent.schemas.company import CompanyIntake
from investagent.workflow.orchestrator import run_pipeline


def _mock_response(tool_input: dict) -> MagicMock:
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = tool_input
    response = MagicMock()
    response.content = [tool_block]
    response.model = "mock-model"
    response.usage = MagicMock()
    response.usage.input_tokens = 50
    response.usage.output_tokens = 50
    return response


def _info_capture() -> dict:
    return {
        "company_profile": {"full_name": "不透明公司"},
        "filing_manifest": [],
        "official_sources": [],
        "trusted_third_party_sources": [],
        "market_snapshot": {},
        "missing_items": ["无法获取任何财报"],
    }


def _filing() -> dict:
    return {
        "filing_meta": {
            "market": "A_SHARE",
            "accounting_standard": "CAS",
            "fiscal_years_covered": [],
            "filing_types": [],
            "currency": "CNY",
            "reporting_language": "zh-CN",
        },
        "income_statement": [],
        "balance_sheet": [],
        "cash_flow": [],
        "segments": [],
        "accounting_policies": [],
        "debt_schedule": [],
        "covenant_status": [],
        "special_items": [],
        "concentration": None,
        "buyback_history": [],
        "acquisition_history": [],
        "dividend_per_share_history": [],
        "footnote_extracts": [],
        "risk_factors": [],
    }


def _triage_reject() -> dict:
    return {
        "decision": "REJECT",
        "explainability_score": {
            "business_model": 2,
            "competition_structure": 3,
            "financial_mapping": 1,
            "key_drivers": 2,
        },
        "fatal_unknowns": [
            "零份财报可获取",
            "财务数据完全不可用",
        ],
        "why_it_is_or_is_not_coverable": "该公司无任何可获取的财报数据，无法进行有意义的分析",
        "next_step": "放弃研究",
        "data_availability_summary": "无财报、无市场数据",
    }


_RESPONSES = [
    _info_capture,
    _filing,
    _triage_reject,
]


def _mock_filing_fetcher():
    fetcher = MagicMock()
    fetcher.market = "A_SHARE"
    fetcher.search_filings = AsyncMock(return_value=[])  # No filings found
    return fetcher


def _mock_market_fetcher():
    fetcher = MagicMock()
    fetcher.get_quote = AsyncMock(return_value=MarketQuote(
        ticker="000001.SZ", name="不透明公司", currency="CNY",
    ))
    return fetcher


@pytest.mark.asyncio
async def test_pipeline_reject_at_triage():
    llm = LLMClient(client=MagicMock())
    llm.create_message = AsyncMock(
        side_effect=[_mock_response(fn()) for fn in _RESPONSES]
    )

    intake = CompanyIntake(ticker="000001", name="不透明公司", exchange="SZSE")
    ctx = await run_pipeline(
        intake,
        llm=llm,
        filing_fetcher=_mock_filing_fetcher(),
        market_fetcher=_mock_market_fetcher(),
    )

    # Pipeline should be stopped
    assert ctx.is_stopped()
    assert "Triage rejected" in ctx.stop_reason

    # InfoCapture + Filing + Triage should have run
    completed = ctx.completed_agents()
    assert completed == ["info_capture", "filing", "triage"]

    # LLM called 3 times (info_capture + filing + triage)
    assert llm.create_message.call_count == 3
