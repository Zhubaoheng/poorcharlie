"""Thin wrapper around Anthropic-compatible async clients (Claude, MiniMax)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import anthropic
import httpx

logger = logging.getLogger(__name__)


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
        extra_body: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> None:
        if client:
            self._client = client
        else:
            # Use a custom httpx client to handle proxies with self-signed certs
            http_client = httpx.AsyncClient(verify=False) if base_url else None
            kwargs: dict[str, Any] = {}
            if base_url:
                kwargs["base_url"] = base_url
            if api_key:
                kwargs["api_key"] = api_key
            if http_client:
                kwargs["http_client"] = http_client
            self._client = anthropic.AsyncAnthropic(**kwargs)
        self.model = model
        self._extra_body = extra_body or {}
        self._temperature = temperature

    async def create_message(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 16384,
    ) -> anthropic.types.Message:
        """Send a single tool-use request to an Anthropic-compatible API."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "system": system,
            "messages": messages,
            "tools": tools,
            "max_tokens": max_tokens,
            "temperature": self._temperature,
        }
        # Force specific tool if exactly one tool is provided
        if len(tools) == 1:
            kwargs["tool_choice"] = {"type": "tool", "name": tools[0]["name"]}
        elif tools:
            kwargs["tool_choice"] = {"type": "any"}
        # Provider-specific parameters (e.g., MiniMax context_window, effort)
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body
        # Retry with backoff on rate limit (429)
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                return await self._client.messages.create(**kwargs)
            except anthropic.RateLimitError:
                if attempt == max_attempts - 1:
                    raise
                wait = 10 * (attempt + 1)  # 10s, 20s, 30s, 40s
                logger.warning("Rate limit 429, waiting %ds (attempt %d/%d)", wait, attempt + 1, max_attempts)
                await asyncio.sleep(wait)
        raise RuntimeError("Unreachable")
