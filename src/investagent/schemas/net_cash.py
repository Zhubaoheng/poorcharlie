"""Net Cash & Capital Return Agent output schema."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from investagent.schemas.common import BaseAgentOutput


class AttentionLevel(str, Enum):
    NORMAL = "NORMAL"
    WATCH = "WATCH"
    PRIORITY = "PRIORITY"
    HIGH_PRIORITY = "HIGH_PRIORITY"


class DividendProfile(BaseModel, frozen=True):
    pays_dividend: bool = False
    coverage_ratio: float | None = None


class BuybackProfile(BaseModel, frozen=True):
    has_buyback: bool = False
    shares_reduced: bool = False


class NetCashOutput(BaseAgentOutput):
    net_cash: float | None = None
    net_cash_to_market_cap: float | None = None
    attention_level: AttentionLevel
    dividend_profile: DividendProfile
    buyback_profile: BuybackProfile
    cash_quality_notes: list[str]
