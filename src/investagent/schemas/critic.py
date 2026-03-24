"""Critic Agent output schema."""

from __future__ import annotations

from investagent.schemas.common import BaseAgentOutput


class CriticOutput(BaseAgentOutput):
    kill_shots: list[str]
    permanent_loss_risks: list[str]
    moat_destruction_paths: list[str]
    management_failure_modes: list[str]
    what_would_make_this_uninvestable: list[str]
