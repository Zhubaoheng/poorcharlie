"""Tests for investagent.agents.filing — hybrid agent with real content extraction."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from investagent.agents.base import AgentOutputError
from investagent.agents.filing import FilingAgent
from investagent.datasources.base import FilingDocument
from investagent.llm import LLMClient
from investagent.schemas.company import CompanyIntake
from investagent.workflow.context import PipelineContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _intake() -> CompanyIntake:
    return CompanyIntake(ticker="1448", name="福寿园", exchange="HKEX")


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


def _filing_tool_input() -> dict:
    return {
        "filing_meta": {
            "market": "HK",
            "accounting_standard": "HKFRS",
            "fiscal_years_covered": ["2023", "2024"],
            "filing_types": ["Annual Report"],
            "currency": "CNY",
            "reporting_language": "en",
        },
        "income_statement": [
            {
                "fiscal_year": "2024",
                "fiscal_period": "FY",
                "revenue": 2800000000.0,
                "net_income": 700000000.0,
            },
        ],
        "balance_sheet": [
            {
                "fiscal_year": "2024",
                "total_assets": 15000000000.0,
                "shareholders_equity": 10000000000.0,
            },
        ],
        "cash_flow": [
            {
                "fiscal_year": "2024",
                "operating_cash_flow": 900000000.0,
                "free_cash_flow": 800000000.0,
            },
        ],
        "segments": [],
        "accounting_policies": [
            {
                "category": "revenue_recognition",
                "fiscal_year": "2024",
                "method": "Revenue recognised at point in time",
                "raw_text": "Revenue from burial services...",
                "changed_from_prior": False,
            },
        ],
        "debt_schedule": [],
        "covenant_status": [],
        "special_items": [],
        "concentration": None,
        "buyback_history": [],
        "acquisition_history": [],
        "dividend_per_share_history": [],
        "footnote_extracts": [],
        "risk_factors": [
            {
                "category": "regulatory",
                "description": "Burial reform policies",
                "raw_text": "The PRC government promotes...",
                "materiality": "high",
            },
        ],
    }


def _make_filing_doc(
    fiscal_year: str = "2024",
    raw_content: bytes | None = b"%PDF-fake",
    text_content: str | None = None,
) -> FilingDocument:
    return FilingDocument(
        market="HK",
        ticker="1448",
        company_name="FU SHOU YUAN",
        filing_type="Annual Report",
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        filing_date=date(2025, 4, 22),
        source_url="https://example.com/report.pdf",
        content_type="pdf",
        raw_content=raw_content,
        text_content=text_content,
    )


def _make_ctx(filing_docs: list[FilingDocument] | None = None) -> PipelineContext:
    ctx = PipelineContext(_intake())
    if filing_docs is not None:
        ctx.set_data("filing_documents", filing_docs)
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@patch("investagent.agents.filing.extract_pdf_markdown")
@patch("investagent.agents.filing.extract_sections")
async def test_filing_with_pdf_content(mock_sections, mock_pdf):
    """PDF content flows through extraction into LLM context."""
    mock_pdf.return_value = "## Income Statement\n| Revenue | 2800M |"
    mock_sections.return_value = {"income_statement": "Revenue: 2800M"}

    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_filing_tool_input())
    )

    doc = _make_filing_doc()
    ctx = _make_ctx([doc])

    agent = FilingAgent(llm)
    result = await agent.run(_intake(), ctx)

    assert result.meta.agent_name == "filing"
    assert result.filing_meta.market == "HK"
    assert result.income_statement[0].revenue == 2800000000.0
    mock_pdf.assert_called_once_with(b"%PDF-fake")
    mock_sections.assert_called_once()


@patch("investagent.agents.filing.extract_pdf_markdown")
@patch("investagent.agents.filing.extract_sections")
async def test_filing_with_text_content(mock_sections, mock_pdf):
    """Text content (e.g., EDGAR HTML) skips PDF extraction."""
    mock_sections.return_value = {"income_statement": "Revenue: 100M"}

    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_filing_tool_input())
    )

    doc = _make_filing_doc(raw_content=None, text_content="<html>Annual Report</html>")
    ctx = _make_ctx([doc])

    agent = FilingAgent(llm)
    result = await agent.run(_intake(), ctx)

    assert result.meta.agent_name == "filing"
    mock_pdf.assert_not_called()  # No PDF extraction for text content


@patch("investagent.agents.filing.extract_pdf_markdown")
@patch("investagent.agents.filing.extract_sections")
async def test_filing_extraction_failure_graceful(mock_sections, mock_pdf):
    """If PDF extraction fails, agent still produces output."""
    mock_pdf.return_value = ""  # Extraction failed
    mock_sections.return_value = {}

    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_filing_tool_input())
    )

    doc = _make_filing_doc()
    ctx = _make_ctx([doc])

    agent = FilingAgent(llm)
    result = await agent.run(_intake(), ctx)

    # Should succeed even with no extracted content
    assert result.filing_meta.market == "HK"


async def test_filing_no_context():
    """FilingAgent works without context (no filing_documents)."""
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_filing_tool_input())
    )

    agent = FilingAgent(llm)
    result = await agent.run(_intake())

    assert result.meta.agent_name == "filing"


async def test_filing_meta_is_server_generated():
    tool_input = _filing_tool_input()
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
    agent = FilingAgent(llm)
    result = await agent.run(_intake())
    assert result.meta.agent_name == "filing"
    assert result.meta.model_used == "claude-sonnet-4-20250514"


async def test_filing_no_tool_use_raises():
    text_block = MagicMock()
    text_block.type = "text"
    response = MagicMock()
    response.content = [text_block]
    response.model = "claude-sonnet-4-20250514"
    response.usage = MagicMock(input_tokens=50, output_tokens=100)

    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=response)
    agent = FilingAgent(llm)
    with pytest.raises(AgentOutputError, match="no tool_use block"):
        await agent.run(_intake())


async def test_filing_malformed_output_raises():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response({"invalid": "data"})
    )
    agent = FilingAgent(llm)
    with pytest.raises(AgentOutputError, match="failed to validate"):
        await agent.run(_intake())
