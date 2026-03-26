"""Integration test: pipeline stops at accounting risk gate."""

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


def _triage_pass() -> dict:
    return {
        "decision": "PASS",
        "explainability_score": {
            "business_model": 8,
            "competition_structure": 7,
            "financial_mapping": 8,
            "key_drivers": 7,
        },
        "fatal_unknowns": [],
        "why_it_is_or_is_not_coverable": "业务模式清晰",
        "next_step": "进入信息捕获",
    }


def _info_capture() -> dict:
    return {
        "company_profile": {"full_name": "某问题公司"},
        "filing_manifest": [
            {
                "filing_type": "年报",
                "fiscal_year": "2023",
                "fiscal_period": "FY",
                "filing_date": "2024-03-28",
                "source_url": "https://static.cninfo.com.cn/2023.PDF",
                "content_type": "pdf",
            },
        ],
        "official_sources": ["交易所"],
        "trusted_third_party_sources": [],
        "market_snapshot": {"price": 10.0, "market_cap": 5e9, "enterprise_value": 6e9},
        "missing_items": [],
    }


def _filing() -> dict:
    return {
        "filing_meta": {
            "market": "A_SHARE",
            "accounting_standard": "CAS",
            "fiscal_years_covered": ["2023"],
            "filing_types": ["年报"],
            "currency": "CNY",
            "reporting_language": "zh-CN",
        },
        "income_statement": [{"fiscal_year": "2023", "fiscal_period": "FY", "revenue": 1e10}],
        "balance_sheet": [{"fiscal_year": "2023", "total_assets": 2e10}],
        "cash_flow": [{"fiscal_year": "2023", "operating_cash_flow": 1e9}],
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


def _accounting_risk_red() -> dict:
    return {
        "risk_level": "RED",
        "major_accounting_changes": [
            "频繁变更收入确认政策",
            "审计师连续两年出具保留意见",
            "对前三年财务数据进行重述",
        ],
        "comparability_impact": "会计政策频繁变更导致各年数据严重不可比",
        "credibility_concern": "审计保留意见+财务重述，财务数据可信度严重受损",
        "stop_or_continue": "建议停止，原因：财务数据可信度不足以支撑有意义的估值分析",
    }


# 4 agents: info_capture -> filing -> triage (pass) -> accounting_risk (RED -> stop)
_RESPONSES = [
    _info_capture,
    _filing,
    _triage_pass,
    _accounting_risk_red,
]


def _mock_filing_fetcher():
    fetcher = MagicMock()
    fetcher.market = "A_SHARE"
    fetcher.search_filings = AsyncMock(return_value=[
        FilingDocument(
            market="A_SHARE", ticker="000999", company_name="问题公司",
            filing_type="年报", fiscal_year="2023", fiscal_period="FY",
            filing_date=date(2024, 3, 28),
            source_url="https://static.cninfo.com.cn/2023.PDF",
            content_type="pdf",
        ),
    ])
    return fetcher


def _mock_market_fetcher():
    fetcher = MagicMock()
    fetcher.get_quote = AsyncMock(return_value=MarketQuote(
        ticker="000999.SZ", name="问题公司", currency="CNY",
        price=10.0, market_cap=5e9, enterprise_value=6e9,
    ))
    return fetcher


@pytest.mark.asyncio
async def test_pipeline_stop_at_accounting_risk():
    llm = LLMClient(client=MagicMock())
    llm.create_message = AsyncMock(
        side_effect=[_mock_response(fn()) for fn in _RESPONSES]
    )

    intake = CompanyIntake(ticker="000999", name="问题公司", exchange="SZSE")
    ctx = await run_pipeline(
        intake,
        llm=llm,
        filing_fetcher=_mock_filing_fetcher(),
        market_fetcher=_mock_market_fetcher(),
    )

    # Pipeline should be stopped
    assert ctx.is_stopped()
    assert "Accounting risk RED" in ctx.stop_reason

    # Only first 4 agents should have run
    completed = ctx.completed_agents()
    assert completed == ["info_capture", "filing", "triage", "accounting_risk"]

    # No agents after accounting_risk
    assert "financial_quality" not in completed
    assert "committee" not in completed

    # LLM called exactly 4 times
    assert llm.create_message.call_count == 4
