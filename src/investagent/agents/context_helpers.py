"""Per-agent data selectors — each agent gets only what it needs.

All extracted data is stored in PipelineContext. These helpers build
tailored data packages for each agent, avoiding token waste.
"""

from __future__ import annotations

import json
from typing import Any


def _safe_get(ctx: Any, name: str) -> Any:
    try:
        return ctx.get_result(name)
    except (KeyError, AttributeError):
        return None


def _safe_data(ctx: Any, key: str) -> Any:
    try:
        return ctx.get_data(key)
    except (KeyError, AttributeError):
        return None


def _get_filing(ctx: Any) -> Any:
    filing = _safe_get(ctx, "filing")
    if filing is None or not hasattr(filing, "filing_meta"):
        return None
    return filing


def _get_market_snapshot(ctx: Any) -> dict[str, Any] | None:
    info = _safe_get(ctx, "info_capture")
    if info is None or not hasattr(info, "market_snapshot"):
        return None
    ms = info.market_snapshot
    return {
        "price": ms.price,
        "market_cap": ms.market_cap,
        "enterprise_value": ms.enterprise_value,
        "pe_ratio": ms.pe_ratio,
        "pb_ratio": ms.pb_ratio,
        "dividend_yield": ms.dividend_yield,
        "currency": ms.currency,
    }


def _compact_rows(rows: list, fy_only: bool = False) -> list[dict]:
    """Strip None values from row dicts to save tokens.

    If fy_only=True, filter to only FY (full-year) rows — avoids
    confusing downstream agents with H1/Q2 partial-year data.
    """
    filtered = rows
    if fy_only:
        filtered = [r for r in rows if getattr(r, 'fiscal_period', 'FY') == 'FY']
    return [{k: v for k, v in r.model_dump().items() if v is not None} for r in filtered]


def _get_mda(ctx: Any, max_full_years: int = 2, max_chars_per_old: int = 8000) -> dict[str, str]:
    """Get MD&A text, with older years truncated to save context.

    Latest max_full_years get full text. Older years are truncated to
    max_chars_per_old chars with a note.
    """
    raw = _safe_data(ctx, "mda_by_year") or {}
    if not raw:
        return {}

    # Sort by year key (descending = newest first)
    sorted_keys = sorted(raw.keys(), reverse=True)
    result: dict[str, str] = {}

    for i, key in enumerate(sorted_keys):
        text = raw[key]
        if i < max_full_years:
            result[key] = text  # full text for newest years
        else:
            if len(text) > max_chars_per_old:
                result[key] = text[:max_chars_per_old] + f"\n\n... (截断：仅保留前 {max_chars_per_old} 字符，完整内容请查阅原始报告)"
            else:
                result[key] = text

    return result


def format_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# Per-agent data builders
# ---------------------------------------------------------------------------

def data_for_accounting_risk(ctx: Any) -> dict[str, Any]:
    """Accounting policies + footnotes + special items + audit + MD&A."""
    filing = _get_filing(ctx)
    if not filing:
        return {"has_filing": False}
    # Collect audit sections from raw extractions
    raw_sections = _safe_data(ctx, "raw_sections_by_year") or {}
    audit_text = ""
    non_ifrs_text = ""
    for year_key, sections in raw_sections.items():
        if "audit" in sections:
            audit_text += f"\n\n[{year_key}]\n{sections['audit']}"
        for k in ("non_ifrs", "non_gaap"):
            if k in sections:
                non_ifrs_text += f"\n\n[{year_key}]\n{sections[k]}"
    return {
        "has_filing": True,
        "filing_meta": {"accounting_standard": filing.filing_meta.accounting_standard,
                        "currency": filing.filing_meta.currency,
                        "years_covered": filing.filing_meta.fiscal_years_covered},
        "accounting_policies": [p.model_dump() for p in filing.accounting_policies],
        "footnote_extracts": [f.model_dump() for f in filing.footnote_extracts],
        "special_items": [s.model_dump() for s in filing.special_items],
        "income_statement": _compact_rows(filing.income_statement, fy_only=True),
        "audit": audit_text.strip() if audit_text else None,
        "non_ifrs_adjustments": non_ifrs_text.strip() if non_ifrs_text else None,
        "notes_tax": _get_notes(ctx, ["notes_tax"]),
        "notes_goodwill": _get_notes(ctx, ["notes_goodwill_intangibles"]),
        "notes_contingencies": _get_notes(ctx, ["notes_contingencies"]),
        "mda": _get_mda(ctx),
    }


