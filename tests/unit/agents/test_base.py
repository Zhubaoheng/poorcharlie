"""Tests for investagent.agents.base."""

import pytest

from investagent.agents.base import BaseAgent


def test_base_agent_is_abstract():
    with pytest.raises(TypeError):
        BaseAgent()  # type: ignore[abstract]
