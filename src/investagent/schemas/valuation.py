"""Valuation & Look-through Return Agent output schema."""

from __future__ import annotations

from pydantic import BaseModel

from investagent.schemas.common import BaseAgentOutput


class ScenarioReturns(BaseModel, frozen=True):
    bear: float | None = None
    base: float | None = None
    bull: float | None = None


class ValuationOutput(BaseAgentOutput):
    valuation_method: list[str]
    expected_lookthrough_return: ScenarioReturns
    friction_adjusted_return: ScenarioReturns
    meets_hurdle_rate: bool
    notes: list[str]