def data_for_financial_quality(ctx: Any) -> dict[str, Any]:
    """Three statements + segments + market snapshot."""
    filing = _get_filing(ctx)
    if not filing:
        return {"has_filing": False}
    return {
        "has_filing": True,
        "filing_meta": {"currency": filing.filing_meta.currency,
                        "market_currency": filing.filing_meta.market_currency,
                        "years_covered": filing.filing_meta.fiscal_years_covered},
        "income_statement": _compact_rows(filing.income_statement, fy_only=True),
        "balance_sheet": _compact_rows(filing.balance_sheet, fy_only=True),
        "cash_flow": _compact_rows(filing.cash_flow, fy_only=True),
        "segments": _compact_rows(filing.segments),
        "buyback_history": [b.model_dump() for b in filing.buyback_history],
        "dividend_per_share_history": filing.dividend_per_share_history,
        "market_snapshot": _get_market_snapshot(ctx),
    }


def _get_notes(ctx: Any, note_keys: list[str], max_full_years: int = 2, max_chars_per_old: int = 8000) -> dict[str, str]:
    """Extract specific notes from raw sections.

    Latest max_full_years get full text. Older years truncated.
    """
    raw = _safe_data(ctx, "raw_sections_by_year") or {}
    if not raw:
        return {}

    sorted_years = sorted(raw.keys(), reverse=True)
    result: dict[str, str] = {}

    for i, year_key in enumerate(sorted_years):
        sections = raw[year_key]
        for nk in note_keys:
            if nk in sections:
                text = sections[nk]
                if i >= max_full_years and len(text) > max_chars_per_old:
                    text = text[:max_chars_per_old] + "\n\n... (旧年截断)"
                result[f"{year_key}/{nk}"] = text

    return result


def data_for_net_cash(ctx: Any) -> dict[str, Any]:
    """Balance sheet + debt + cash flow + notes_borrowings + market snapshot."""
    filing = _get_filing(ctx)
    if not filing:
        return {"has_filing": False}
    return {
        "has_filing": True,
        "filing_meta": {"currency": filing.filing_meta.currency,
                        "market_currency": filing.filing_meta.market_currency},
        "balance_sheet": _compact_rows(filing.balance_sheet, fy_only=True),
        "cash_flow": _compact_rows(filing.cash_flow, fy_only=True),
        "debt_schedule": [d.model_dump() for d in filing.debt_schedule],
        "buyback_history": [b.model_dump() for b in filing.buyback_history],
        "dividend_per_share_history": filing.dividend_per_share_history,
        "footnote_extracts": [f.model_dump() for f in filing.footnote_extracts
                              if f.topic in ("debt", "pledged_assets", "related_party")],
        "notes_borrowings": _get_notes(ctx, ["notes_borrowings", "liquidity"]),
        "market_snapshot": _get_market_snapshot(ctx),
    }


def data_for_valuation(ctx: Any) -> dict[str, Any]:
    """Income + cash flow + market snapshot."""
    filing = _get_filing(ctx)
    if not filing:
        return {"has_filing": False}
    return {
        "has_filing": True,
        "filing_meta": {"currency": filing.filing_meta.currency,
                        "market_currency": filing.filing_meta.market_currency,
                        "years_covered": filing.filing_meta.fiscal_years_covered},
        "income_statement": _compact_rows(filing.income_statement, fy_only=True),
        "cash_flow": _compact_rows(filing.cash_flow, fy_only=True),
        "balance_sheet": _compact_rows(filing.balance_sheet, fy_only=True),
        "segments": _compact_rows(filing.segments),
        "market_snapshot": _get_market_snapshot(ctx),
    }


