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
    """Only POOR enterprises stop — unless strong qualitative signals override.

    Mental model agents run in parallel with financial_quality. If moat is WIDE
    or compounding is STRONG, the company may be in a strategic investment phase
    where poor current financials don't represent the true quality of the business.
    """
    result: FinancialQualityOutput = ctx.get_result("financial_quality")  # type: ignore[assignment]
    if result.enterprise_quality != "POOR":
        return True, ""

    # POOR financials — check if qualitative signals override
    qualitative_override = False
    override_reasons: list[str] = []

    try:
        moat = ctx.get_result("moat")
        if getattr(moat, "moat_rating", "") == "WIDE":
            qualitative_override = True
            override_reasons.append("moat_rating=WIDE")
    except (KeyError, AttributeError):
        pass

    try:
        compounding = ctx.get_result("compounding")
        if getattr(compounding, "compounding_quality", "") == "STRONG":
            qualitative_override = True
            override_reasons.append("compounding_quality=STRONG")
    except (KeyError, AttributeError):
        pass

    if qualitative_override:
        import logging

        logging.getLogger(__name__).info(
            "Financial quality POOR but qualitative override: %s — continuing pipeline",
            ", ".join(override_reasons),
        )
        return True, ""

    return False, f"Enterprise quality POOR: {', '.join(result.key_failures)}"
