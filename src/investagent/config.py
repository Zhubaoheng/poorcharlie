"""Settings: model names, hurdle rates, thresholds."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_PROVIDER_DEFAULTS: dict[str, dict[str, str | None]] = {
    "claude": {
        "default_model": "claude-sonnet-4-20250514",
        "base_url": None,
        "api_key_env": None,  # SDK reads ANTHROPIC_API_KEY internally
    },
    "minimax": {
        "default_model": "MiniMax-M2.7-highspeed",
        "base_url_default": "https://api.minimaxi.com/anthropic",
        "base_url_env": "MINIMAX_BASE_URL",
        "api_key_env": "MINIMAX_API_KEY",
    },
}

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
_CACHE_FILE = Path.home() / ".cache" / "investagent" / "risk_free_rates.json"
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
        self.provider: str = os.getenv("INVESTAGENT_PROVIDER", "claude")
        if self.provider not in _PROVIDER_DEFAULTS:
            raise ValueError(
                f"Unknown provider {self.provider!r}. "
                f"Supported: {list(_PROVIDER_DEFAULTS)}"
            )

        prov = _PROVIDER_DEFAULTS[self.provider]
        self.model_name: str = os.getenv(
            "INVESTAGENT_MODEL", prov["default_model"]  # type: ignore[arg-type]
        )
        self.max_tokens: int = int(os.getenv("INVESTAGENT_MAX_TOKENS", "4096"))

        # Base URL
        base_url_env = prov.get("base_url_env")
        if base_url_env:
            self.api_base_url: str | None = os.getenv(
                base_url_env, prov.get("base_url_default")
            )
        else:
            self.api_base_url = prov.get("base_url")

        # API key
        api_key_env = prov.get("api_key_env")
        if api_key_env:
            self.api_key: str | None = os.getenv(api_key_env)
            if not self.api_key:
                raise ValueError(
                    f"Provider {self.provider!r} requires the "
                    f"{api_key_env} environment variable"
                )
        else:
            self.api_key = None

        # Risk-free rates (cached, refreshed monthly)
        self.risk_free_rates: dict[str, float] = _fetch_risk_free_rates()

    def get_hurdle_rate(self, currency: str = "USD") -> float:
        """Return 2× risk-free rate for the given currency."""
        rfr = self.risk_free_rates.get(currency, 0.04)
        return round(rfr * 2, 4)
