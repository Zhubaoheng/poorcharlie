"""Candidate pool and portfolio state schemas for Part 2 decision pipeline."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel


class CandidateState(str, Enum):
    ANALYZED = "ANALYZED"
    COMPARED = "COMPARED"
    HELD = "HELD"
    EXITED = "EXITED"
    STALE = "STALE"


class CandidateSnapshot(BaseModel, frozen=True):
    """One company's analysis summary, extracted from Part 1 pipeline output."""

    ticker: str
    name: str = ""
    industry: str = ""
    final_label: str  # FinalLabel value from CommitteeOutput
    enterprise_quality: str = ""
    price_vs_value: str = ""
    margin_of_safety_pct: float | None = None
    meets_hurdle_rate: bool = False
    thesis: str = ""
    anti_thesis: str = ""
    largest_unknowns: list[str] = []
    expected_return_summary: str = ""
    why_now: str = ""
    scan_date: date
    state: CandidateState = CandidateState.ANALYZED
    # Valuation trigger fields (for price-based re-evaluation)
    intrinsic_value_base: float | None = None     # base IV (median of method estimates)
    scan_close_price: float | None = None          # qfq close price at scan date
    valuation_trigger_ratio: float | None = None   # (base_iv * 0.8) / scan_close


class PortfolioHolding(BaseModel, frozen=True):
    """One position in the current portfolio."""

    ticker: str
    name: str = ""
    industry: str = ""
    target_weight: float
    entry_date: date
    entry_reason: str = ""


class StoreState(BaseModel):
    """Top-level serialization target for the CandidateStore JSON file."""

    last_updated: datetime
    candidates: dict[str, CandidateSnapshot] = {}  # ticker -> latest snapshot
    holdings: list[PortfolioHolding] = []
    scan_history: list[date] = []
