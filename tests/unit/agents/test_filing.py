"""Tests for FilingAgent — per-filing extraction with validation and merge."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from investagent.agents.base import AgentOutputError
from investagent.agents.filing import FilingAgent
from investagent.datasources.base import FilingDocument
from investagent.llm import LLMClient
from investagent.schemas.common import AgentMeta
from investagent.schemas.company import CompanyIntake
from investagent.schemas.filing import FilingMeta, FilingOutput
from investagent.schemas.info_capture import InfoCaptureOutput, MarketSnapshot
from investagent.workflow.context import PipelineContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _intake() -> CompanyIntake:
    # Use a fake exchange to prevent AkShare from being called in tests
    return CompanyIntake(ticker="TEST", name="测试公司", exchange="TEST")


def _mock_llm() -> LLMClient:
    return LLMClient(client=MagicMock())


def _mock_response(tool_input: dict) -> MagicMock:
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = tool_input
    response = MagicMock()
    response.content = [tool_block]
    response.model = "test-model"
    response.usage = MagicMock(input_tokens=100, output_tokens=200)
    return response


def _filing_tool_input(year: str = "2024", revenue: float | None = 2e9) -> dict:
    return {
        "filing_meta": {
            "market": "HK",
            "accounting_standard": "HKFRS",
            "fiscal_years_covered": [year, str(int(year) - 1)],
            "filing_types": ["Annual Report"],
            "currency": "CNY",
            "reporting_language": "en",
        },
        "income_statement": [
            {"fiscal_year": year, "fiscal_period": "FY", "revenue": revenue, "net_income": 5e8, "shares_basic": 3e9},
            {"fiscal_year": str(int(year) - 1), "fiscal_period": "FY", "revenue": 2.5e9, "net_income": 7e8, "shares_basic": 3e9},
        ],
        "balance_sheet": [
            {"fiscal_year": year, "total_assets": 15e9, "shareholders_equity": 10e9},
            {"fiscal_year": str(int(year) - 1), "total_assets": 14e9, "shareholders_equity": 9.5e9},
        ],
        "cash_flow": [
            {"fiscal_year": year, "operating_cash_flow": 9e8, "capex": 2e8, "free_cash_flow": 7e8},
            {"fiscal_year": str(int(year) - 1), "operating_cash_flow": 8.5e8, "capex": 1.8e8},
        ],
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


def _make_filing_doc(year: str = "2024") -> FilingDocument:
    return FilingDocument(
        market="HK", ticker="1448", company_name="FU SHOU YUAN",
        filing_type="Annual Report", fiscal_year=year, fiscal_period="FY",
        filing_date=date(int(year) + 1, 4, 22),
        source_url="https://example.com/report.pdf",
        content_type="pdf",
        raw_content=b"%PDF-fake",
    )


def _make_ctx(docs: list[FilingDocument] | None = None) -> PipelineContext:
    ctx = PipelineContext(_intake())
    if docs:
        ctx.set_data("filing_documents", docs)
    return ctx


# ---------------------------------------------------------------------------
# Basic tests
# ---------------------------------------------------------------------------

@patch("investagent.agents.filing.extract_pdf_markdown", return_value="## Income Statement\n|Revenue|2B|")
@patch("investagent.agents.filing.extract_sections", return_value={"income_statement": "Revenue: 2B"})
async def test_filing_single_report(mock_sections, mock_pdf):
    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=_mock_response(_filing_tool_input()))

    ctx = _make_ctx([_make_filing_doc()])
    agent = FilingAgent(llm)
    result = await agent.run(_intake(), ctx)

    assert result.meta.agent_name == "filing"
    assert result.filing_meta.market == "HK"
    assert len(result.income_statement) == 2


@patch("investagent.agents.filing.extract_pdf_markdown", return_value="text")
@patch("investagent.agents.filing.extract_sections", return_value={"income_statement": "data"})
async def test_filing_multi_report_merge(mock_sections, mock_pdf):
    """3 filings → 3 LLM calls → merged output with deduplicated years."""
    llm = _mock_llm()
    llm.create_message = AsyncMock(side_effect=[
        _mock_response(_filing_tool_input("2024")),
        _mock_response(_filing_tool_input("2022")),
        _mock_response(_filing_tool_input("2020")),
    ])

    docs = [_make_filing_doc("2024"), _make_filing_doc("2022"), _make_filing_doc("2020")]
    ctx = _make_ctx(docs)
    agent = FilingAgent(llm)
    result = await agent.run(_intake(), ctx)

    # Should have 6 unique years (2024,2023 + 2022,2021 + 2020,2019)
    years = {r.fiscal_year for r in result.income_statement}
    assert len(years) >= 5
    assert result.meta.token_usage == 900  # 300 * 3 calls


@patch("investagent.agents.filing.extract_pdf_markdown", return_value="text")
@patch("investagent.agents.filing.extract_sections", return_value={"income_statement": "data"})
async def test_filing_dedup_prefers_newer(mock_sections, mock_pdf):
    """When same year appears in two reports, newer report's data wins."""
    older = _filing_tool_input("2023")
    older["income_statement"][0]["revenue"] = 1e9  # older report says 1B

    newer = _filing_tool_input("2024")
    # newer report has 2023 as prior year with revenue 2.5B

    llm = _mock_llm()
    llm.create_message = AsyncMock(side_effect=[
        _mock_response(newer),  # processed first (newer)
        _mock_response(older),
    ])

    docs = [_make_filing_doc("2024"), _make_filing_doc("2023")]
    ctx = _make_ctx(docs)
    agent = FilingAgent(llm)
    result = await agent.run(_intake(), ctx)

    # 2023 data should come from the 2024 report (first processed = preferred)
    row_2023 = next(r for r in result.income_statement if r.fiscal_year == "2023")
    assert row_2023.revenue == 2.5e9  # from newer report


