"""Portfolio Construction Agent output schema."""

from __future__ import annotations

from pydantic import BaseModel

from investagent.schemas.common import BaseAgentOutput


class PortfolioAllocation(BaseModel, frozen=True):
    ticker: str
    name: str = ""
    target_weight: float  # 0.05 ~ 0.30
    reason: str = ""


class PortfolioOutput(BaseAgentOutput):
    allocations: list[PortfolioAllocation]
    cash_weight: float  # remaining cash proportion
    industry_distribution: dict[str, float] = {}  # industry -> total weight
    rebalance_actions: list[str] = []  # human-readable action descriptions
