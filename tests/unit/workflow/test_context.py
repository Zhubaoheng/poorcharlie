"""Tests for investagent.workflow.context."""

from investagent.schemas.company import CompanyIntake
from investagent.workflow.context import PipelineContext


def test_pipeline_context_init():
    intake = CompanyIntake(ticker="AAPL", name="Apple Inc.", exchange="NASDAQ")
    ctx = PipelineContext(intake=intake)
    assert ctx.intake.ticker == "AAPL"
    assert ctx.stopped is False
    assert ctx.stop_reason is None
