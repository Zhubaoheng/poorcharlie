"""Shared helpers for serializing upstream agent outputs into prompt context.

Each downstream agent needs different slices of the upstream data.
These helpers extract and format the relevant fields, controlling token usage.
"""

from __future__ import annotations

import json
from typing import Any


def _safe_get(ctx: Any, name: str) -> Any:
    """Get a result from context, return None if missing."""
    try:
        return ctx.get_result(name)
    except (KeyError, AttributeError):
        return None


def serialize_filing_for_prompt(ctx: Any) -> dict[str, Any]:
    """Serialize FilingOutput into a compact dict for downstream agent prompts."""
    filing = _safe_get(ctx, "filing")
    if filing is None:
        return {"has_filing": False}

    # Guard against non-FilingOutput objects (e.g., mocks in tests)
    if not hasattr(filing, "filing_meta"):
        return {"has_filing": False}

    result: dict[str, Any] = {"has_filing": True}

    # Meta
    m = filing.filing_meta
    result["filing_meta"] = {
        "market": m.market,
        "accounting_standard": m.accounting_standard,
        "years_covered": m.fiscal_years_covered,
        "currency": m.currency,
    }

    # Income statement (compact: one dict per year)
    result["income_statement"] = [
        {k: v for k, v in row.model_dump().items() if v is not None}
        for row in filing.income_statement
    ]

    # Balance sheet (compact)
    result["balance_sheet"] = [
        {k: v for k, v in row.model_dump().items() if v is not None}
        for row in filing.balance_sheet
    ]

    # Cash flow (compact)
    result["cash_flow"] = [
        {k: v for k, v in row.model_dump().items() if v is not None}
        for row in filing.cash_flow
    ]

    # Segments
    result["segments"] = [
        {k: v for k, v in row.model_dump().items() if v is not None}
        for row in filing.segments
    ]

    # Accounting policies (preserve raw_text for accounting_risk)
    result["accounting_policies"] = [
        p.model_dump() for p in filing.accounting_policies
    ]

    # Debt
    result["debt_schedule"] = [d.model_dump() for d in filing.debt_schedule]

    # Special items
    result["special_items"] = [s.model_dump() for s in filing.special_items]

    # Concentration
    result["concentration"] = filing.concentration.model_dump() if filing.concentration else None

    # Capital allocation
    result["buyback_history"] = [b.model_dump() for b in filing.buyback_history]
    result["acquisition_history"] = [a.model_dump() for a in filing.acquisition_history]
    result["dividend_per_share_history"] = filing.dividend_per_share_history

    # Footnotes (preserve raw_text)
    result["footnote_extracts"] = [f.model_dump() for f in filing.footnote_extracts]

    # Risk factors
    result["risk_factors"] = [r.model_dump() for r in filing.risk_factors]

    return result


def serialize_upstream_for_committee(ctx: Any) -> dict[str, Any]:
    """Serialize all upstream agent outputs for Critic/Committee agents."""
    result: dict[str, Any] = {}

    # Triage
    triage = _safe_get(ctx, "triage")
    if triage:
        result["triage"] = {
            "decision": triage.decision.value,
            "scores": triage.explainability_score.model_dump(),
            "fatal_unknowns": triage.fatal_unknowns,
        }

    # Filing summary (compact)
    filing = _safe_get(ctx, "filing")
    if filing:
        result["filing_years"] = filing.filing_meta.fiscal_years_covered
        result["filing_standard"] = filing.filing_meta.accounting_standard

    # Accounting risk
    acct = _safe_get(ctx, "accounting_risk")
    if acct:
        result["accounting_risk"] = {
            "risk_level": acct.risk_level,
            "major_changes": acct.major_accounting_changes,
            "credibility": acct.credibility_concern,
        }

    # Financial quality
    fq = _safe_get(ctx, "financial_quality")
    if fq:
        result["financial_quality"] = {
            "pass": fq.pass_minimum_standard,
            "scores": fq.scores.model_dump(),
            "strengths": fq.key_strengths,
            "failures": fq.key_failures,
        }

    # Net cash
    nc = _safe_get(ctx, "net_cash")
    if nc:
        result["net_cash"] = {
            "net_cash": nc.net_cash,
            "ratio": nc.net_cash_to_market_cap,
            "attention": nc.attention_level,
            "cash_quality": nc.cash_quality_notes,
        }

    # Valuation
    val = _safe_get(ctx, "valuation")
    if val:
        result["valuation"] = {
            "methods": val.valuation_method,
            "returns": val.expected_lookthrough_return.model_dump(),
            "friction_adjusted": val.friction_adjusted_return.model_dump(),
            "meets_hurdle": val.meets_hurdle_rate,
        }

    # Mental models
    for name in ("moat", "compounding", "psychology", "systems", "ecology"):
        mm = _safe_get(ctx, name)
        if mm:
            result[name] = mm.model_dump(exclude={"meta", "stop_signal"}, mode="json")

    # Critic
    critic = _safe_get(ctx, "critic")
    if critic:
        result["critic"] = {
            "kill_shots": critic.kill_shots,
            "permanent_loss_risks": critic.permanent_loss_risks,
            "moat_destruction": critic.moat_destruction_paths,
            "management_failures": critic.management_failure_modes,
        }

    # Market snapshot from info_capture
    info = _safe_get(ctx, "info_capture")
    if info:
        result["market_snapshot"] = info.market_snapshot.model_dump()
        result["company_profile"] = info.company_profile

    return result


def format_filing_json(data: dict[str, Any], max_chars: int = 30000) -> str:
    """Format filing data as JSON string, truncated if needed."""
    text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (截断)"
    return text
