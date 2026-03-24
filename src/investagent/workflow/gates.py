"""Gate checks — should the pipeline continue or stop?"""

from __future__ import annotations

from investagent.workflow.context import PipelineContext


def check_triage_gate(ctx: PipelineContext) -> tuple[bool, str]:
    """REJECT -> stop pipeline."""
    raise NotImplementedError


def check_accounting_risk_gate(ctx: PipelineContext) -> tuple[bool, str]:
    """RED -> stop pipeline."""
    raise NotImplementedError


def check_financial_quality_gate(ctx: PipelineContext) -> tuple[bool, str]:
    """pass_minimum_standard=False -> stop pipeline."""
    raise NotImplementedError