def data_for_moat(ctx: Any) -> dict[str, Any]:
    """Segments + income trends + concentration + MD&A."""
    filing = _get_filing(ctx)
    if not filing:
        return {"has_filing": False}
    return {
        "has_filing": True,
        "income_statement": _compact_rows(filing.income_statement, fy_only=True),
        "segments": _compact_rows(filing.segments),
        "concentration": filing.concentration.model_dump() if filing.concentration else None,
        "risk_factors": [r.model_dump() for r in filing.risk_factors],
        "mda": _get_mda(ctx),
    }


def data_for_compounding(ctx: Any) -> dict[str, Any]:
    """Three statements + segments for ROIC/reinvestment analysis."""
    filing = _get_filing(ctx)
    if not filing:
        return {"has_filing": False}
    return {
        "has_filing": True,
        "income_statement": _compact_rows(filing.income_statement, fy_only=True),
        "balance_sheet": _compact_rows(filing.balance_sheet, fy_only=True),
        "cash_flow": _compact_rows(filing.cash_flow, fy_only=True),
        "segments": _compact_rows(filing.segments),
        "buyback_history": [b.model_dump() for b in filing.buyback_history],
        "dividend_per_share_history": filing.dividend_per_share_history,
        "mda": _get_mda(ctx),
    }


def data_for_psychology(ctx: Any) -> dict[str, Any]:
    """MD&A + management compensation + insider interests + buyback signals."""
    filing = _get_filing(ctx)
    if not filing:
        return {"has_filing": False}
    mda = _get_mda(ctx)
    # Extract remuneration and directors' interests from raw sections
    raw_sections = _safe_data(ctx, "raw_sections_by_year") or {}
    remuneration_text = ""
    directors_interests_text = ""
    for year_key, sections in raw_sections.items():
        if "remuneration" in sections:
            remuneration_text += f"\n\n[{year_key}]\n{sections['remuneration']}"
        if "directors_interests" in sections:
            directors_interests_text += f"\n\n[{year_key}]\n{sections['directors_interests']}"
    return {
        "has_filing": True,
        "income_statement": _compact_rows(filing.income_statement, fy_only=True),
        "cash_flow": _compact_rows(filing.cash_flow, fy_only=True),
        "buyback_history": [b.model_dump() for b in filing.buyback_history],
        "acquisition_history": [a.model_dump() for a in filing.acquisition_history],
        "special_items": [s.model_dump() for s in filing.special_items],
        "mda": mda,
        "remuneration": remuneration_text.strip() if remuneration_text else None,
        "directors_interests": directors_interests_text.strip() if directors_interests_text else None,
        "market_snapshot": _get_market_snapshot(ctx),
    }


def data_for_systems(ctx: Any) -> dict[str, Any]:
    """Concentration + debt + balance sheet + risk factors."""
    filing = _get_filing(ctx)
    if not filing:
        return {"has_filing": False}
    return {
        "has_filing": True,
        "balance_sheet": _compact_rows(filing.balance_sheet, fy_only=True),
        "cash_flow": _compact_rows(filing.cash_flow, fy_only=True),
        "debt_schedule": [d.model_dump() for d in filing.debt_schedule],
        "concentration": filing.concentration.model_dump() if filing.concentration else None,
        "segments": _compact_rows(filing.segments),
        "risk_factors": [r.model_dump() for r in filing.risk_factors],
        "footnote_extracts": [f.model_dump() for f in filing.footnote_extracts],
        "mda": _get_mda(ctx),
    }


def data_for_ecology(ctx: Any) -> dict[str, Any]:
    """Income trends + segments + risk factors + MD&A."""
    filing = _get_filing(ctx)
    if not filing:
        return {"has_filing": False}
    return {
        "has_filing": True,
        "income_statement": _compact_rows(filing.income_statement, fy_only=True),
        "segments": _compact_rows(filing.segments),
        "risk_factors": [r.model_dump() for r in filing.risk_factors],
        "mda": _get_mda(ctx),
    }


