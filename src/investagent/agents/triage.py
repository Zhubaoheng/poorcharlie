"""Triage Agent — gate: is the company explainable from actual data?

Runs AFTER InfoCapture and Filing. Assesses explainability based on
real filing data, market snapshots, and structured financial tables —
not speculation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.triage import TriageOutput


def _build_filing_data_summary(filing_output: Any) -> dict[str, Any]:
    """Extract a compact summary from FilingOutput for the prompt context."""
    return {
        "years_covered": filing_output.filing_meta.fiscal_years_covered,
        "accounting_standard": filing_output.filing_meta.accounting_standard,
        "currency": filing_output.filing_meta.currency,
        "income_statement_years": len(filing_output.income_statement),
        "balance_sheet_years": len(filing_output.balance_sheet),
        "cash_flow_years": len(filing_output.cash_flow),
        "has_segments": len(filing_output.segments) > 0,
        "num_segments": len(filing_output.segments),
        "num_accounting_policies": len(filing_output.accounting_policies),
        "any_policy_changes": any(
            p.changed_from_prior for p in filing_output.accounting_policies
        ),
        "has_concentration_data": filing_output.concentration is not None,
        "num_risk_factors": len(filing_output.risk_factors),
        "risk_factor_categories": sorted(
            set(r.category for r in filing_output.risk_factors)
        ),
        "footnote_topics": sorted(
            set(f.topic for f in filing_output.footnote_extracts)
        ),
        "has_related_party_footnotes": any(
            "related_party" in f.topic for f in filing_output.footnote_extracts
        ),
        "num_debt_instruments": len(filing_output.debt_schedule),
        "num_special_items": len(filing_output.special_items),
    }


class TriageAgent(BaseAgent):
    name: str = "triage"

    def _output_type(self) -> type[BaseAgentOutput]:
        return TriageOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Triage Agent. Your role is to evaluate whether a company "
            "can be meaningfully analyzed based on the data that has already been "
            "gathered. You have access to the filing manifest, market snapshot, "
            "and structured financial data. You assess explainability across four "
            "dimensions: business model clarity, competition structure visibility, "
            "financial mapping fidelity, and key driver identifiability. "
            "You default to skepticism — only companies with clear, explainable "
            "businesses and sufficient data should pass through to deep analysis."
        )

    def _build_user_context(
        self, input_data: BaseModel, ctx: Any = None,
    ) -> dict[str, Any]:
        assert isinstance(input_data, CompanyIntake)
        result: dict[str, Any] = {
            "ticker": input_data.ticker,
            "name": input_data.name,
            "exchange": input_data.exchange,
            "sector": input_data.sector,
            "notes": input_data.notes,
        }

        # InfoCapture upstream data
        result["has_info_capture"] = False
        result["filing_refs"] = []
        result["market_snapshot"] = None
        result["company_profile"] = {}
        result["missing_items"] = []

        if ctx is not None:
            try:
                info = ctx.get_result("info_capture")
                result["has_info_capture"] = True
                result["filing_refs"] = info.filing_manifest
                result["market_snapshot"] = info.market_snapshot
                result["company_profile"] = info.company_profile
                result["missing_items"] = info.missing_items
            except KeyError:
                pass

        # Filing upstream data
        result["has_filing"] = False
        result["filing_data_summary"] = {}

        if ctx is not None:
            try:
                filing = ctx.get_result("filing")
                result["has_filing"] = True
                result["filing_data_summary"] = _build_filing_data_summary(filing)
            except KeyError:
                pass

        return result
