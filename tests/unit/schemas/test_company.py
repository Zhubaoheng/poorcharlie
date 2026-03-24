"""Tests for investagent.schemas.company."""

import pytest
from pydantic import ValidationError

from investagent.schemas.company import CompanyIntake


def test_company_intake_required_fields():
    intake = CompanyIntake(ticker="AAPL", name="Apple Inc.", exchange="NASDAQ")
    assert intake.ticker == "AAPL"
    assert intake.sector is None
    assert intake.notes is None


def test_company_intake_all_fields(sample_intake):
    assert sample_intake.ticker == "AAPL"
    assert sample_intake.sector == "Technology"


def test_company_intake_missing_required_field():
    with pytest.raises(ValidationError):
        CompanyIntake(ticker="AAPL")  # type: ignore[call-arg]
