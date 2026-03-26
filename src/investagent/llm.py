"""Thin wrapper around Anthropic-compatible async clients (Claude, MiniMax)."""

from __future__ import annotations

from typing import Any

import anthropic


class LLMClient:
    """Async LLM client for Anthropic-compatible APIs.

    Works with any provider that exposes the Anthropic Messages API
    (Claude, MiniMax, etc.).  Designed to be injected into agents so
    that tests can substitute a mock.
    """

    def __init__(
        self,
        *,
        model: str = "claude-sonnet-4-20250514",
        base_url: str | None = None,
        api_key: str | None = None,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self._client = client or anthropic.AsyncAnthropic(
            base_url=base_url,
            api_key=api_key,
        )
        self.model = model

    async def create_message(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 8192,
    ) -> anthropic.types.Message:
        """Send a single tool-use request to an Anthropic-compatible API."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "system": system,
            "messages": messages,
            "tools": tools,
            "max_tokens": max_tokens,
        }
        # Force specific tool if exactly one tool is provided
        if len(tools) == 1:
            kwargs["tool_choice"] = {"type": "tool", "name": tools[0]["name"]}
        elif tools:
            kwargs["tool_choice"] = {"type": "any"}
        return await self._client.messages.create(**kwargs)
