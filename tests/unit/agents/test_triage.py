"""Tests for investagent.agents.triage — now runs after InfoCapture + Filing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from investagent.agents.base import AgentOutputError
from investagent.agents.triage import TriageAgent
from investagent.llm import LLMClient
from investagent.schemas.company import CompanyIntake
from investagent.schemas.common import AgentMeta
from investagent.schemas.info_capture import (
    FilingRef,
    InfoCaptureOutput,
    MarketSnapshot,
)
from investagent.schemas.triage import TriageDecision
from investagent.workflow.context import PipelineContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _intake() -> CompanyIntake:
    return CompanyIntake(ticker="600519", name="贵州茅台", exchange="SSE")


def _mock_llm() -> LLMClient:
    return LLMClient(client=MagicMock())


def _mock_info_capture_output() -> InfoCaptureOutput:
    return InfoCaptureOutput(
        meta=AgentMeta(
            agent_name="info_capture",
            timestamp="2025-01-01T00:00:00Z",
            model_used="mock",
            token_usage=0,
        ),
        company_profile={"full_name": "贵州茅台酒股份有限公司", "listing": "SSE:600519"},
        filing_manifest=[
            FilingRef(
                filing_type="年报", fiscal_year="2023", fiscal_period="FY",
                filing_date="2024-03-28",
                source_url="https://static.cninfo.com.cn/2023.PDF",
                content_type="pdf",
            ),
            FilingRef(
                filing_type="年报", fiscal_year="2022", fiscal_period="FY",
                filing_date="2023-03-30",
                source_url="https://static.cninfo.com.cn/2022.PDF",
                content_type="pdf",
            ),
        ],
        official_sources=["上交所"],
        trusted_third_party_sources=["Wind"],
        market_snapshot=MarketSnapshot(
            price=1680.0, market_cap=2.1e12, enterprise_value=2.05e12, currency="CNY",
        ),
        missing_items=[],
    )


def _mock_filing_output() -> MagicMock:
    """Minimal mock of FilingOutput with attributes needed by _build_filing_data_summary."""
    filing = MagicMock()
    filing.filing_meta = MagicMock()
    filing.filing_meta.fiscal_years_covered = ["2022", "2023"]
    filing.filing_meta.accounting_standard = "CAS"
    filing.filing_meta.currency = "CNY"
    filing.income_statement = [MagicMock(), MagicMock()]
    filing.balance_sheet = [MagicMock(), MagicMock()]
    filing.cash_flow = [MagicMock(), MagicMock()]
    filing.segments = [MagicMock()]
    policy = MagicMock()
    policy.changed_from_prior = False
    filing.accounting_policies = [policy]
    filing.concentration = MagicMock()
    risk = MagicMock()
    risk.category = "regulatory"
    filing.risk_factors = [risk]
    footnote = MagicMock()
    footnote.topic = "debt"
    filing.footnote_extracts = [footnote]
    filing.debt_schedule = []
    filing.special_items = []
    # Give it a stop_signal for PipelineContext.set_result
    filing.stop_signal = None
    return filing


def _make_ctx() -> PipelineContext:
    """PipelineContext pre-populated with InfoCapture + Filing results."""
    ctx = PipelineContext(_intake())
    ctx.set_result("info_capture", _mock_info_capture_output())
    ctx.set_result("filing", _mock_filing_output())
    return ctx


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


def _triage_tool_input(decision: str = "PASS") -> dict:
    return {
        "decision": decision,
        "explainability_score": {
            "business_model": 9,
            "competition_structure": 8,
            "financial_mapping": 8,
            "key_drivers": 9,
        },
        "fatal_unknowns": [],
        "why_it_is_or_is_not_coverable": "茅台商业模式清晰，数据充足",
        "next_step": "进入深度分析",
        "data_availability_summary": "2年年报 + 完整市场数据",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_triage_pass():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_triage_tool_input("PASS"))
    )
    agent = TriageAgent(llm)
    result = await agent.run(_intake(), _make_ctx())
    assert result.decision == TriageDecision.PASS
    assert result.meta.agent_name == "triage"
    assert result.meta.token_usage == 300


async def test_triage_reject():
    llm = _mock_llm()
    tool_input = _triage_tool_input("REJECT")
    tool_input["fatal_unknowns"] = ["财报数据不足"]
    llm.create_message = AsyncMock(
        return_value=_mock_response(tool_input)
    )
    agent = TriageAgent(llm)
    result = await agent.run(_intake(), _make_ctx())
    assert result.decision == TriageDecision.REJECT
    assert len(result.fatal_unknowns) == 1


async def test_triage_watch():
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_triage_tool_input("WATCH"))
    )
    agent = TriageAgent(llm)
    result = await agent.run(_intake(), _make_ctx())
    assert result.decision == TriageDecision.WATCH


async def test_triage_meta_is_server_generated():
    tool_input = _triage_tool_input()
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
    agent = TriageAgent(llm)
    result = await agent.run(_intake(), _make_ctx())
    assert result.meta.agent_name == "triage"
    assert result.meta.model_used == "claude-sonnet-4-20250514"


async def test_triage_no_tool_use_raises():
    text_block = MagicMock()
    text_block.type = "text"
    response = MagicMock()
    response.content = [text_block]
    response.model = "claude-sonnet-4-20250514"
    response.usage = MagicMock(input_tokens=50, output_tokens=100)

    llm = _mock_llm()
    llm.create_message = AsyncMock(return_value=response)
    agent = TriageAgent(llm)
    with pytest.raises(AgentOutputError, match="no tool_use block"):
        await agent.run(_intake(), _make_ctx())


async def test_triage_malformed_output_raises():
    bad_input = {"decision": "INVALID_VALUE", "random_field": 42}
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(bad_input)
    )
    agent = TriageAgent(llm)
    with pytest.raises(AgentOutputError, match="failed to validate"):
        await agent.run(_intake(), _make_ctx())


async def test_triage_without_context():
    """Triage should handle ctx=None gracefully."""
    llm = _mock_llm()
    llm.create_message = AsyncMock(
        return_value=_mock_response(_triage_tool_input("PASS"))
    )
    agent = TriageAgent(llm)
    result = await agent.run(_intake())  # No ctx
    assert result.decision == TriageDecision.PASS


async def test_triage_context_populates_filing_summary():
    """Verify _build_user_context extracts filing data summary."""
    agent = TriageAgent(_mock_llm())
    ctx = _make_ctx()
    context = agent._build_user_context(_intake(), ctx)

    assert context["has_info_capture"] is True
    assert context["has_filing"] is True
    assert len(context["filing_refs"]) == 2
    assert context["market_snapshot"].price == 1680.0
    assert context["filing_data_summary"]["income_statement_years"] == 2
    assert context["filing_data_summary"]["accounting_standard"] == "CAS"
