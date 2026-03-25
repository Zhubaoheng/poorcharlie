"""BaseAgent — all agents inherit from this."""

from __future__ import annotations

import importlib.resources
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from jinja2 import Template
from pydantic import BaseModel

from investagent.llm import LLMClient
from investagent.prompts.soul import SOUL_PROMPT
from investagent.schemas.common import AgentMeta, BaseAgentOutput


class AgentOutputError(Exception):
    """Raised when LLM response cannot be parsed into the expected output."""


class BaseAgent(ABC):
    """Base for all pipeline agents.

    Subclasses implement three hooks:
    - ``_output_type``: which Pydantic model the agent returns
    - ``_agent_role_description``: one-paragraph role injected into system prompt
    - ``_build_user_context``: dict of variables fed to the Jinja2 template

    ``run()`` is concrete — it orchestrates prompt rendering, LLM call,
    and output parsing.
    """

    name: str = "base"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    # ------------------------------------------------------------------
    # Abstract hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def _output_type(self) -> type[BaseAgentOutput]:
        """Return the Pydantic output model class."""
        raise NotImplementedError

    @abstractmethod
    def _agent_role_description(self) -> str:
        """Return a one-paragraph description of this agent's role."""
        raise NotImplementedError

    @abstractmethod
    def _build_user_context(
        self, input_data: BaseModel, ctx: Any = None,
    ) -> dict[str, Any]:
        """Build the template context dict from *input_data* and optional *ctx*."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def _render_system_prompt(self) -> str:
        return f"{SOUL_PROMPT}\n\n{self._agent_role_description()}"

    def _load_template(self) -> Template:
        templates = importlib.resources.files("investagent.prompts.templates")
        text = (templates / f"{self.name}.txt").read_text(encoding="utf-8")
        return Template(text)

    def _render_user_prompt(self, input_data: BaseModel, ctx: Any = None) -> str:
        context = self._build_user_context(input_data, ctx)
        return self._load_template().render(**context)

    # ------------------------------------------------------------------
    # Tool schema
    # ------------------------------------------------------------------

    def _prepare_tool_schema(self) -> dict[str, Any]:
        """Generate an Anthropic-compatible tool definition.

        Strips ``meta`` and ``stop_signal`` from the schema so the LLM
        never generates them — they are injected server-side.
        """
        schema = self._output_type().model_json_schema()
        props = schema.get("properties", {})
        props.pop("meta", None)
        props.pop("stop_signal", None)
        required = schema.get("required", [])
        for field in ("meta", "stop_signal"):
            if field in required:
                required.remove(field)
        return {
            "name": self.name,
            "description": f"Output for the {self.name} agent",
            "input_schema": schema,
        }

    # ------------------------------------------------------------------
    # Meta construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_meta(name: str, response: Any) -> AgentMeta:
        return AgentMeta(
            agent_name=name,
            timestamp=datetime.now(tz=timezone.utc),
            model_used=response.model,
            token_usage=response.usage.input_tokens + response.usage.output_tokens,
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(
        self, input_data: BaseModel, ctx: Any = None,
    ) -> BaseAgentOutput:
        """Render prompts, call LLM, parse and validate output."""
        system = self._render_system_prompt()
        user_prompt = self._render_user_prompt(input_data, ctx)
        tool_schema = self._prepare_tool_schema()

        response = await self._llm.create_message(
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[tool_schema],
        )

        # Extract tool_use block
        tool_input = None
        for block in response.content:
            if block.type == "tool_use":
                tool_input = block.input
                break

        if tool_input is None:
            raise AgentOutputError(
                f"{self.name}: no tool_use block in LLM response"
            )

        # Inject server-managed meta (overwrites anything the LLM emitted)
        meta = self._build_meta(self.name, response)
        tool_input["meta"] = meta.model_dump(mode="json")

        try:
            return self._output_type().model_validate(tool_input)
        except Exception as exc:
            raise AgentOutputError(
                f"{self.name}: failed to validate output: {exc}"
            ) from exc
