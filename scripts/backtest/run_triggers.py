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
    "csindex.com.cn,legulegu.com,baostock.com"
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
    # Opportunity triggers (WATCHLIST+ unheld crosses IV × 0.8)
    # ------------------------------------------------------------------
    watchlist = store.get_valuation_watchlist()
    if not watchlist:
        logger.info("No valuation watchlist")
        return

    logger.info(
        "Monitoring %d WATCHLIST+ stocks for opportunity triggers",
        len(watchlist),
    )
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

    logger.info("Processing %d opportunity triggers with full pipeline re-eval", len(val_triggers))
    for trigger_date, ticker in val_triggers:
        c = store._state.candidates.get(ticker)
        if not c or c.final_label not in ("INVESTABLE", "DEEP_DIVE", "WATCHLIST", "SPECIAL_SITUATION"):
            continue

        trig_dir = trigger_output_dir / f"opp_{trigger_date.isoformat()}_{ticker}"
        try:
            outcome = await reevaluate_ticker(
                ticker=ticker,
                trigger_date=trigger_date,
                prev_store_path=prev_store_path,
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
    ))
    added = len(all_decisions) - before

    save_decisions(decisions_path, all_decisions)
    logger.info(
        "Added %d trigger decisions; total %d → %s",
        added, len(all_decisions), decisions_path,
    )


if __name__ == "__main__":
    main()
