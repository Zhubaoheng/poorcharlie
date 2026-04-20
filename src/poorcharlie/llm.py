"""Thin wrapper around Anthropic-compatible async clients (Claude, MiniMax)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import anthropic
import httpx

logger = logging.getLogger(__name__)


# Poll every N seconds when quota is exhausted; adapt to any reset schedule
# without needing to know it. Override via {PROVIDER}_QUOTA_POLL_SECONDS.
# Also cap cumulative wait per call via {PROVIDER}_QUOTA_MAX_WAIT_SECONDS so a
# permanently-exhausted plan doesn't block the pipeline indefinitely.
_DEFAULT_QUOTA_POLL_S = 300         # 5 min between retries
_DEFAULT_QUOTA_MAX_WAIT_S = 6 * 3600  # give up after 6h cumulative wait


def _quota_poll_seconds(provider: str) -> int:
    raw = os.getenv(f"{provider.upper()}_QUOTA_POLL_SECONDS")
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _DEFAULT_QUOTA_POLL_S


def _quota_max_wait_seconds(provider: str) -> int:
    raw = os.getenv(f"{provider.upper()}_QUOTA_MAX_WAIT_SECONDS")
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _DEFAULT_QUOTA_MAX_WAIT_S


class _QuotaGate:
    """Process-wide quota coordination for a single provider.

    Problem: when the pipeline runs N concurrent LLM calls and hits a
    provider quota limit (e.g., MiniMax 2056), each call independently
    sleeps poll_s and retries — a thundering herd that:
    1. Spams logs with N "polling" lines per poll window.
    2. Wakes all N calls simultaneously, which may immediately re-trip
       the rate limit when quota partially refills.

    Fix: only ONE caller ("prober") probes at a time. When the probe
    succeeds, it sets `healthy` and all waiters wake and retry their
    own calls. Failed probes (non-quota errors) release the prober
    slot so the next caller takes over.
    """

    def __init__(self) -> None:
        self.healthy = asyncio.Event()
        self.healthy.set()
        self.prober_active = False


_quota_gates: dict[str, _QuotaGate] = {}


def _get_quota_gate(provider: str) -> _QuotaGate:
    if provider not in _quota_gates:
        _quota_gates[provider] = _QuotaGate()
    return _quota_gates[provider]


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
        # Force specific tool if exactly one tool is provided.
        # Qwen thinking mode SOMETIMES rejects {type:"tool"} or {type:"any"}
        # with "InvalidParameter: tool_choice ... not supported in thinking
        # mode". We handle that via a one-shot fallback to tool_choice=auto
        # in the retry loop below, not by pre-emptively weakening it.
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
        # Cumulative time spent sleeping on quota-exhaustion (2056). Capped so
        # a permanently-dry plan doesn't block the whole pipeline forever.
        # Quota polling does NOT consume a retry attempt — it's a separate
        # dimension bounded by quota_cum_wait_s vs max_wait.
        quota_cum_wait_s = 0
        is_prober = False  # set to True when this call has taken the probing role
        gate = _get_quota_gate(self.provider)

        # Before firing, block if another concurrent call has already detected
        # quota exhaustion. Only one prober probes; everyone else waits here.
        if not gate.healthy.is_set():
            t_wait = time.time()
            await gate.healthy.wait()
            logger.info(
                "LLM %s: quota gate released after %.0fs, retrying",
                self.provider, time.time() - t_wait,
            )

        attempt = 0
        while attempt < _MAX_ATTEMPTS:
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

                # If I was probing and just succeeded, quota is back — wake everyone.
                if is_prober:
                    gate.prober_active = False
                    gate.healthy.set()
                    logger.info(
                        "LLM %s: prober succeeded, quota gate opened",
                        self.provider,
                    )

                return resp

            except (anthropic.RateLimitError, anthropic.APIStatusError) as e:
                latency = time.time() - t0
                _stats["retries"] += 1
                status = e.status_code if hasattr(e, "status_code") else 429
                err_msg = str(e)

                # DashScope thinking-mode tool_choice incompatibility: if the
                # backend rejects our {type:"tool"} or {type:"any"} with the
                # specific thinking-mode error, retry once with tool_choice=auto
                # and let the system prompt direct the model to use the tool.
                # Error signature is specific enough to match regardless of the
                # provider tag — users who configure via the legacy LLM_* form
                # without LLM_PROVIDER=qwen should still be rescued.
                if (status == 400
                        and "tool_choice" in err_msg
                        and "thinking mode" in err_msg
                        and kwargs.get("tool_choice", {}).get("type") != "auto"):
                    logger.warning(
                        "%s thinking-mode rejected tool_choice=%s; retrying with auto",
                        self.provider, kwargs.get("tool_choice"),
                    )
                    kwargs["tool_choice"] = {"type": "auto"}
                    continue

                # MiniMax usage limit (error code 2056): coordinate via shared
                # gate so only ONE caller probes while others wait. Prevents
                # the thundering-herd pattern where N concurrent calls each
                # independently sleep and wake together.
                if self.provider == "minimax" and (
                    "2056" in err_msg or "usage limit exceeded" in err_msg.lower()
                ):
                    extra_wait, is_prober, give_up = await self._handle_quota_exhausted(
                        gate, quota_cum_wait_s, is_prober,
                    )
                    if give_up:
                        _stats["errors"] += 1
                        raise
                    quota_cum_wait_s += extra_wait
                    continue  # retry without counting as failure

                # Non-retryable API errors (4xx except 429)
                if isinstance(e, anthropic.APIStatusError) and status not in (429, 529):
                    _stats["errors"] += 1
                    logger.error("LLM API error %s after %.1fs: %s", status, latency, e)
                    self._release_prober_if_held(gate, is_prober)
                    raise
                if attempt == _MAX_ATTEMPTS - 1:
                    _stats["errors"] += 1
                    self._release_prober_if_held(gate, is_prober)
                    raise
                # Rate limit: backoff
                wait = min(30 * (attempt + 1), 1800)
                logger.warning(
                    "LLM rate limit %s, waiting %ds (attempt %d/%d)",
                    status, wait, attempt + 1, _MAX_ATTEMPTS,
                )
                await asyncio.sleep(wait)
                attempt += 1

            except asyncio.TimeoutError:
                latency = time.time() - t0
                _stats["retries"] += 1
                if attempt == _MAX_ATTEMPTS - 1:
                    _stats["errors"] += 1
                    logger.error(
                        "LLM call timeout after %ds (attempt %d/%d) | input~%dchars",
                        _CALL_TIMEOUT, attempt + 1, _MAX_ATTEMPTS, input_chars,
                    )
                    self._release_prober_if_held(gate, is_prober)
                    raise
                logger.warning(
                    "LLM call timeout after %ds, retrying (attempt %d/%d) | input~%dchars",
                    _CALL_TIMEOUT, attempt + 1, _MAX_ATTEMPTS, input_chars,
                )
                attempt += 1

            except Exception as e:
                latency = time.time() - t0
                _stats["retries"] += 1
                err_msg = str(e)

                # MiniMax usage limit (error code 2056): coordinate via shared gate.
                if self.provider == "minimax" and (
                    "2056" in err_msg or "usage limit exceeded" in err_msg.lower()
                ):
                    extra_wait, is_prober, give_up = await self._handle_quota_exhausted(
                        gate, quota_cum_wait_s, is_prober,
                    )
                    if give_up:
                        _stats["errors"] += 1
                        raise
                    quota_cum_wait_s += extra_wait
                    continue

                if attempt == _MAX_ATTEMPTS - 1:
                    _stats["errors"] += 1
                    logger.error(
                        "LLM failed after %d attempts: %s: %s | input~%dchars",
                        _MAX_ATTEMPTS, type(e).__name__, e, input_chars,
                    )
                    self._release_prober_if_held(gate, is_prober)
                    raise
                # Timeout/connection error: retry immediately (no backoff)
                logger.warning(
                    "LLM transient error, retrying immediately (attempt %d/%d): %s: %s",
                    attempt + 1, _MAX_ATTEMPTS, type(e).__name__,
                    str(e)[:80],
                )
                attempt += 1

        raise RuntimeError("Unreachable")

    @staticmethod
    def _release_prober_if_held(gate: _QuotaGate, is_prober: bool) -> None:
        """Release the prober slot so other waiters can retry.

        Called before every `raise` that exits the retry loop. If this
        coroutine held the prober role and is about to bail out, we must
        unblock everyone else — otherwise they deadlock on `gate.healthy`.
        """
        if is_prober:
            gate.prober_active = False
            gate.healthy.set()

    async def _handle_quota_exhausted(
        self,
        gate: _QuotaGate,
        quota_cum_wait_s: int,
        is_prober: bool,
    ) -> tuple[int, bool, bool]:
        """Coordinate multi-caller behavior on quota-exhaustion (2056).

        Returns (extra_wait_seconds, new_is_prober_flag, should_give_up).

        Semantics:
        - First caller to hit 2056 becomes the prober: sleeps poll_s, then
          retries its own call (which acts as the probe).
        - Subsequent callers await `gate.healthy` without making API calls,
          so the provider sees only the prober's traffic during exhaustion.
        - On probe success (see the success branch in create_message), the
          prober sets `gate.healthy` — all waiters wake and retry their
          own calls with fresh quota.
        - If cumulative wait exceeds the per-call cap, this caller gives
          up; if it was the prober, the role is released for the next
          caller to take over.
        """
        poll_s = _quota_poll_seconds(self.provider)
        max_wait = _quota_max_wait_seconds(self.provider)

        if quota_cum_wait_s + poll_s > max_wait:
            # Free the prober slot AND wake everyone else so they can retry.
            # Correct intuition: we don't know if quota is still exhausted; if
            # one waiter wakes and succeeds, it takes over the healthy signal.
            # Otherwise the next failure will re-clear and elect a new prober.
            self._release_prober_if_held(gate, is_prober)
            logger.error(
                "LLM %s quota still exhausted after %.1fmin cumulative "
                "wait (cap %.1fmin) — giving up this call.",
                self.provider, quota_cum_wait_s / 60, max_wait / 60,
            )
            return 0, is_prober, True

        gate.healthy.clear()
        if is_prober or not gate.prober_active:
            # Take the prober role if we don't already have it.
            if not is_prober:
                gate.prober_active = True
                is_prober = True
                logger.warning(
                    "LLM %s quota exhausted (2056), I'm the prober — "
                    "polling every %ds; other calls will wait on the gate",
                    self.provider, poll_s,
                )
            else:
                logger.warning(
                    "LLM %s: prober still blocked (total waited %.0fmin), "
                    "sleeping another %ds",
                    self.provider, quota_cum_wait_s / 60, poll_s,
                )
            await asyncio.sleep(poll_s)
            return poll_s, is_prober, False

        # Someone else is probing — wait for their verdict (no API call).
        t_wait = time.time()
        await gate.healthy.wait()
        waited = time.time() - t_wait
        logger.info(
            "LLM %s: woken by prober after %.0fs, retrying own call",
            self.provider, waited,
        )
        return int(waited), is_prober, False
