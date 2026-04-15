#!/usr/bin/env python3
"""Between-scan trigger processing — Munger-style.

- Price observations on held stocks: LOG ONLY (volatility != risk).
  Holdings are only re-evaluated at scheduled fundamental checkpoints.
- Valuation opportunities on unheld WATCHLIST+: when price crosses IV × 0.8,
  run a single-ticker pipeline re-eval and let PortfolioStrategy decide
  (no more hardcoded 5% trial position).

Writes decisions into all_decisions.json (schema v1.1, merged, not overwritten).

Usage:
    uv run python scripts/backtest/run_triggers.py \\
        --store data/runs/overnight_XXX/candidate_store.json \\
        --start 2023-11-18 --end 2024-05-20 \\
        --decisions data/full_backtest/all_decisions.json \\
        --output-dir data/full_backtest/triggers \\
        [--no-opportunity]   # disable opportunity re-eval (detection only)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

_NO_PROXY = (
    "cninfo.com.cn,static.cninfo.com.cn,"
    "eastmoney.com,push2.eastmoney.com,push2his.eastmoney.com,"
    "10jqka.com.cn,sina.com.cn,finance.sina.com.cn,"
    "csindex.com.cn,legulegu.com,baostock.com,"
    "minimaxi.com,api.minimaxi.com,"
    "deepseek.com,api.deepseek.com"
)
os.environ.setdefault("NO_PROXY", _NO_PROXY)
os.environ.setdefault("no_proxy", _NO_PROXY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("triggers")


async def run_triggers(
    prev_store_path: Path,
    scan_start: date,
    scan_end: date,
    all_decisions: dict,
    trigger_output_dir: Path,
    enable_opportunity: bool = True,
    concurrency: int | None = None,
) -> None:
    """Observe holdings (log-only) and process opportunity triggers."""
    from data_feeds import fetch_daily_prices
    from decision_schema import make_record
    from poorcharlie.store.candidate_store import CandidateStore
    from run_precompute import detect_valuation_triggers

    store = CandidateStore(prev_store_path)
    holdings = store.get_current_holdings()

    logger.info("--- Triggers: %s → %s ---", scan_start, scan_end)

    # ------------------------------------------------------------------
    # Price observations (diagnostics only, no allocation changes)
    # ------------------------------------------------------------------
    if holdings:
        logger.info("Observing %d held stocks (log-only)", len(holdings))
        for h in holdings:
            ticker = h.ticker
            try:
                df = fetch_daily_prices(ticker, scan_start, scan_end)
                if df.empty:
                    continue
                if h.entry_price is not None and h.entry_price > 0:
                    entry_close = h.entry_price
                    basis = "holding.entry_price"
                else:
                    entry_close = df.iloc[0]["close"]
                    basis = "interval_first_close(FALLBACK)"
                    logger.warning("%s has no entry_price — using %s", ticker, basis)
                for _, row in df.iterrows():
                    close = row["close"]
                    dt = str(row["date"])[:10]
                    pct = (close - entry_close) / entry_close
                    if pct <= -0.20:
                        logger.info(
                            "Price obs DOWN (no action): %s on %s (%.1f%% vs %s)",
                            ticker, dt, pct * 100, basis,
                        )
                        break
                    elif pct >= 0.50:
                        logger.info(
                            "Price obs UP (no action): %s on %s (+%.1f%% vs %s)",
                            ticker, dt, pct * 100, basis,
                        )
                        break
            except Exception:
                logger.warning("Price observation failed for %s", ticker, exc_info=True)

    # ------------------------------------------------------------------
    # Opportunity triggers (Munger-tiered; see top-of-file docstring)
    # ------------------------------------------------------------------
    #   Label filter:    only INVESTABLE / DEEP_DIVE
    #   Quality filter:  only GREAT / GOOD / AVERAGE
    #   MoS threshold:   GREAT 15%, GOOD 25%, AVERAGE 40%
    # This is recomputed per-candidate at trigger time so older stores
    # whose stored valuation_trigger_ratio was built with the old uniform
    # 20% threshold still use the new tiered logic going forward.
    _MOS_BY_QUALITY = {"GREAT": 0.15, "GOOD": 0.25, "AVERAGE": 0.40}
    _ELIGIBLE_LABELS = ("INVESTABLE", "DEEP_DIVE")

    watchlist_all = store.get_valuation_watchlist()
    watchlist: dict[str, dict] = {}
    for ticker, info in watchlist_all.items():
        c = store._state.candidates.get(ticker)
        if c is None:
            continue
        if c.final_label not in _ELIGIBLE_LABELS:
            continue
        required_mos = _MOS_BY_QUALITY.get(c.enterprise_quality)
        if required_mos is None:
            continue
        iv_base = c.intrinsic_value_base
        scan_close = c.scan_close_price
        if not iv_base or not scan_close or scan_close <= 0:
            continue
        recomputed_ratio = (iv_base * (1 - required_mos)) / scan_close
        watchlist[ticker] = {
            **info,
            "trigger_ratio": recomputed_ratio,
        }

    skipped = len(watchlist_all) - len(watchlist)
    logger.info(
        "Opportunity watchlist: %d candidates after Munger filter "
        "(label∈%s, quality∈%s), %d dropped",
        len(watchlist), _ELIGIBLE_LABELS,
        list(_MOS_BY_QUALITY.keys()), skipped,
    )
    if not watchlist:
        logger.info("No eligible opportunity candidates")
        return

    try:
        val_triggers = detect_valuation_triggers(watchlist, scan_start, scan_end)
    except Exception:
        logger.warning("Valuation trigger detection failed", exc_info=True)
        return

    if not val_triggers:
        logger.info("No opportunity triggers detected")
        return

    if not enable_opportunity:
        logger.info(
            "Detected %d opportunity triggers; --no-opportunity set, skipping re-eval",
            len(val_triggers),
        )
        for td, tk in val_triggers:
            logger.info("  trigger %s on %s (detection-only)", tk, td)
        return

    # Set up pipeline dependencies
    from poorcharlie.config import create_llm_client
    from poorcharlie.datasources.cache import AkShareCache, FilingCache
    from opportunity_trigger import reevaluate_ticker

    llm = create_llm_client()  # picks LLM_DEFAULT_PROFILE
    filing_cache = FilingCache(PROJECT_ROOT / "data" / "cache" / "filings")
    akshare_cache = AkShareCache(PROJECT_ROOT / "data" / "cache" / "akshare")

    # Serial execution with CASCADING baseline: each trigger re-evaluates
    # using the PREVIOUS trigger's (or scan's) output as its starting
    # store. Parallelism would violate the temporal ordering — trigger N+1
    # must see trigger N's allocation to reason about "what to do next".
    # The `concurrency` argument is accepted for API compat but ignored.
    eligible = [
        (td, tk) for td, tk in sorted(val_triggers, key=lambda p: p[0])
        if (c := store._state.candidates.get(tk)) is not None
        and c.final_label in _ELIGIBLE_LABELS
    ]
    logger.info(
        "Processing %d opportunity triggers with cascading baseline (serial)",
        len(eligible),
    )

    # Rolling baseline: starts as the scan store, updates to each
    # successful trigger's output so the next trigger sees the latest
    # allocation state.
    rolling_baseline = prev_store_path

    for trigger_date, ticker in eligible:
        trig_dir = trigger_output_dir / f"opp_{trigger_date.isoformat()}_{ticker}"
        try:
            outcome = await reevaluate_ticker(
                ticker=ticker,
                trigger_date=trigger_date,
                prev_store_path=rolling_baseline,  # ← cascades
                trigger_output_dir=trig_dir,
                llm=llm,
                filing_cache=filing_cache,
                akshare_cache=akshare_cache,
            )
        except Exception:
            logger.warning("Opportunity re-eval failed for %s on %s",
                           ticker, trigger_date, exc_info=True)
            continue
        if outcome is None:
            continue
        allocations, meta = outcome
        rec = make_record(
            source="opportunity_trigger",
            weights=allocations,
            run_id=meta["run_id"],
            rationale=meta["rationale"],
            trigger_ticker=ticker,
            trigger_reason=meta["trigger_reason"],
        )
        all_decisions[trigger_date.isoformat()] = rec
        logger.info(
            "Opportunity %s on %s → %d positions, cash=%.1f%% (label=%s)",
            ticker, trigger_date, len(allocations),
            rec["cash"] * 100, meta["pipeline_label"],
        )
        # Roll the baseline forward so the next trigger sees this
        # trigger's allocation as the starting point.
        new_baseline = trig_dir / "candidate_store.json"
        if new_baseline.exists():
            rolling_baseline = new_baseline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True, help="Path to candidate_store.json")
    parser.add_argument("--start", required=True, help="Scan start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Scan end date YYYY-MM-DD")
    parser.add_argument("--decisions", required=True, help="Path to all_decisions.json")
    parser.add_argument(
        "--output-dir", default=None,
        help="Dir for trigger artifacts (default: <decisions>_triggers)",
    )
    parser.add_argument(
        "--no-opportunity", action="store_true",
        help="Detect but skip opportunity re-eval (diagnostic mode)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=None,
        help="Max concurrent opportunity re-evals (default 3 or "
             "OPPORTUNITY_TRIGGER_CONCURRENCY env)",
    )
    args = parser.parse_args()

    from decision_schema import load_decisions, save_decisions

    store_path = Path(args.store)
    decisions_path = Path(args.decisions)
    if args.output_dir:
        trigger_dir = Path(args.output_dir)
    else:
        trigger_dir = decisions_path.parent / "triggers"

    all_decisions = load_decisions(decisions_path)
    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    before = len(all_decisions)
    asyncio.run(run_triggers(
        store_path, start_date, end_date,
        all_decisions, trigger_dir,
        enable_opportunity=not args.no_opportunity,
        concurrency=args.concurrency,
    ))
    added = len(all_decisions) - before

    save_decisions(decisions_path, all_decisions)
    logger.info(
        "Added %d trigger decisions; total %d → %s",
        added, len(all_decisions), decisions_path,
    )


if __name__ == "__main__":
    main()
