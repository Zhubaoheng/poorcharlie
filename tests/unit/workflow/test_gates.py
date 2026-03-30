"""Tests for investagent.workflow.gates."""

from datetime import datetime, timezone

from investagent.schemas.accounting_risk import AccountingRiskOutput, RiskLevel
from investagent.schemas.common import AgentMeta
from investagent.schemas.company import CompanyIntake
from investagent.schemas.financial_quality import (
    FinancialQualityOutput,
    FinancialQualityScores,
)
from investagent.schemas.triage import (
    ExplainabilityScore,
    TriageDecision,
    TriageOutput,
)
from investagent.workflow.context import PipelineContext
from investagent.workflow.gates import (
    check_accounting_risk_gate,
    check_financial_quality_gate,
    check_triage_gate,
)


def _meta(name: str) -> AgentMeta:
    return AgentMeta(
        agent_name=name,
        timestamp=datetime.now(tz=timezone.utc),
        model_used="test",
        token_usage=0,
    )


def _make_ctx() -> PipelineContext:
    return PipelineContext(
        CompanyIntake(ticker="AAPL", name="Apple Inc.", exchange="NASDAQ")
    )


def _scores() -> ExplainabilityScore:
    return ExplainabilityScore(
        business_model=8,
        competition_structure=7,
        financial_mapping=6,
        key_drivers=7,
    )


class TestTriageGate:
    def test_pass_proceeds(self):
        ctx = _make_ctx()
        ctx.set_result(
            "triage",
            TriageOutput(
                meta=_meta("triage"),
                decision=TriageDecision.PASS,
                explainability_score=_scores(),
                fatal_unknowns=[],
                why_it_is_or_is_not_coverable="Clear business model",
                next_step="Continue",
            ),
        )
        proceed, reason = check_triage_gate(ctx)
        assert proceed is True
        assert reason == ""

    def test_watch_proceeds(self):
        ctx = _make_ctx()
        ctx.set_result(
            "triage",
            TriageOutput(
                meta=_meta("triage"),
                decision=TriageDecision.WATCH,
                explainability_score=_scores(),
                fatal_unknowns=[],
                why_it_is_or_is_not_coverable="Some concerns",
                next_step="Monitor",
            ),
        )
        proceed, _ = check_triage_gate(ctx)
        assert proceed is True

    def test_reject_stops(self):
        ctx = _make_ctx()
        ctx.set_result(
            "triage",
            TriageOutput(
                meta=_meta("triage"),
                decision=TriageDecision.REJECT,
                explainability_score=_scores(),
                fatal_unknowns=["opaque structure"],
                why_it_is_or_is_not_coverable="Too complex",
                next_step="Stop",
            ),
        )
        proceed, reason = check_triage_gate(ctx)
        assert proceed is False
        assert "Too complex" in reason


class TestAccountingRiskGate:
    def test_green_proceeds(self):
        ctx = _make_ctx()
        ctx.set_result(
            "accounting_risk",
            AccountingRiskOutput(
                meta=_meta("accounting_risk"),
                risk_level=RiskLevel.GREEN,
                major_accounting_changes=[],
                comparability_impact="None",
                credibility_concern="None",
                stop_or_continue="Continue",
            ),
        )
        proceed, _ = check_accounting_risk_gate(ctx)
        assert proceed is True

    def test_yellow_proceeds(self):
        ctx = _make_ctx()
        ctx.set_result(
            "accounting_risk",
            AccountingRiskOutput(
                meta=_meta("accounting_risk"),
                risk_level=RiskLevel.YELLOW,
                major_accounting_changes=["Revenue recognition change"],
                comparability_impact="Moderate",
                credibility_concern="Explainable",
                stop_or_continue="Continue with caution",
            ),
        )
        proceed, _ = check_accounting_risk_gate(ctx)
        assert proceed is True

    def test_red_stops(self):
        ctx = _make_ctx()
        ctx.set_result(
            "accounting_risk",
            AccountingRiskOutput(
                meta=_meta("accounting_risk"),
                risk_level=RiskLevel.RED,
                major_accounting_changes=["Massive restatement"],
                comparability_impact="Severe",
                credibility_concern="Fraudulent reporting suspected",
                stop_or_continue="Stop",
            ),
        )
        proceed, reason = check_accounting_risk_gate(ctx)
        assert proceed is False
        assert "Fraudulent" in reason


class TestFinancialQualityGate:
    def _quality_scores(self) -> FinancialQualityScores:
        return FinancialQualityScores(
            per_share_growth=7,
            return_on_capital=8,
            cash_conversion=6,
            leverage_safety=7,
            capital_allocation=6,
            moat_financial_trace=7,
        )

    def test_pass_proceeds(self):
        ctx = _make_ctx()
        ctx.set_result(
            "financial_quality",
            FinancialQualityOutput(
                meta=_meta("financial_quality"),
                pass_minimum_standard=True,
                scores=self._quality_scores(),
                key_strengths=["High ROIC"],
                key_failures=[],
                should_continue="Yes",
            ),
        )
        proceed, _ = check_financial_quality_gate(ctx)
        assert proceed is True

    def test_average_continues_even_if_fail(self):
        """AVERAGE enterprises pass the gate even with pass_minimum_standard=False."""
        ctx = _make_ctx()
        ctx.set_result(
            "financial_quality",
            FinancialQualityOutput(
                meta=_meta("financial_quality"),
                pass_minimum_standard=False,
                enterprise_quality="AVERAGE",
                scores=self._quality_scores(),
                key_strengths=["Strong revenue growth"],
                key_failures=["Negative FCF from capex"],
                should_continue="Continue — investment phase",
            ),
        )
        proceed, _ = check_financial_quality_gate(ctx)
        assert proceed is True

    def test_poor_stops(self):
        """Only POOR enterprises are stopped by the financial quality gate."""
        ctx = _make_ctx()
        ctx.set_result(
            "financial_quality",
            FinancialQualityOutput(
                meta=_meta("financial_quality"),
                pass_minimum_standard=False,
                enterprise_quality="POOR",
                scores=self._quality_scores(),
                key_strengths=[],
                key_failures=["Declining EPS", "High leverage"],
                should_continue="No",
            ),
        )
        proceed, reason = check_financial_quality_gate(ctx)
        assert proceed is False
        assert "POOR" in reason
