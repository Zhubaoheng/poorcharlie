"""Shared enums, base models, and evidence types used across all agent schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class EvidenceType(str, Enum):
    FACT = "FACT"
    INFERENCE = "INFERENCE"
    UNKNOWN = "UNKNOWN"


class EvidenceItem(BaseModel, frozen=True):
    content: str
    source: str
    evidence_type: EvidenceType


class AgentMeta(BaseModel, frozen=True):
    model_config = ConfigDict(protected_namespaces=())

    agent_name: str
    timestamp: datetime
    model_used: str
    token_usage: int


class StopSignal(BaseModel, frozen=True):
    should_stop: bool
    reason: str


class BaseAgentOutput(BaseModel, frozen=True):
    meta: AgentMeta
    stop_signal: StopSignal | None = None
