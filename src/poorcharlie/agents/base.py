"""BaseAgent — all agents inherit from this."""

from __future__ import annotations

import importlib.resources
import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from jinja2 import Template
from pydantic import BaseModel

from poorcharlie.llm import LLMClient
from poorcharlie.prompts.soul import SOUL_PROMPT, build_soul_prompt
from poorcharlie.schemas.common import AgentMeta, BaseAgentOutput


class AgentOutputError(Exception):
    """Raised when LLM response cannot be parsed into the expected output."""


def _repair_json_strings(obj: Any) -> Any:
    """Repair common LLM output quirks before Pydantic validation.

    Some providers (e.g., MiniMax) return nested objects/arrays as JSON
    strings instead of native dicts/lists. This function recursively
    parses any string that is valid JSON into its native Python type.
    """
    if isinstance(obj, str):
        stripped = obj.strip()
        if stripped and stripped[0] in ("{", "[", '"'):
            # Try as-is first
            try:
                parsed = json.loads(stripped)
                if not isinstance(parsed, str):
                    return _repair_json_strings(parsed)
            except (json.JSONDecodeError, ValueError):
                pass
            # Fallback: escape unescaped control characters
            import re
            cleaned = re.sub(
                r'(?<!\\)([\n\r\t])',
                lambda m: {"\n": "\\n", "\r": "\\r", "\t": "\\t"}[m.group(1)],
                stripped,
            )
            try:
                parsed = json.loads(cleaned)
                if not isinstance(parsed, str):
                    return _repair_json_strings(parsed)
            except (json.JSONDecodeError, ValueError):
                pass
        return obj
    elif isinstance(obj, dict):
        return {k: _repair_json_strings(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_repair_json_strings(item) for item in obj]
    return obj


def _coerce_lists_to_strings(obj: Any, schema: dict[str, Any]) -> Any:
    """Coerce list values to joined strings when the schema expects a string.

    Some LLM providers return list[str] for fields typed as str.
    """
    if not isinstance(obj, dict) or not schema:
        return obj
    props = schema.get("properties", {})
    for key, value in obj.items():
        prop_schema = props.get(key, {})
        prop_type = prop_schema.get("type")
        if isinstance(value, list) and prop_type == "string":
            # Join list items into a single string
            obj[key] = "\n".join(str(item) for item in value)
        elif isinstance(value, dict) and prop_type == "object":
            obj[key] = _coerce_lists_to_strings(value, prop_schema)
    return obj


def _coerce_strings_to_lists(obj: Any, schema: dict[str, Any]) -> Any:
    """Coerce string values to lists when the schema expects an array.

    Some LLM providers (MiniMax) return JSON arrays as strings, and
    _repair_json_strings may fail if the string contains literal
    newlines or other characters that break json.loads.
    """
    if not isinstance(obj, dict) or not schema:
        return obj
    props = schema.get("properties", {})
    for key, value in obj.items():
        prop_schema = props.get(key, {})
        prop_type = prop_schema.get("type")
        if isinstance(value, str) and prop_type == "array":
            stripped = value.strip()
            if stripped.startswith("["):
                # Try parsing as-is first
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        obj[key] = parsed
                        continue
                except (json.JSONDecodeError, ValueError):
                    pass
                # Fallback: escape unescaped control characters
                import re
                cleaned = re.sub(
                    r'(?<!\\)([\n\r\t])',
                    lambda m: {"\n": "\\n", "\r": "\\r", "\t": "\\t"}[m.group(1)],
                    stripped,
                )
                try:
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, list):
                        obj[key] = parsed
                except (json.JSONDecodeError, ValueError):
                    pass
    return obj


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

    def __init__(self, llm: LLMClient, as_of_date: str | None = None) -> None:
        self._llm = llm
        self._as_of_date = as_of_date

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
        soul = build_soul_prompt(self._as_of_date)
        parts = [soul, self._agent_role_description()]
        # Qwen thinking mode forces tool_choice="auto" (see llm.py); reinforce
        # the expectation that the model MUST use the provided tool so the
        # agent pipeline can parse a structured result.
        if getattr(self._llm, "provider", "") == "qwen":
            parts.append(
                "【输出格式要求】你必须且只能通过提供的工具返回结构化结果。"
                "不要输出纯文本回答。先充分思考，再调用工具一次输出最终结果。"
            )
        return "\n\n".join(parts)

    def _load_template(self) -> Template:
        templates = importlib.resources.files("poorcharlie.prompts.templates")
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
        *, max_retries: int = 5,
    ) -> BaseAgentOutput:
        """Render prompts, call LLM, parse and validate output.

        Retries up to *max_retries* times if the LLM fails to return a
        valid tool_use block (common with some providers on complex schemas).
        """
        system = self._render_system_prompt()
        user_prompt = self._render_user_prompt(input_data, ctx)
        tool_schema = self._prepare_tool_schema()

        last_error: Exception | None = None
        for attempt in range(1 + max_retries):
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
                last_error = AgentOutputError(
                    f"{self.name}: no tool_use block in LLM response "
                    f"(attempt {attempt + 1}/{1 + max_retries})"
                )
                continue

            # Repair LLM output quirks
            tool_input = _repair_json_strings(tool_input)
            output_schema = self._output_type().model_json_schema()
            tool_input = _coerce_lists_to_strings(tool_input, output_schema)
            tool_input = _coerce_strings_to_lists(tool_input, output_schema)

            # Inject server-managed meta (overwrites anything the LLM emitted)
            meta = self._build_meta(self.name, response)
            tool_input["meta"] = meta.model_dump(mode="json")

            try:
                return self._output_type().model_validate(tool_input)
            except Exception as exc:
                last_error = AgentOutputError(
                    f"{self.name}: failed to validate output "
                    f"(attempt {attempt + 1}/{1 + max_retries}): {exc}"
                )
                continue

        raise last_error  # type: ignore[misc]
