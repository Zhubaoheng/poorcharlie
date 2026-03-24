"""Tests for investagent.schemas.triage."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from investagent.schemas.common import AgentMeta
from investagent.schemas.triage import ExplainabilityScore, TriageDecision, TriageOutput


def _meta() -> AgentMeta:
    return AgentMeta(
        agent_name="triage",
        timestamp=datetime.now(tz=timezone.utc),
        model_used="test",
        token_usage=0,
    )


def test_triage_decision_values():
    assert TriageDecision.PASS == "PASS"
    assert TriageDecision.REJECT == "REJECT"


def test_triage_output_creation():
    output = TriageOutput(
        meta=_meta(),
        decision=TriageDecision.PASS,
        explainability_score=ExplainabilityScore(
            business_model=8,
            competition_structure=7,
            financial_mapping=6,
            key_drivers=7,
        ),
        fatal_unknowns=[],
        why_it_is_or_is_not_coverable="Business model is clear",
        next_step="Proceed to Info Capture",
    )
    assert output.decision == TriageDecision.PASS


def test_triage_output_invalid_decision():
    with pytest.raises(ValidationError):
        TriageOutput(
            meta=_meta(),
            decision="INVALID",  # type: ignore[arg-type]
            explainability_score=ExplainabilityScore(
                business_model=0,
                competition_structure=0,
                financial_mapping=0,
                key_drivers=0,
            ),
            fatal_unknowns=[],
            why_it_is_or_is_not_coverable="",
            next_step="",
        )
