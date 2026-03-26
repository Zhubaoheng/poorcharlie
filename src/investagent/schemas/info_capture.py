"""Info Capture Agent output schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.schemas.common import BaseAgentOutput


class MarketSnapshot(BaseModel, frozen=True):
    price: float | None = None
    market_cap: float | None = None
    enterprise_value: float | None = None
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    dividend_yield: float | None = None
    currency: str | None = None


class FilingRef(BaseModel, frozen=True):
    """A reference to a filing discovered during info capture."""

    filing_type: str       # "20-F" | "年报" | "Annual Report" | etc.
    fiscal_year: str       # "2024"
    fiscal_period: str     # "FY" | "H1"
    filing_date: str       # ISO date
    source_url: str
    content_type: str      # "pdf" | "html" | "xbrl"


class InfoCaptureOutput(BaseAgentOutput):
    company_profile: dict[str, Any]
    filing_manifest: list[FilingRef]
    official_sources: list[str]
    trusted_third_party_sources: list[str]
    market_snapshot: MarketSnapshot
    missing_items: list[str]
