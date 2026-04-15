"""Decision file schema — read/write helpers for all_decisions.json.

Schema v1.1 format:
{
    "schema_version": "1.1",
    "decisions": {
        "2023-11-18": {
            "source": "scan" | "opportunity_trigger" | "price_obs",
            "scan_id": "S0" | None,
            "run_id": "overnight_..." | "opp_...",
            "weights": {"600566": 0.10, ...},
            "cash": 0.60,
            "rationale": "..."
            # optional: trigger_ticker, trigger_reason
        },
        ...
    }
}

Legacy v1.0 format (still readable for backward compat):
{"2023-11-18": {"600566": 0.10, ...}, ...}
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CURRENT_SCHEMA = "1.1"
TOLERANCE = 1e-6
# LLM-generated weights (from PortfolioStrategyAgent) can overshoot 1.0 by a
# few percent due to rounding / arithmetic imprecision. Scale them down silently
# up to this threshold; beyond it, treat as a bug and raise.
RENORMALIZE_MAX_OVERFLOW = 0.10  # up to +10% overflow → auto-scale


def load_decisions(path: Path) -> dict[str, dict[str, Any]]:
    """Load and normalize decisions to v1.1 dict keyed by date string.

    Each value contains at least {source, weights, cash}.
    Legacy files are auto-upgraded in-memory (file not touched).
    """
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _normalize(raw)


def load_weights_only(path: Path) -> dict[str, dict[str, float]]:
    """Backward-compat reader: returns {date: {ticker: weight}}.

    Use this for consumers (backtrader strategy) that haven't migrated yet.
    """
    decisions = load_decisions(path)
    return {d: rec["weights"] for d, rec in decisions.items()}


def save_decisions(path: Path, decisions: dict[str, dict[str, Any]]) -> None:
    """Write decisions in v1.1 format, validating weight invariants."""
    for d, rec in decisions.items():
        _validate_record(d, rec)
    payload = {"schema_version": CURRENT_SCHEMA, "decisions": decisions}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_record(
    source: str,
    weights: dict[str, float],
    *,
    scan_id: str | None = None,
    run_id: str | None = None,
    rationale: str = "",
    trigger_ticker: str | None = None,
    trigger_reason: str | None = None,
) -> dict[str, Any]:
    """Build a decision record dict with computed cash and validated weights.

    Small LLM-induced overshoot (<=10%) is auto-scaled down; larger overshoot
    is treated as a bug and raises.
    """
    weights = dict(weights)  # defensive copy before possible renormalize
    total = sum(weights.values())
    overflow = total - 1.0
    if overflow > RENORMALIZE_MAX_OVERFLOW:
        raise ValueError(
            f"weights sum {total:.3f} > 1.0 by {overflow:.3f} "
            f"(cap {RENORMALIZE_MAX_OVERFLOW:.0%}); likely a bug: {weights}"
        )
    if overflow > TOLERANCE:
        logger.warning(
            "weights sum %.3f > 1.0 by %.3f (n=%d); scaling down to 1.0",
            total, overflow, len(weights),
        )
        scale = 1.0 / total
        weights = {t: w * scale for t, w in weights.items()}
        total = 1.0
    cash = max(0.0, 1.0 - total)
    rec: dict[str, Any] = {
        "source": source,
        "weights": dict(weights),
        "cash": cash,
    }
    if scan_id is not None:
        rec["scan_id"] = scan_id
    if run_id is not None:
        rec["run_id"] = run_id
    if rationale:
        rec["rationale"] = rationale
    if trigger_ticker is not None:
        rec["trigger_ticker"] = trigger_ticker
    if trigger_reason is not None:
        rec["trigger_reason"] = trigger_reason
    return rec


def _normalize(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Upgrade legacy to v1.1 in memory, or pass through if already v1.1."""
    if isinstance(raw, dict) and raw.get("schema_version") == CURRENT_SCHEMA:
        return raw["decisions"]
    # Legacy: {date: {ticker: weight}}
    upgraded = {}
    for date_str, val in raw.items():
        if date_str == "schema_version" or date_str == "decisions":
            continue
        if not isinstance(val, dict):
            continue
        # Detect if it's already a record (has "weights" key) vs raw ticker map
        if "weights" in val:
            upgraded[date_str] = val
        else:
            # Treat as raw {ticker: weight}
            weights = {t: float(w) for t, w in val.items() if isinstance(w, (int, float))}
            upgraded[date_str] = make_record(
                source="legacy",
                weights=weights,
                rationale="imported from v1.0 format",
            )
    return upgraded


def _validate_record(date_str: str, rec: dict[str, Any]) -> None:
    required = {"source", "weights", "cash"}
    missing = required - set(rec.keys())
    if missing:
        raise ValueError(f"decision {date_str} missing fields: {missing}")
    w_sum = sum(rec["weights"].values())
    total = w_sum + rec["cash"]
    if abs(total - 1.0) > TOLERANCE:
        raise ValueError(
            f"decision {date_str} invariant broken: weights+cash = {total:.6f} != 1.0",
        )
