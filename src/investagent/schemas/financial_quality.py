"""Financial Quality Agent output schema."""

from __future__ import annotations

from pydantic import BaseModel

from investagent.schemas.common import BaseAgentOutput


class FinancialQualityScores(BaseModel, frozen=True):
    per_share_growth: int
    return_on_capital: int
    cash_conversion: int
    leverage_safety: int
    capital_allocation: int
    moat_financial_trace: int


class FinancialQualityOutput(BaseAgentOutput):
    pass_minimum_standard: bool
    scores: FinancialQualityScores
    key_strengths: list[str]
    key_failures: list[str]
    should_continue: str