# ---------------------------------------------------------------------------
# Critic / Committee — need everything
# ---------------------------------------------------------------------------

def serialize_upstream_for_committee(ctx: Any) -> dict[str, Any]:
    """All upstream agent outputs for Critic/Committee synthesis."""
    result: dict[str, Any] = {}

    triage = _safe_get(ctx, "triage")
    if triage:
        result["triage"] = {
            "decision": triage.decision.value,
            "scores": triage.explainability_score.model_dump(),
            "fatal_unknowns": triage.fatal_unknowns,
        }

    filing = _get_filing(ctx)
    if filing:
        result["filing_years"] = filing.filing_meta.fiscal_years_covered
        result["filing_standard"] = filing.filing_meta.accounting_standard

    acct = _safe_get(ctx, "accounting_risk")
    if acct:
        result["accounting_risk"] = {
            "risk_level": acct.risk_level,
            "major_changes": acct.major_accounting_changes,
            "credibility": acct.credibility_concern,
        }

    fq = _safe_get(ctx, "financial_quality")
    if fq:
        result["financial_quality"] = {
            "pass": fq.pass_minimum_standard,
            "enterprise_quality": fq.enterprise_quality,
            "scores": fq.scores.model_dump(),
            "strengths": fq.key_strengths,
            "failures": fq.key_failures,
        }

    nc = _safe_get(ctx, "net_cash")
    if nc:
        result["net_cash"] = {
            "net_cash": nc.net_cash,
            "ratio": nc.net_cash_to_market_cap,
            "attention": nc.attention_level,
            "cash_quality": nc.cash_quality_notes,
        }

    val = _safe_get(ctx, "valuation")
    if val:
        result["valuation"] = {
            "methods": val.valuation_method,
            "returns": val.expected_lookthrough_return.model_dump(),
            "friction_adjusted": val.friction_adjusted_return.model_dump(),
            "meets_hurdle": val.meets_hurdle_rate,
        }

    for name in ("moat", "compounding", "psychology", "systems", "ecology"):
        mm = _safe_get(ctx, name)
        if mm:
            result[name] = mm.model_dump(exclude={"meta", "stop_signal"}, mode="json")

    critic = _safe_get(ctx, "critic")
    if critic:
        result["critic"] = {
            "kill_shots": critic.kill_shots,
            "permanent_loss_risks": critic.permanent_loss_risks,
            "moat_destruction": critic.moat_destruction_paths,
            "management_failures": critic.management_failure_modes,
        }

    info = _safe_get(ctx, "info_capture")
    if info:
        result["market_snapshot"] = _get_market_snapshot(ctx)
        result["company_profile"] = info.company_profile

    # MD&A for critic/committee too
    mda = _get_mda(ctx)
    if mda:
        result["mda"] = mda

    return result


def data_for_critic(ctx: Any) -> dict[str, Any]:
    """Filing data + all upstream agent conclusions + MD&A."""
    filing = _get_filing(ctx)
    filing_data: dict[str, Any] = {"has_filing": False}
    if filing:
        filing_data = {
            "has_filing": True,
            "income_statement": _compact_rows(filing.income_statement, fy_only=True),
            "balance_sheet": _compact_rows(filing.balance_sheet, fy_only=True),
            "cash_flow": _compact_rows(filing.cash_flow, fy_only=True),
            "segments": _compact_rows(filing.segments),
            "risk_factors": [r.model_dump() for r in filing.risk_factors],
            "special_items": [s.model_dump() for s in filing.special_items],
            "concentration": filing.concentration.model_dump() if filing.concentration else None,
        }
    return {
        "filing": filing_data,
        "upstream": serialize_upstream_for_committee(ctx),
    }


def data_for_committee(ctx: Any) -> dict[str, Any]:
    """All upstream agent outputs synthesized."""
    return serialize_upstream_for_committee(ctx)
