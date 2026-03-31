"""CompanyIntake — pipeline entry point."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class CompanyIntake(BaseModel, frozen=True):
    ticker: str
    name: str
    exchange: str
    sector: str | None = None
    notes: str | None = None
    as_of_date: date | None = None  # backtest mode: use historical data as of this date