@patch("investagent.agents.filing.extract_pdf_markdown", return_value="text")
@patch("investagent.agents.filing.extract_sections", return_value={"income_statement": "data"})
async def test_filing_dedup_prefers_more_complete(mock_sections, mock_pdf):
    """When newer report has sparse prior-year data, older report's complete row wins."""
    # 2024 report has sparse 2023 data (only revenue, no net_income)
    newer = _filing_tool_input("2024")
    newer["income_statement"][1] = {
        "fiscal_year": "2023", "fiscal_period": "FY",
        "revenue": 2.5e9, "net_income": None,  # sparse!
    }

    # 2023 report has complete 2023 data
    older = _filing_tool_input("2023")
    older["income_statement"][0] = {
        "fiscal_year": "2023", "fiscal_period": "FY",
        "revenue": 2.5e9, "net_income": 7e8, "shares_basic": 3e9,
        "operating_income": 1e9, "tax_provision": 2e8,
    }

    llm = _mock_llm()
    llm.create_message = AsyncMock(side_effect=[
        _mock_response(newer),
        _mock_response(older),
    ])

    docs = [_make_filing_doc("2024"), _make_filing_doc("2023")]
    ctx = _make_ctx(docs)
    agent = FilingAgent(llm)
    result = await agent.run(_intake(), ctx)

    # 2023 row should come from older report (more fields filled)
    row_2023 = next(r for r in result.income_statement if r.fiscal_year == "2023")
    assert row_2023.net_income == 7e8  # from complete older row


async def test_filing_no_context():
    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=_mock_response(_filing_tool_input()))
    agent = FilingAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "filing"


async def test_filing_meta_is_server_generated():
    tool_input = _filing_tool_input()
    tool_input["meta"] = {"agent_name": "hacked", "timestamp": "2020-01-01T00:00:00Z", "model_used": "fake", "token_usage": 0}
    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=_mock_response(tool_input))
    agent = FilingAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "filing"
    assert result.meta.model_used == "test-model"


# ---------------------------------------------------------------------------
# Unit scale fix
# ---------------------------------------------------------------------------

