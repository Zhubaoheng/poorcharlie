"""Accounting Risk Agent — detect accounting method changes, rate GREEN/YELLOW/RED."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.accounting_risk import AccountingRiskOutput


class AccountingRiskAgent(BaseAgent):
    name: str = "accounting_risk"

    def _output_type(self) -> type[BaseAgentOutput]:
        return AccountingRiskOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Accounting Risk Agent. Your role is to detect accounting "
            "method changes, aggressive accounting practices, and credibility "
            "concerns across a company's recent filings. You examine 10 specific "
            "risk items: revenue recognition changes, consolidation scope changes, "
            "segment disclosure changes, depreciation policy changes, inventory "
            "valuation changes, bad debt provision changes, one-time item "
            "normalization, non-GAAP metrics aggressiveness, audit opinion changes, "
            "and restatements. You rate overall risk as GREEN (no major changes), "
            "YELLOW (changes present but explainable), or RED (major changes "
            "affecting credibility — pipeline must stop). You default to caution: "
            "when in doubt, escalate rather than dismiss."
        )

    def _build_user_context(self, input_data: BaseModel, ctx: Any = None) -> dict[str, Any]:
        assert isinstance(input_data, CompanyIntake)
        result: dict[str, Any] = {
            "ticker": input_data.ticker,
            "name": input_data.name,
            "exchange": input_data.exchange,
        }
        # Add filing data summary from context if available
        if ctx is not None:
            try:
                filing = ctx.get_result("filing")
                result["has_filing_data"] = True
                # Add relevant filing summary fields
            except KeyError:
                result["has_filing_data"] = False
        else:
            result["has_filing_data"] = False
        return result
