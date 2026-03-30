"""Valuation & Look-through Return Agent output schema."""

from __future__ import annotations

from pydantic import BaseModel

from investagent.schemas.common import BaseAgentOutput


class ScenarioReturns(BaseModel, frozen=True):
    bear: float | None = None
    base: float | None = None
    bull: float | None = None


class IntrinsicValueRange(BaseModel, frozen=True):
    """Per-share intrinsic value estimates under three scenarios."""
    bear: float | None = None
    base: float | None = None
    bull: float | None = None
    currency: str = ""


class ValuationOutput(BaseAgentOutput):
    valuation_method: list[str]
    expected_lookthrough_return: ScenarioReturns
    friction_adjusted_return: ScenarioReturns
    meets_hurdle_rate: bool
    intrinsic_value_per_share: IntrinsicValueRange | None = None
    margin_of_safety_pct: float | None = None  # (base_value - price) / base_value
    price_vs_value: str = ""  # "CHEAP" | "FAIR" | "EXPENSIVE"
    key_assumptions: list[str] = []
    sensitivity_drivers: list[str] = []
    notes: list[str] = []
