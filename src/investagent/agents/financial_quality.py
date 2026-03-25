"""Financial Quality Agent — score financial health across six dimensions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.financial_quality import FinancialQualityOutput


class FinancialQualityAgent(BaseAgent):
    name: str = "financial_quality"

    def _output_type(self) -> type[BaseAgentOutput]:
        return FinancialQualityOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Financial Quality Agent. Your role is to evaluate the "
            "financial quality of a company across six scoring dimensions: "
            "per-share growth (EPS/FCF 5-year trends and dilution), return on "
            "capital (ROIC/ROE/ROA and margin stability), cash conversion "
            "(CFO/NI, FCF/NI, capex intensity), leverage safety (net debt/EBIT, "
            "interest coverage, liquidity), capital allocation (buyback quality, "
            "dividend sustainability, M&A track record), and moat financial traces "
            "(stable high margins/ROIC, scale effects). Each dimension is scored "
            "1-10. You determine whether the company passes the minimum quality "
            "standard required for further analysis. If it does not pass, the "
            "pipeline stops. You rely on structured financial data and must "
            "clearly distinguish between fact, inference, and unknown."
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
