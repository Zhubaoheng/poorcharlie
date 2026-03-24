"""Tests for investagent.schemas.common."""

from datetime import datetime, timezone

from investagent.schemas.common import (
    AgentMeta,
    BaseAgentOutput,
    EvidenceItem,
    EvidenceType,
    StopSignal,
)


def test_evidence_type_values():
    assert EvidenceType.FACT == "FACT"
    assert EvidenceType.INFERENCE == "INFERENCE"
    assert EvidenceType.UNKNOWN == "UNKNOWN"


def test_evidence_item_creation():
    item = EvidenceItem(
        content="Revenue grew 10%",
        source="10-K 2024",
        evidence_type=EvidenceType.FACT,
    )
    assert item.content == "Revenue grew 10%"


def test_agent_meta_creation():
    meta = AgentMeta(
        agent_name="triage",
        timestamp=datetime.now(tz=timezone.utc),
        model_used="claude-sonnet-4-20250514",
        token_usage=100,
    )
    assert meta.agent_name == "triage"


def test_stop_signal_creation():
    signal = StopSignal(should_stop=True, reason="Company is opaque")
    assert signal.should_stop is True


def test_base_agent_output_optional_stop_signal():
    meta = AgentMeta(
        agent_name="test",
        timestamp=datetime.now(tz=timezone.utc),
        model_used="test-model",
        token_usage=0,
    )
    output = BaseAgentOutput(meta=meta)
    assert output.stop_signal is None
