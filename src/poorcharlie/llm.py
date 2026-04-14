"""Thin wrapper around Anthropic-compatible async clients (Claude, MiniMax)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, time as dtime, timedelta
from typing import Any

import anthropic
import httpx

logger = logging.getLogger(__name__)


# Default fallback when no reset schedule is configured: 30-minute sleep.
_DEFAULT_QUOTA_SLEEP_S = 1800
_QUOTA_WAKE_BUFFER_S = 60   # small safety margin after the reset boundary
_QUOTA_MAX_SLEEP_S = 6 * 3600  # never sleep longer than 6h in one shot


def _parse_reset_hours(raw: str | None) -> list[int]:
    """Parse comma-separated hours (0-23). Empty/invalid → []."""
    if not raw:
        return []
    hours: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            h = int(part)
        except ValueError:
            continue
        if 0 <= h <= 23:
            hours.append(h)
    return sorted(set(hours))


def _quota_sleep_seconds(provider: str) -> tuple[int, str]:
    """How long to sleep for a usage-limit hit, and a human-readable description.

    If ``{PROVIDER}_QUOTA_RESET_HOURS`` env is set (e.g. "0,5,10,15,20"),
    sleep until the next reset hour (local time) + small buffer, capped
    at _QUOTA_MAX_SLEEP_S. Otherwise fall back to the 30-min default.
    """
    env_key = f"{provider.upper()}_QUOTA_RESET_HOURS"
    reset_hours = _parse_reset_hours(os.getenv(env_key))
    if not reset_hours:
        return _DEFAULT_QUOTA_SLEEP_S, "30min (default)"

    now = datetime.now()
    today = now.date()
    # Find the next reset boundary strictly after now.
    candidates = [
        datetime.combine(today, dtime(h, 0)) for h in reset_hours
    ] + [
        datetime.combine(today + timedelta(days=1), dtime(reset_hours[0], 0)),
    ]
    next_reset = min(c for c in candidates if c > now)
    delta = (next_reset - now).total_seconds() + _QUOTA_WAKE_BUFFER_S
    wait = int(min(max(delta, _QUOTA_WAKE_BUFFER_S), _QUOTA_MAX_SLEEP_S))
    desc = f"until {next_reset.strftime('%H:%M')} reset (~{wait/60:.0f}min)"
    return wait, desc

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
        provider: str = "claude",
    ) -> None:
        self.provider = provider
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

        # Retry on ANY transient error: timeout, rate limit, overload, connection reset.
        # 10 attempts, immediate retry (no backoff) for timeouts,
        # backoff only for rate limits (429/529).
        _MAX_ATTEMPTS = 10
        _CALL_TIMEOUT = 300  # 5 min max per LLM call (guards against hung connections)
        for attempt in range(_MAX_ATTEMPTS):
            _stats["calls"] += 1
            t0 = time.time()
            try:
                resp = await asyncio.wait_for(
                    self._client.messages.create(**kwargs),
                    timeout=_CALL_TIMEOUT,
                )
                latency = time.time() - t0

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
                status = e.status_code if hasattr(e, "status_code") else 429
                err_msg = str(e)

                # MiniMax usage limit (error code 2056): sleep until the next
                # quota reset boundary (configured via MINIMAX_QUOTA_RESET_HOURS)
                # or 30 min if unset. Gated on provider to avoid matching
                # unrelated "2056" substrings from other vendors.
                if self.provider == "minimax" and (
                    "2056" in err_msg or "usage limit exceeded" in err_msg.lower()
                ):
                    wait, desc = _quota_sleep_seconds(self.provider)
                    logger.warning(
                        "LLM usage limit exceeded (2056), sleeping %s | %s",
                        desc, err_msg[:120],
                    )
                    await asyncio.sleep(wait)
                    continue  # retry without counting as failure

                # Non-retryable API errors (4xx except 429)
                if isinstance(e, anthropic.APIStatusError) and status not in (429, 529):
                    _stats["errors"] += 1
                    logger.error("LLM API error %s after %.1fs: %s", status, latency, e)
                    raise
                if attempt == _MAX_ATTEMPTS - 1:
                    _stats["errors"] += 1
                    raise
                # Rate limit: backoff
                wait = min(30 * (attempt + 1), 1800)
                logger.warning(
                    "LLM rate limit %s, waiting %ds (attempt %d/%d)",
                    status, wait, attempt + 1, _MAX_ATTEMPTS,
                )
                await asyncio.sleep(wait)

            except asyncio.TimeoutError:
                latency = time.time() - t0
                _stats["retries"] += 1
                if attempt == _MAX_ATTEMPTS - 1:
                    _stats["errors"] += 1
                    logger.error(
                        "LLM call timeout after %ds (attempt %d/%d) | input~%dchars",
                        _CALL_TIMEOUT, attempt + 1, _MAX_ATTEMPTS, input_chars,
                    )
                    raise
                logger.warning(
                    "LLM call timeout after %ds, retrying (attempt %d/%d) | input~%dchars",
                    _CALL_TIMEOUT, attempt + 1, _MAX_ATTEMPTS, input_chars,
                )

            except Exception as e:
                latency = time.time() - t0
                _stats["retries"] += 1
                err_msg = str(e)

                # MiniMax usage limit (error code 2056): sleep until next reset.
                if self.provider == "minimax" and (
                    "2056" in err_msg or "usage limit exceeded" in err_msg.lower()
                ):
                    wait, desc = _quota_sleep_seconds(self.provider)
                    logger.warning(
                        "LLM usage limit exceeded (2056), sleeping %s | %s",
                        desc, err_msg[:120],
                    )
                    await asyncio.sleep(wait)
                    continue

                if attempt == _MAX_ATTEMPTS - 1:
                    _stats["errors"] += 1
                    logger.error(
                        "LLM failed after %d attempts: %s: %s | input~%dchars",
                        _MAX_ATTEMPTS, type(e).__name__, e, input_chars,
                    )
                    raise
                # Timeout/connection error: retry immediately (no backoff)
                logger.warning(
                    "LLM transient error, retrying immediately (attempt %d/%d): %s: %s",
                    attempt + 1, _MAX_ATTEMPTS, type(e).__name__,
                    str(e)[:80],
                )

        raise RuntimeError("Unreachable")
