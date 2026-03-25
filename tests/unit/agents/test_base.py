"""Tests for investagent.agents.base."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.llm import LLMClient
from investagent.schemas.common import BaseAgentOutput


def _mock_llm() -> LLMClient:
    return LLMClient(client=MagicMock())


class _ConcreteAgent(BaseAgent):
    name: str = "test_agent"

    def _output_type(self) -> type[BaseAgentOutput]:
        return BaseAgentOutput

    def _agent_role_description(self) -> str:
        return "Test agent for unit testing."

    def _build_user_context(self, input_data: BaseModel, ctx: Any = None) -> dict[str, Any]:
        return {}


def test_base_agent_is_abstract():
    with pytest.raises(TypeError):
        BaseAgent(llm=_mock_llm())  # type: ignore[abstract]


def test_concrete_agent_instantiation():
    agent = _ConcreteAgent(llm=_mock_llm())
    assert agent.name == "test_agent"


def test_prepare_tool_schema_strips_meta():
    agent = _ConcreteAgent(llm=_mock_llm())
    schema = agent._prepare_tool_schema()
    props = schema["input_schema"]["properties"]
    assert "meta" not in props
    assert "stop_signal" not in props


def test_prepare_tool_schema_required_strips_meta():
    agent = _ConcreteAgent(llm=_mock_llm())
    schema = agent._prepare_tool_schema()
    required = schema["input_schema"].get("required", [])
    assert "meta" not in required
    assert "stop_signal" not in required


def test_render_system_prompt():
    agent = _ConcreteAgent(llm=_mock_llm())
    system = agent._render_system_prompt()
    assert "Munger-style" in system
    assert "Test agent" in system
