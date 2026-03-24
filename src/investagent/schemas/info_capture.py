"""Info Capture Agent output schema."""

from __future__ import annotations

from pydantic import BaseModel

from investagent.schemas.common import BaseAgentOutput


class MarketSnapshot(BaseModel, frozen=True):
    price: float | None = None
    market_cap: float | None = None
    enterprise_value: float | None = None


class InfoCaptureOutput(BaseAgentOutput):
    company_profile: dict[str, str]
    filing_manifest: list[str]
    official_sources: list[str]
    trusted_third_party_sources: list[str]
    market_snapshot: MarketSnapshot
    missing_items: list[str]