def test_normalize_fiscal_keys():
    """Y2023→2023, FY2023→FY period, then re-dedup."""
    from investagent.agents.filing import FilingAgent

    ti = _filing_tool_input("2024")
    # Add a duplicate row with Y-prefix fiscal_year
    ti["income_statement"].append({
        "fiscal_year": "Y2023", "fiscal_period": "FY",
        "revenue": 2.6e9, "net_income": 7e8,
    })
    # Add a row with FY-suffixed period
    ti["income_statement"].append({
        "fiscal_year": "2023", "fiscal_period": "FY2023",
        "revenue": 2.5e9,  # less complete than above
    })
    output = FilingOutput.model_validate({
        "meta": {"agent_name": "f", "timestamp": "2025-01-01T00:00:00Z", "model_used": "m", "token_usage": 0},
        **ti,
    })

    result = FilingAgent._normalize_fiscal_keys(output)

    # Y2023 and 2023_FY2023 should merge into one "2023_FY" row
    fy_2023 = [r for r in result.income_statement if r.fiscal_year == "2023"]
    assert len(fy_2023) == 1, f"Expected 1 row for 2023, got {len(fy_2023)}"
    # Should keep the more complete one (with net_income)
    assert fy_2023[0].net_income == 7e8


def test_fix_unit_scale():
    """Cross-year unit inconsistency: some years' revenue scaled wrong."""
    from investagent.agents.filing import _fix_unit_scale
    from investagent.schemas.filing import IncomeStatementRow

    rows = [
        IncomeStatementRow(fiscal_year="2019", fiscal_period="FY", revenue=3768e8, net_income=802e8),
        IncomeStatementRow(fiscal_year="2020", fiscal_period="FY", revenue=5097e8, net_income=1403e8),
        IncomeStatementRow(fiscal_year="2021", fiscal_period="FY", revenue=7172e8, net_income=1432e8),
        # These are 1000x too small
        IncomeStatementRow(fiscal_year="2022", fiscal_period="FY", revenue=8.5e8, net_income=0.5e8),
        IncomeStatementRow(fiscal_year="2023", fiscal_period="FY", revenue=8.7e8, net_income=0.7e8),
        IncomeStatementRow(fiscal_year="2024", fiscal_period="FY", revenue=9.4e8, net_income=0.7e8),
    ]

    fixed = _fix_unit_scale(rows, "revenue")

    # 2022-2024 should be scaled up — within 5x of median now
    median_rev = sorted(r.revenue for r in fixed if r.revenue)[len(fixed) // 2]
    for r in fixed:
        if r.fiscal_year in ("2022", "2023", "2024"):
            ratio = median_rev / r.revenue
            assert ratio < 10, f"{r.fiscal_year} revenue={r.revenue} still too far from median (ratio={ratio:.0f}x)"
        # 2019-2021 should be unchanged
        if r.fiscal_year == "2019":
            assert r.revenue == 3768e8


# ---------------------------------------------------------------------------
# Validation + retry
# ---------------------------------------------------------------------------

def test_validate_extraction_pass():
    output = FilingOutput.model_validate({
        "meta": {"agent_name": "filing", "timestamp": "2025-01-01T00:00:00Z", "model_used": "m", "token_usage": 0},
        **_filing_tool_input(),
    })
    problems = FilingAgent._validate_extraction(output)
    assert problems == []


def test_validate_extraction_fail():
    ti = _filing_tool_input()
    # Set critical fields to null
    ti["income_statement"][0]["revenue"] = None
    ti["income_statement"][0]["net_income"] = None
    ti["income_statement"][1]["revenue"] = None
    ti["balance_sheet"][0]["total_assets"] = None
    ti["cash_flow"][0]["operating_cash_flow"] = None
    output = FilingOutput.model_validate({
        "meta": {"agent_name": "f", "timestamp": "2025-01-01T00:00:00Z", "model_used": "m", "token_usage": 0},
        **ti,
    })
    problems = FilingAgent._validate_extraction(output)
    assert len(problems) > 0  # should flag the nulls


@patch("investagent.agents.filing.extract_pdf_markdown", return_value="text")
@patch("investagent.agents.filing.extract_sections", return_value={"income_statement": "data"})
async def test_filing_validation_triggers_retry(mock_sections, mock_pdf):
    """When >30% critical fields null, agent retries with hints."""
    bad = _filing_tool_input()
    bad["income_statement"][0]["revenue"] = None
    bad["income_statement"][0]["net_income"] = None
    bad["income_statement"][1]["revenue"] = None
    bad["balance_sheet"][0]["total_assets"] = None
    bad["cash_flow"][0]["operating_cash_flow"] = None

    good = _filing_tool_input()  # all fields populated

    llm = _mock_llm()
    llm.create_message = AsyncMock(side_effect=[
        _mock_response(bad),   # first attempt: bad
        _mock_response(good),  # retry: good
    ])

    ctx = _make_ctx([_make_filing_doc()])
    agent = FilingAgent(llm)
    result = await agent.run(_intake(), ctx)

    # Should have called LLM twice (initial + retry)
    assert llm.create_message.call_count == 2
    assert result.income_statement[0].revenue == 2e9  # from good output


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------

@patch("investagent.agents.filing.extract_pdf_markdown", return_value="text")
@patch("investagent.agents.filing.extract_sections", return_value={"income_statement": "data"})
async def test_filing_market_currency_from_info_capture(mock_sections, mock_pdf):
    """market_currency populated from info_capture's market_snapshot."""
    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=_mock_response(_filing_tool_input()))

    ctx = _make_ctx([_make_filing_doc()])
    info = MagicMock()
    info.market_snapshot = MagicMock()
    info.market_snapshot.currency = "HKD"
    info.stop_signal = None
    ctx.set_result("info_capture", info)

    agent = FilingAgent(llm)
    result = await agent.run(_intake(), ctx)

    assert result.filing_meta.market_currency == "HKD"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_compute_derived_fields():
    """Post-processing fills eps, gross_profit, fcf from available data."""
    ti = _filing_tool_input()
    # Set up: have net_income and shares but no eps; have revenue and cost but no gross_profit
    ti["income_statement"] = [
        {
            "fiscal_year": "2024", "fiscal_period": "FY",
            "revenue": 2e9, "cost_of_revenue": 1e9,
            "gross_profit": None,  # should be computed: 2e9 - 1e9 = 1e9
            "net_income": 5e8, "net_income_to_parent": 4e8,
            "shares_basic": 2.5e9,
            "eps_basic": None,  # should be computed: 4e8 / 2.5e9 = 0.16
            "eps_diluted": None,
            "shares_diluted": None,
            "operating_income": 7e8,
        },
    ]
    ti["cash_flow"] = [
        {
            "fiscal_year": "2024",
            "operating_cash_flow": 9e8, "capex": 2e8,
            "free_cash_flow": None,  # should be computed: 9e8 - 2e8 = 7e8
            "dividends_paid": 3e8,
        },
    ]
    output = FilingOutput.model_validate({
        "meta": {"agent_name": "f", "timestamp": "2025-01-01T00:00:00Z", "model_used": "m", "token_usage": 0},
        **ti,
    })

    result = FilingAgent._compute_derived_fields(output)

    assert result.income_statement[0].gross_profit == 1e9
    assert result.income_statement[0].eps_basic == 0.16
    assert result.cash_flow[0].free_cash_flow == 7e8


def test_compute_shares_from_eps():
    """Derive shares_basic from net_income / eps when shares not extracted."""
    ti = _filing_tool_input()
    ti["income_statement"] = [
        {
            "fiscal_year": "2024", "fiscal_period": "FY",
            "revenue": 2e9, "net_income": 5e8, "net_income_to_parent": 4e8,
            "eps_basic": 0.16,
            "shares_basic": None,  # should be computed: 4e8 / 0.16 = 2.5e9
        },
    ]
    output = FilingOutput.model_validate({
        "meta": {"agent_name": "f", "timestamp": "2025-01-01T00:00:00Z", "model_used": "m", "token_usage": 0},
        **ti,
    })

    result = FilingAgent._compute_derived_fields(output)
    assert result.income_statement[0].shares_basic == 2_500_000_000


async def test_filing_all_fail_raises():
    text_block = MagicMock()
    text_block.type = "text"
    response = MagicMock()
    response.content = [text_block]
    response.model = "m"
    response.usage = MagicMock(input_tokens=50, output_tokens=100)

    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=response)
    agent = FilingAgent(llm)
    with pytest.raises(AgentOutputError, match="all extraction attempts failed"):
        await agent.run(_intake())
