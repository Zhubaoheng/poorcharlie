"""Pre-screening Agent — lightweight LLM pass to filter the stock universe.

Sits before the full pipeline. Consumes pre-computed financial ratios
(from ratio_calc) and company basic info, outputs SKIP/PROCEED/SPECIAL_CASE.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.screener import ScreenerOutput


class ScreenerInput(BaseModel, frozen=True):
    """Input data for the screener agent."""

    ticker: str
    name: str
    industry: str = ""
    main_business: str = ""
    listing_date: str = ""
    market_cap: str = ""
    ratios: dict[str, list[float | None]] = {}


def _fmt_ratio_list(values: list[float | None]) -> str:
    """Format a list of ratio values for display."""
    parts = []
    for v in values:
        if v is None:
            parts.append("N/A")
        else:
            parts.append(f"{v:.2%}" if abs(v) < 10 else f"{v:.1f}")
    return " → ".join(parts)


class ScreenerAgent(BaseAgent):
    """Lightweight pre-screening agent."""

    name: str = "screener"

    def _output_type(self) -> type[BaseAgentOutput]:
        return ScreenerOutput

    def _agent_role_description(self) -> str:
        return (
            "你是预筛选代理，代表芒格做第一轮快速筛选。"
            "大多数公司不值得深入研究。只有看到明确的质量信号（高回报、强现金流、竞争优势痕迹）才放行。"
            "默认判定是 SKIP。"
        )

    def _build_user_context(
        self, input_data: BaseModel, ctx: Any = None,
    ) -> dict[str, Any]:
        data = input_data if isinstance(input_data, ScreenerInput) else ScreenerInput.model_validate(input_data)
        ratios = data.ratios
        fiscal_years = ratios.get("fiscal_years", [])

        context: dict[str, Any] = {
            "ticker": data.ticker,
            "name": data.name,
            "industry": data.industry or "未知",
            "main_business": data.main_business or "未知",
            "listing_date": data.listing_date or "未知",
            "market_cap": data.market_cap or "未知",
            "num_years": len(fiscal_years),
            "fiscal_years": " / ".join(str(y) for y in fiscal_years),
        }

        # Format each ratio group
        ratio_keys = [
            "roe", "roic", "gross_margin", "net_margin",
            "revenue_growth", "net_income_growth", "eps_growth",
            "ocf_to_ni", "fcf_to_ni", "capex_to_revenue",
            "debt_to_assets", "net_debt_to_ebit", "interest_coverage",
            "pe_ttm", "pb", "dividend_yield",
        ]
        for key in ratio_keys:
            values = ratios.get(key, [])
            context[key] = _fmt_ratio_list(values) if values else "N/A"

        return context
