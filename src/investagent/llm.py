"""Thin wrapper around Anthropic-compatible async clients (Claude, MiniMax)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import anthropic
import httpx

logger = logging.getLogger(__name__)

# Cumulative LLM call stats (thread-safe for asyncio single-thread)
_stats = {
    "calls": 0,
    "successes": 0,
    "retries": 0,
    "errors": 0,
    "total_latency": 0.0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
}


def get_llm_stats() -> dict[str, Any]:
    """Return a snapshot of cumulative LLM call stats."""
    s = dict(_stats)
    s["avg_latency"] = round(s["total_latency"] / s["calls"], 1) if s["calls"] else 0
    return s


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
            http_client = httpx.AsyncClient(
                verify=False,
                timeout=httpx.Timeout(connect=10, read=600, write=600, pool=600),
            ) if base_url else None
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

        # Estimate input size for logging
        input_chars = len(system) + sum(
            len(str(m.get("content", ""))) for m in messages
        )

        # Retry with backoff on rate limit (429) and overload (529)
        _BACKOFF = [10, 30, 60, 300, 1800]
        for attempt, wait in enumerate(_BACKOFF):
            _stats["calls"] += 1
            t0 = time.time()
            try:
                resp = await self._client.messages.create(**kwargs)
                latency = time.time() - t0

                # Track stats
                _stats["successes"] += 1
                _stats["total_latency"] += latency
                in_tok = getattr(resp.usage, "input_tokens", 0)
                out_tok = getattr(resp.usage, "output_tokens", 0)
                _stats["total_input_tokens"] += in_tok
                _stats["total_output_tokens"] += out_tok

                logger.info(
                    "LLM call #%d: %.1fs | in=%d out=%d tokens | input~%dchars | model=%s | stop=%s",
                    _stats["successes"], latency, in_tok, out_tok,
                    input_chars, self.model, resp.stop_reason,
                )

                # Warn on slow calls
                if latency > 120:
                    logger.warning(
                        "LLM SLOW call: %.1fs | in=%d out=%d | cumulative avg=%.1fs",
                        latency, in_tok, out_tok,
                        _stats["total_latency"] / _stats["successes"],
                    )

                return resp

            except (anthropic.RateLimitError, anthropic.APIStatusError) as e:
                latency = time.time() - t0
                _stats["retries"] += 1
                is_last = attempt == len(_BACKOFF) - 1
                status = e.status_code if hasattr(e, "status_code") else 429
                if isinstance(e, anthropic.APIStatusError) and status not in (429, 529):
                    _stats["errors"] += 1
                    logger.error("LLM API error %s after %.1fs: %s", status, latency, e)
                    raise
                if is_last:
                    _stats["errors"] += 1
                    logger.error(
                        "LLM rate limit exhausted after %d attempts (%.1fs total backoff)",
                        len(_BACKOFF), sum(_BACKOFF),
                    )
                    raise
                logger.warning(
                    "LLM rate limit %s, waiting %ds (attempt %d/%d) | latency=%.1fs",
                    status, wait, attempt + 1, len(_BACKOFF), latency,
                )
                await asyncio.sleep(wait)

            except BaseException as e:
                latency = time.time() - t0
                _stats["errors"] += 1
                logger.error(
                    "LLM call failed after %.1fs: %s: %s | input~%dchars | stats: %d ok / %d err / %d retry",
                    latency, type(e).__name__, e, input_chars,
                    _stats["successes"], _stats["errors"], _stats["retries"],
                )
                raise

        raise RuntimeError("Unreachable")
