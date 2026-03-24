"""Accounting Risk Agent output schema."""

from __future__ import annotations

from enum import Enum

from investagent.schemas.common import BaseAgentOutput


class RiskLevel(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class AccountingRiskOutput(BaseAgentOutput):
    risk_level: RiskLevel
    major_accounting_changes: list[str]
    comparability_impact: str
    credibility_concern: str
    stop_or_continue: str
