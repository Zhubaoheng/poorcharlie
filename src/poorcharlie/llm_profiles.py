"""Named LLM profile registry.

Each profile is a complete LLM connection config identified by name
(e.g. "minimax", "claude", "deepseek"), loaded from env vars with the
profile name as prefix:

    MINIMAX_BASE_URL, MINIMAX_API_KEY, MINIMAX_MODEL, MINIMAX_PROVIDER,
    MINIMAX_EXTRA_BODY (optional JSON)

Which profile is the "default" is decided by ``LLM_DEFAULT_PROFILE`` env.

Callers get an LLMClient via ``create_llm_client(profile="claude")`` in
``poorcharlie.config``; this module only handles the env-to-dataclass mapping.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Vendor tags that trigger provider-specific branches downstream (e.g.
# MiniMax 2056 quota handling in llm.py). New tags added here must either
# map to an existing branch or be benign.
_KNOWN_PROVIDERS: tuple[str, ...] = (
    "claude", "minimax", "deepseek", "openai", "qwen",
)

# Profile name → default provider tag when {PROFILE}_PROVIDER is absent.
# The profile name is the canonical identifier; provider tag is an optional
# override for vendor-specific behavior on endpoints that route multiple
# vendors through one URL.
_PROFILE_DEFAULT_PROVIDER: dict[str, str] = {
    "claude": "claude",
    "minimax": "minimax",
    "deepseek": "deepseek",
    "openai": "openai",
    "qwen": "qwen",
}


@dataclass(frozen=True)
class LLMProfile:
    """One LLM connection config."""

    name: str
    base_url: str
    api_key: str
    model: str
    provider: str
    extra_body: dict[str, Any] = field(default_factory=dict)


def load_profile(profile_name: str) -> LLMProfile:
    """Load a named profile from env vars.

    Reads ``{PROFILE}_BASE_URL`` / ``_API_KEY`` / ``_MODEL`` (required),
    and optional ``_PROVIDER`` / ``_EXTRA_BODY`` (JSON).

    Raises ValueError if any required var is missing.
    """
    up = profile_name.upper()
    base_url = os.getenv(f"{up}_BASE_URL")
    api_key = os.getenv(f"{up}_API_KEY")
    model = os.getenv(f"{up}_MODEL")
    provider = os.getenv(f"{up}_PROVIDER") or _PROFILE_DEFAULT_PROVIDER.get(
        profile_name.lower(), "openai",
    )

    extra_raw = os.getenv(f"{up}_EXTRA_BODY", "").strip()
    extra_body: dict[str, Any] = {}
    if extra_raw:
        try:
            parsed = json.loads(extra_raw)
            if isinstance(parsed, dict):
                extra_body = parsed
            else:
                logger.warning(
                    "%s_EXTRA_BODY must be a JSON object, got %s",
                    up, type(parsed).__name__,
                )
        except json.JSONDecodeError as e:
            logger.warning("Invalid %s_EXTRA_BODY JSON: %s", up, e)

    missing = [k for k, v in [
        (f"{up}_BASE_URL", base_url),
        (f"{up}_API_KEY", api_key),
        (f"{up}_MODEL", model),
    ] if not v]
    if missing:
        raise ValueError(
            f"Profile {profile_name!r} missing env vars: {', '.join(missing)}",
        )
    if provider not in _KNOWN_PROVIDERS:
        logger.warning(
            "Unknown provider tag %r for profile %s; vendor-specific "
            "branches will not trigger. Known: %s",
            provider, profile_name, _KNOWN_PROVIDERS,
        )

    return LLMProfile(
        name=profile_name.lower(),
        base_url=base_url,   # type: ignore[arg-type]
        api_key=api_key,     # type: ignore[arg-type]
        model=model,         # type: ignore[arg-type]
        provider=provider,
        extra_body=extra_body,
    )


def resolve_default_profile() -> str:
    """Profile to use when caller does not specify one.

    Controlled by LLM_DEFAULT_PROFILE env; falls back to "minimax".
    """
    return os.getenv("LLM_DEFAULT_PROFILE", "").strip().lower() or "minimax"


def list_available_profiles() -> list[str]:
    """Scan env for which known profiles have a full config. Diagnostic use only."""
    available = []
    for name in _PROFILE_DEFAULT_PROVIDER:
        up = name.upper()
        if (
            os.getenv(f"{up}_BASE_URL")
            and os.getenv(f"{up}_API_KEY")
            and os.getenv(f"{up}_MODEL")
        ):
            available.append(name)
    return available
