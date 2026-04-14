"""Settings: model names, hurdle rates, thresholds."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Known provider tags. Used only as a label for vendor-specific branching
# (e.g. MiniMax 2056 quota handling). Connection info itself is NOT looked up
# here — caller provides base_url + api_key directly.
_KNOWN_PROVIDERS: tuple[str, ...] = ("claude", "minimax", "deepseek", "openai")


@dataclass
class LLMProviderConfig:
    """Unified LLM connection config: base_url + api_key, plus a provider tag.

    `provider` is preserved separately so downstream code can branch on
    vendor-specific behavior (e.g. MiniMax 2056 quota code) instead of
    sniffing the base_url.
    """

    base_url: str
    api_key: str
    model: str
    provider: str = "openai"
    extra_body: dict[str, Any] = field(default_factory=dict)


def load_llm_config_from_env(prefix: str = "LLM") -> LLMProviderConfig:
    """Load unified LLM config from env.

    Reads ``{prefix}_BASE_URL``, ``{prefix}_API_KEY``, ``{prefix}_MODEL``,
    and optional ``{prefix}_PROVIDER`` (tag for vendor-specific branches,
    default "openai").
    """
    base_url = os.getenv(f"{prefix}_BASE_URL")
    api_key = os.getenv(f"{prefix}_API_KEY")
    model = os.getenv(f"{prefix}_MODEL")
    provider = os.getenv(f"{prefix}_PROVIDER", "openai")

    missing = [k for k, v in [
        (f"{prefix}_BASE_URL", base_url),
        (f"{prefix}_API_KEY", api_key),
        (f"{prefix}_MODEL", model),
    ] if not v]
    if missing:
        raise ValueError(f"Missing required env vars: {', '.join(missing)}")
    if provider not in _KNOWN_PROVIDERS:
        logger.warning(
            "Unknown LLM provider tag %r; vendor-specific branches will not trigger. "
            "Known: %s", provider, _KNOWN_PROVIDERS,
        )

    return LLMProviderConfig(
        base_url=base_url,  # type: ignore[arg-type]
        api_key=api_key,    # type: ignore[arg-type]
        model=model,        # type: ignore[arg-type]
        provider=provider,
    )

# yfinance tickers for 10-year government bond yields
_BOND_TICKERS: dict[str, str] = {
    "CNY": "^TNX",     # fallback to US 10Y; China bond not on yfinance
    "HKD": "^TNX",     # HKD pegged to USD, use US 10Y
    "USD": "^TNX",     # US 10-year Treasury
}

# Hardcoded fallbacks if yfinance unavailable
_FALLBACK_RATES: dict[str, float] = {
    "CNY": 0.022,   # 中国10年期国债 ~2.2%
    "HKD": 0.038,   # 港元跟随美元
    "USD": 0.042,   # 美国10年期国债 ~4.2%
}

# Cache file for risk-free rates (refreshed every 30 days)
_CACHE_FILE = Path.home() / ".cache" / "poorcharlie" / "risk_free_rates.json"
_CACHE_TTL = 30 * 24 * 3600  # 30 days in seconds


def _load_cached_rates() -> dict[str, float] | None:
    """Load rates from cache if fresh enough."""
    try:
        if _CACHE_FILE.exists():
            data = json.loads(_CACHE_FILE.read_text())
            if time.time() - data.get("timestamp", 0) < _CACHE_TTL:
                return data.get("rates", {})
    except Exception:
        pass
    return None


def _save_cached_rates(rates: dict[str, float]) -> None:
    """Save rates to cache."""
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({
            "timestamp": time.time(),
            "rates": rates,
        }))
    except Exception:
        pass


def _fetch_risk_free_rates() -> dict[str, float]:
    """Fetch current risk-free rates. Uses cache, falls back to hardcoded."""
    # Try cache first
    cached = _load_cached_rates()
    if cached:
        logger.debug("Using cached risk-free rates: %s", cached)
        return cached

    # Try yfinance
    rates = dict(_FALLBACK_RATES)
    try:
        import yfinance as yf

        # US 10Y Treasury yield
        tnx = yf.Ticker("^TNX")
        hist = tnx.history(period="5d")
        if not hist.empty:
            us_yield = hist["Close"].iloc[-1] / 100  # ^TNX is in percentage
            rates["USD"] = round(us_yield, 4)
            rates["HKD"] = round(us_yield, 4)  # HKD pegged to USD

        # China 10Y — no direct yfinance ticker, estimate from spread
        # China 10Y ≈ US 10Y - 1.5% to 2% (typical spread)
        rates["CNY"] = round(max(rates["USD"] - 0.018, 0.015), 4)

        logger.info("Fetched risk-free rates: %s", rates)
        _save_cached_rates(rates)

    except Exception:
        logger.debug("Failed to fetch rates, using fallbacks", exc_info=True)

    return rates


class Settings:
    """Project-wide configuration, overridable via environment variables."""

    hurdle_rate: float = 0.10  # fallback if currency unknown
    net_cash_watch_threshold: float = 0.5
    net_cash_priority_threshold: float = 1.0
    net_cash_high_priority_threshold: float = 1.5

    def __init__(self) -> None:
        # LLM config is only required when an LLMClient is actually created.
        # Settings itself tolerates missing vars so unrelated tests can
        # instantiate it freely.
        self.provider: str = os.getenv("LLM_PROVIDER", "openai")
        self.model_name: str | None = os.getenv("LLM_MODEL")
        self.api_base_url: str | None = os.getenv("LLM_BASE_URL")
        self.api_key: str | None = os.getenv("LLM_API_KEY")
        self.max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))

        # Risk-free rates (cached, refreshed monthly)
        self.risk_free_rates: dict[str, float] = _fetch_risk_free_rates()

    def get_hurdle_rate(self, currency: str = "USD") -> float:
        """Return 2× risk-free rate for the given currency."""
        rfr = self.risk_free_rates.get(currency, 0.04)
        return round(rfr * 2, 4)


def create_llm_client(
    profile: str | None = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    extra_body: dict[str, Any] | None = None,
    env_prefix: str | None = None,
) -> "LLMClient":
    """Create an LLMClient.

    Resolution priority (highest wins):
        1. Explicit kwargs (base_url / api_key / model / provider / extra_body)
        2. Named profile via ``profile=`` (or LLM_DEFAULT_PROFILE env)
        3. Legacy ``LLM_*`` env via ``env_prefix="LLM"`` (backward compat)

    Typical usage after migration:
        create_llm_client()                 # uses LLM_DEFAULT_PROFILE
        create_llm_client(profile="claude") # force Claude
    """
    from poorcharlie.llm import LLMClient
    from poorcharlie.llm_profiles import load_profile, resolve_default_profile

    # Decide profile: explicit profile kwarg wins. Otherwise, if caller did
    # not pass full connection kwargs and did not force legacy via env_prefix,
    # use LLM_DEFAULT_PROFILE.
    using_explicit_kwargs = (
        base_url is not None and api_key is not None and model is not None
    )
    explicit_profile_requested = profile is not None
    if profile is None and env_prefix is None and not using_explicit_kwargs:
        profile = resolve_default_profile()

    # Try profile first. If the profile's env vars are incomplete:
    #   - caller explicitly asked for this profile → raise (surface the bug)
    #   - we resolved it from LLM_DEFAULT_PROFILE → silently fall through to
    #     legacy LLM_* so existing .env files keep working
    profile_loaded = False
    if profile:
        try:
            p = load_profile(profile)
            base_url = base_url or p.base_url
            api_key = api_key or p.api_key
            model = model or p.model
            provider = provider or p.provider
            if extra_body is None:
                extra_body = dict(p.extra_body)
            profile_loaded = True
        except ValueError:
            if explicit_profile_requested:
                raise

    # Legacy / explicit-prefix path (also covers default-profile fallback)
    if not profile_loaded and not using_explicit_kwargs:
        cfg = load_llm_config_from_env(env_prefix or "LLM")
        base_url = base_url or cfg.base_url
        api_key = api_key or cfg.api_key
        model = model or cfg.model
        provider = provider or cfg.provider
        if extra_body is None:
            extra_body = dict(cfg.extra_body)

    return LLMClient(
        provider=provider or "openai",
        model=model,
        base_url=base_url,
        api_key=api_key,
        extra_body=extra_body or {},
    )
