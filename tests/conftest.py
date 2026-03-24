"""Shared fixtures for investagent tests."""

import json
from pathlib import Path

import pytest

from investagent.schemas.company import CompanyIntake

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_intake() -> CompanyIntake:
    raw = json.loads((FIXTURES_DIR / "sample_intake.json").read_text())
    return CompanyIntake(**raw)
