"""Tests for report generation."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from investagent.report import generate_report
from investagent.schemas.common import AgentMeta, StopSignal
from investagent.schemas.company import CompanyIntake
from investagent.schemas.info_capture import (
    FilingRef,
    InfoCaptureOutput,
    MarketSnapshot,
)
from investagent.schemas.triage import (
    ExplainabilityScore,
    TriageDecision,
    TriageOutput,
)
from investagent.workflow.context import PipelineContext


def _meta(name: str = "test") -> AgentMeta:
    return AgentMeta(
        agent_name=name,
        timestamp=datetime(2025, 1, 1),
        model_used="mock",
        token_usage=100,
    )


def _make_info_capture() -> InfoCaptureOutput:
    return InfoCaptureOutput(
        meta=_meta("info_capture"),
        company_profile={"full_name": "福寿园国际集团", "listing": "HKEX:1448"},
        filing_manifest=[
            FilingRef(
                filing_type="Annual Report",
                fiscal_year="2024",
                fiscal_period="FY",
                filing_date="2025-04-22",
                source_url="https://example.com/report.pdf",
                content_type="pdf",
            ),
        ],
        official_sources=["HKEX"],
        trusted_third_party_sources=["Wind"],
        market_snapshot=MarketSnapshot(
            price=2.64, market_cap=6e9, currency="HKD",
        ),
        missing_items=[],
    )


def _make_triage(decision: str = "PASS") -> TriageOutput:
    return TriageOutput(
        meta=_meta("triage"),
        decision=TriageDecision(decision),
        explainability_score=ExplainabilityScore(
            business_model=7,
            competition_structure=6,
            financial_mapping=7,
            key_drivers=7,
        ),
        fatal_unknowns=[],
        why_it_is_or_is_not_coverable="数据充足",
        next_step="继续分析",
        data_availability_summary="5年数据完整",
    )


def test_report_with_info_capture_and_triage():
    intake = CompanyIntake(ticker="1448", name="福寿园", exchange="HKEX")
    ctx = PipelineContext(intake)
    ctx.set_result("info_capture", _make_info_capture())
    ctx.set_result("triage", _make_triage())

    report = generate_report(ctx, elapsed=30.0)

    assert "福寿园" in report
    assert "1448" in report
    assert "HKEX" in report
    assert "Info Capture" in report or "info_capture" in report
    assert "Triage" in report or "triage" in report
    assert "Annual Report" in report
    assert "2.64" in report
    assert "30s" in report or "30" in report


def test_report_stopped_pipeline():
    intake = CompanyIntake(ticker="1448", name="福寿园", exchange="HKEX")
    ctx = PipelineContext(intake)
    ctx.set_result("info_capture", _make_info_capture())
    ctx.set_result("triage", _make_triage("REJECT"))
    ctx.stop("Triage rejected: 数据不足")

    report = generate_report(ctx)

    assert "停止" in report or "REJECT" in report
    assert "数据不足" in report or "Triage" in report
    # Should NOT contain agents that didn't run
    assert "committee" not in report.lower() or "Committee" not in report


def test_report_empty_pipeline():
    intake = CompanyIntake(ticker="1448", name="福寿园", exchange="HKEX")
    ctx = PipelineContext(intake)

    report = generate_report(ctx)

    assert "福寿园" in report
    assert "1448" in report


def test_report_overview_table():
    intake = CompanyIntake(ticker="1448", name="福寿园", exchange="HKEX")
    ctx = PipelineContext(intake)
    ctx.set_result("info_capture", _make_info_capture())

    report = generate_report(ctx)

    assert "| Agent" in report
    assert "| info_capture" in report
    assert "100" in report  # token count
