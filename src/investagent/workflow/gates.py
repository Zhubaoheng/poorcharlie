"""Gate checks — should the pipeline continue or stop?"""

from __future__ import annotations

from investagent.schemas.accounting_risk import AccountingRiskOutput, RiskLevel
from investagent.schemas.financial_quality import FinancialQualityOutput
from investagent.schemas.triage import TriageDecision, TriageOutput
from investagent.workflow.context import PipelineContext


def check_triage_gate(ctx: PipelineContext) -> tuple[bool, str]:
    """REJECT -> stop pipeline. WATCH/PASS -> continue."""
    result: TriageOutput = ctx.get_result("triage")  # type: ignore[assignment]
    if result.decision == TriageDecision.REJECT:
        return False, f"Triage rejected: {result.why_it_is_or_is_not_coverable}"
    return True, ""


def check_accounting_risk_gate(ctx: PipelineContext) -> tuple[bool, str]:
    """RED -> stop pipeline."""
    result: AccountingRiskOutput = ctx.get_result("accounting_risk")  # type: ignore[assignment]
    if result.risk_level == RiskLevel.RED:
        return False, f"Accounting risk RED: {result.credibility_concern}"
    return True, ""


def check_financial_quality_gate(ctx: PipelineContext) -> tuple[bool, str]:
    """Only POOR enterprises stop the pipeline.

    AVERAGE and GREAT enterprises always continue to Critic/Committee,
    even if pass_minimum_standard is False — the mental model agents
    may have found strong moat/compounding/management signals that
    deserve full evaluation.
    """
    result: FinancialQualityOutput = ctx.get_result("financial_quality")  # type: ignore[assignment]
    if result.enterprise_quality == "POOR":
        return False, f"Enterprise quality POOR: {', '.join(result.key_failures)}"
    return True, ""
