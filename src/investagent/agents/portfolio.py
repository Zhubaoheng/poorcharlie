"""Portfolio Construction Agent — select stocks and allocate positions.

Terminal pipeline agent that runs after all individual company analyses
complete. Takes INVESTABLE candidates and current holdings, outputs a
target portfolio with position weights.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.portfolio import PortfolioOutput


class HoldingInfo(BaseModel, frozen=True):
    ticker: str
    name: str = ""
    weight: float = 0.0
    industry: str = ""


class CandidateInfo(BaseModel, frozen=True):
    ticker: str
    name: str = ""
    industry: str = ""
    enterprise_quality: str = ""  # "GREAT" | "AVERAGE" | "POOR"
    price_vs_value: str = ""  # "CHEAP" | "FAIR" | "EXPENSIVE"
    margin_of_safety_pct: float | None = None
    meets_hurdle_rate: bool = False
    thesis: str = ""


class PortfolioInput(BaseModel, frozen=True):
    candidates: list[CandidateInfo] = []
    current_holdings: list[HoldingInfo] = []
    available_cash_pct: float = 1.0


class PortfolioAgent(BaseAgent):
    """Selects stocks and allocates position weights."""

    name: str = "portfolio"

    def _output_type(self) -> type[BaseAgentOutput]:
        return PortfolioOutput

    def _agent_role_description(self) -> str:
        return (
            "你是组合构建代理，负责从通过投资分析流水线的标的中选股并分配仓位。"
            "你遵循芒格集中投资原则：买最好的几家企业，以合理价格买入伟大企业，"
            "适度行业分散，不够好的主意就持有现金。"
        )

    def _build_user_context(
        self, input_data: BaseModel, ctx: Any = None,
    ) -> dict[str, Any]:
        data = input_data if isinstance(input_data, PortfolioInput) else PortfolioInput.model_validate(input_data)

        candidates = []
        for c in data.candidates:
            mos = f"{c.margin_of_safety_pct:.0%}" if c.margin_of_safety_pct is not None else "N/A"
            candidates.append({
                "ticker": c.ticker,
                "name": c.name,
                "industry": c.industry or "未知",
                "enterprise_quality": c.enterprise_quality or "未知",
                "price_vs_value": c.price_vs_value or "未知",
                "margin_of_safety_pct": mos,
                "meets_hurdle_rate": "是" if c.meets_hurdle_rate else "否",
                "thesis": c.thesis or "无",
            })

        holdings = []
        for h in data.current_holdings:
            holdings.append({
                "ticker": h.ticker,
                "name": h.name,
                "weight": f"{h.weight:.0%}",
                "industry": h.industry or "未知",
            })

        return {
            "candidates": candidates,
            "current_holdings": holdings,
            "available_cash_pct": f"{data.available_cash_pct:.0%}",
        }
