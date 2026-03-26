"""Triage Agent output schema."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from investagent.schemas.common import BaseAgentOutput


class TriageDecision(str, Enum):
    PASS = "PASS"
    WATCH = "WATCH"
    REJECT = "REJECT"


class ExplainabilityScore(BaseModel, frozen=True):
    business_model: int
    competition_structure: int
    financial_mapping: int
    key_drivers: int


class TriageOutput(BaseAgentOutput):
    decision: TriageDecision
    explainability_score: ExplainabilityScore
    fatal_unknowns: list[str]
    why_it_is_or_is_not_coverable: str
    next_step: str
    data_availability_summary: str | None = None
