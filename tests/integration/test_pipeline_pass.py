"""Integration test: full pipeline with a company that passes all gates."""

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
            "business_model": 9,
            "competition_structure": 8,
            "financial_mapping": 9,
            "key_drivers": 8,
        },
        "fatal_unknowns": [],
        "why_it_is_or_is_not_coverable": "业务模式清晰，公开信息充分",
        "next_step": "进入信息捕获阶段",
    }


def _info_capture() -> dict:
    return {
        "company_profile": {"full_name": "贵州茅台酒股份有限公司"},
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
        "official_sources": ["上交所"],
        "trusted_third_party_sources": ["Wind"],
        "market_snapshot": {
            "price": 1680.0,
            "market_cap": 2.1e12,
            "enterprise_value": 2.05e12,
        },
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
        "income_statement": [{"fiscal_year": "2023", "fiscal_period": "FY", "revenue": 1.5e11, "net_income": 7.5e10}],
        "balance_sheet": [{"fiscal_year": "2023", "total_assets": 2.6e11, "shareholders_equity": 1.75e11}],
        "cash_flow": [{"fiscal_year": "2023", "operating_cash_flow": 8.2e10, "free_cash_flow": 7.6e10}],
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


def _accounting_risk_green() -> dict:
    return {
        "risk_level": "GREEN",
        "major_accounting_changes": [],
        "comparability_impact": "会计政策保持一致，可比性良好",
        "credibility_concern": "无可信度问题",
        "stop_or_continue": "继续分析",
    }


def _financial_quality_pass() -> dict:
    return {
        "pass_minimum_standard": True,
        "scores": {
            "per_share_growth": 8,
            "return_on_capital": 9,
            "cash_conversion": 9,
            "leverage_safety": 9,
            "capital_allocation": 7,
            "moat_financial_trace": 9,
        },
        "key_strengths": ["ROIC持续>30%", "自由现金流充裕"],
        "key_failures": [],
        "should_continue": "继续分析",
    }


def _net_cash() -> dict:
    return {
        "net_cash": 5.72e10,
        "net_cash_to_market_cap": 0.027,
        "attention_level": "NORMAL",
        "dividend_profile": {"pays_dividend": True, "coverage_ratio": 2.0},
        "buyback_profile": {"has_buyback": False, "shares_reduced": False},
        "cash_quality_notes": ["现金以银行存款为主，质量高"],
    }


def _valuation() -> dict:
    return {
        "valuation_method": ["DCF", "PE"],
        "expected_lookthrough_return": {"bear": 0.05, "base": 0.10, "bull": 0.15},
        "friction_adjusted_return": {"bear": 0.04, "base": 0.09, "bull": 0.14},
        "meets_hurdle_rate": True,
        "notes": ["基于10%门槛利率"],
    }


def _moat() -> dict:
    return {
        "industry_structure": "寡头垄断，茅台占据超高端白酒绝对份额",
        "moat_type": ["brand", "pricing_power"],
        "pricing_power_position": "极强定价权",
        "moat_trend": "稳定",
    }


def _compounding() -> dict:
    return {
        "compounding_engine": "品牌溢价驱动的高利润率复利增长",
        "incremental_return_on_capital": "增量ROIC持续超过30%",
        "sustainability_period": "10年以上",
        "per_share_value_growth_logic": "收入增长+利润率稳定+适度分红",
    }


def _psychology() -> dict:
    return {
        "management_incentive_distortion": "国企体制下激励机制有限但稳定",
        "market_sentiment_bias": "市场对白酒龙头存在品牌溢价偏好",
        "narrative_vs_fact_divergence": "叙事与事实基本一致",
    }


def _systems() -> dict:
    return {
        "single_points_of_failure": ["茅台镇产区不可复制"],
        "fragility_sources": ["政策风险"],
        "fault_tolerance": "高",
        "system_resilience": "极强品牌韧性",
    }


def _ecology() -> dict:
    return {
        "ecological_niche": "超高端白酒生态位",
        "adaptability_trend": "稳定",
        "cyclical_vs_structural": "弱周期性，结构性需求为主",
        "long_term_survival_probability": "极高",
    }


def _critic() -> dict:
    return {
        "kill_shots": ["全面禁酒令"],
        "permanent_loss_risks": ["食品安全事件"],
        "moat_destruction_paths": ["年轻一代消费习惯转变"],
        "management_failure_modes": ["国企改革失败"],
        "what_would_make_this_uninvestable": ["估值过高时买入"],
    }


def _committee_investable() -> dict:
    return {
        "final_label": "INVESTABLE",
        "thesis": "茅台拥有中国最强消费品牌",
        "anti_thesis": "估值溢价过高",
        "largest_unknowns": ["消费税改革方向"],
        "expected_return_summary": "年化10-12%",
        "why_now_or_why_not_now": "估值处于历史中位",
        "next_action": "加入观察清单",
    }


# 14 agent calls: info_capture, filing, triage, accounting_risk,
# financial_quality, net_cash, valuation, moat, compounding, psychology,
# systems, ecology, critic, committee
_ALL_RESPONSES = [
    _info_capture,
    _filing,
    _triage_pass,
    _accounting_risk_green,
    _financial_quality_pass,
    _net_cash,
    _valuation,
    _moat,
    _compounding,
    _psychology,
    _systems,
    _ecology,
    _critic,
    _committee_investable,
]


def _mock_filing_fetcher():
    fetcher = MagicMock()
    fetcher.market = "A_SHARE"
    fetcher.search_filings = AsyncMock(return_value=[
        FilingDocument(
            market="A_SHARE", ticker="600519", company_name="贵州茅台",
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
        ticker="600519.SS", name="KWEICHOW MOUTAI", currency="CNY",
        price=1680.0, market_cap=2.1e12, enterprise_value=2.05e12,
    ))
    return fetcher


@pytest.mark.asyncio
async def test_pipeline_pass_all_gates():
    llm = LLMClient(client=MagicMock())
    llm.create_message = AsyncMock(
        side_effect=[_mock_response(fn()) for fn in _ALL_RESPONSES]
    )

    intake = CompanyIntake(ticker="600519", name="贵州茅台", exchange="SSE")
    ctx = await run_pipeline(
        intake,
        llm=llm,
        filing_fetcher=_mock_filing_fetcher(),
        market_fetcher=_mock_market_fetcher(),
    )

    assert not ctx.is_stopped()
    # All 14 agents should have run
    completed = ctx.completed_agents()
    assert "triage" in completed
    assert "info_capture" in completed
    assert "filing" in completed
    assert "accounting_risk" in completed
    assert "financial_quality" in completed
    assert "net_cash" in completed
    assert "valuation" in completed
    assert "moat" in completed
    assert "compounding" in completed
    assert "psychology" in completed
    assert "systems" in completed
    assert "ecology" in completed
    assert "critic" in completed
    assert "committee" in completed
    assert len(completed) == 14

    # Verify committee output
    committee_result = ctx.get_result("committee")
    assert committee_result.final_label.value == "INVESTABLE"

    # Verify LLM was called 14 times
    assert llm.create_message.call_count == 14
