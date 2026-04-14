"""Replay S0→S1 backtest using existing pipeline checkpoints.

Loads pre-computed pipeline results from overnight runs, runs
decision pipeline (Phase 5) at each scan point, detects valuation
triggers between scans, and outputs all_decisions.json for backtrader.

Usage:
    uv run python scripts/backtest/run_replay_s0_s1.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for data_feeds, temporal, etc.

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# Bypass proxy for domestic sites
_NO_PROXY = (
    "cninfo.com.cn,static.cninfo.com.cn,"
    "eastmoney.com,push2.eastmoney.com,push2his.eastmoney.com,"
    "10jqka.com.cn,sina.com.cn,finance.sina.com.cn,"
    "csindex.com.cn,legulegu.com,baostock.com"
)
os.environ.setdefault("NO_PROXY", _NO_PROXY)
os.environ.setdefault("no_proxy", _NO_PROXY)

from poorcharlie.config import create_llm_client
from poorcharlie.schemas.candidate import PortfolioHolding
from poorcharlie.store.candidate_store import CandidateStore
from poorcharlie.workflow.decision_pipeline import run_decision_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("replay")

# Scan points and their checkpoint directories
SCANS = [
    {
        "date": date(2023, 11, 6),
        "checkpoint_dir": Path("data/runs/overnight_20260410T174855_7302/checkpoints/pipeline"),
        "label": "S0: 2023-11-06 冷启动",
    },
    {
        "date": date(2024, 5, 6),
        "checkpoint_dir": Path("data/runs/overnight_20260411T120654_9a37/checkpoints/pipeline"),
        "label": "S1: 2024-05-06 增量",
    },
]

DATA_DIR = Path("data/backtest_replay")


def load_pipeline_results(checkpoint_dir: Path) -> list[dict]:
    """Load all pipeline checkpoint results."""
    results = []
    for f in checkpoint_dir.glob("*.json"):
        try:
            results.append(json.load(open(f)))
        except Exception:
            pass
    return results


async def run_scan_decision(
    scan: dict,
    store: CandidateStore,
    llm,
) -> dict[str, float]:
    """Run decision pipeline for one scan point."""
    logger.info("=" * 60)
    logger.info("%s", scan["label"])
    logger.info("=" * 60)

    # Load pipeline results into store
    results = load_pipeline_results(scan["checkpoint_dir"])
    store.ingest_scan_results(results, scan["date"])
    logger.info("Ingested %d pipeline results", len(results))

    actionable = store.get_actionable_candidates()
    holdings = store.get_current_holdings()
    logger.info("Actionable: %d, Current holdings: %d", len(actionable), len(holdings))

    # Run decision pipeline
    allocations = await run_decision_pipeline(store, llm, scan_date=scan["date"])

    # Log results
    logger.info("Portfolio:")
    for t, w in sorted(allocations.items(), key=lambda x: -x[1]):
        c = store._state.candidates.get(t)
        name = c.name if c else "?"
        fl = c.final_label if c else "?"
        logger.info("  %s %s [%s] %.0f%%", t, name, fl, w * 100)
    cash = 1.0 - sum(allocations.values())
    logger.info("  Cash: %.0f%%", cash * 100)

    return allocations


async def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    store = CandidateStore(DATA_DIR / "candidate_store.json")
    llm = create_llm_client()  # picks LLM_DEFAULT_PROFILE

    all_decisions: dict[str, dict[str, float]] = {}

    for i, scan in enumerate(SCANS):
        # Run decision pipeline
        allocations = await run_scan_decision(scan, store, llm)
        all_decisions[scan["date"].isoformat()] = allocations

        # Detect triggers between this scan and next
        if i < len(SCANS) - 1:
            next_scan = SCANS[i + 1]
            logger.info("")
            logger.info("--- Triggers: %s → %s ---", scan["date"], next_scan["date"])

            from run_precompute import detect_valuation_triggers
            from data_feeds import fetch_daily_prices

            # 1. Price triggers for held stocks (±20% from entry)
            held_tickers = list(allocations.keys())
            if held_tickers:
                logger.info("Monitoring %d held stocks for price triggers", len(held_tickers))
                for ticker in held_tickers:
                    try:
                        df = fetch_daily_prices(ticker, scan["date"], next_scan["date"])
                        if df.empty:
                            continue
                        entry_close = df.iloc[0]["close"]
                        for _, row in df.iterrows():
                            close = row["close"]
                            dt = str(row["date"])[:10]
                            pct = (close - entry_close) / entry_close
                            if pct <= -0.20:
                                logger.info("Price trigger DOWN: %s on %s (%.1f%%)", ticker, dt, pct * 100)
                                # For replay: keep current allocation (would re-run pipeline in full mode)
                                break
                            elif pct >= 0.50:
                                logger.info("Price trigger UP: %s on %s (+%.1f%%)", ticker, dt, pct * 100)
                                break
                    except Exception:
                        logger.warning("Price trigger check failed for %s", ticker, exc_info=True)

            # 2. Valuation triggers for WATCHLIST+ unheld stocks
            watchlist = store.get_valuation_watchlist()
            if watchlist:
                logger.info("Monitoring %d WATCHLIST+ stocks for valuation triggers", len(watchlist))
                val_triggers = detect_valuation_triggers(
                    watchlist, scan["date"], next_scan["date"],
                )
                for trigger_date, ticker in val_triggers:
                    logger.info("Valuation trigger: %s on %s → adding to decisions", ticker, trigger_date)
                    # In replay mode: can't re-run pipeline, but record as a
                    # potential entry point. Use existing analysis + 5% trial.
                    c = store._state.candidates.get(ticker)
                    if c and c.final_label in ("INVESTABLE", "DEEP_DIVE"):
                        trigger_alloc = dict(allocations)  # copy current
                        trigger_alloc[ticker] = 0.05  # trial position
                        all_decisions[trigger_date.isoformat()] = trigger_alloc
                        logger.info("  → Added %s 5%% trial position on %s", ticker, trigger_date)
            else:
                logger.info("No valuation watchlist (no trigger ratios in data)")

    # Save all decisions
    decisions_file = DATA_DIR / "all_decisions.json"
    decisions_file.write_text(
        json.dumps(all_decisions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("")
    logger.info("=" * 60)
    logger.info("Saved %d decision points to %s", len(all_decisions), decisions_file)

    # Summary
    logger.info("")
    logger.info("=== Decision Summary ===")
    for dt, alloc in sorted(all_decisions.items()):
        positions = len(alloc)
        cash = 1.0 - sum(alloc.values())
        tickers = ", ".join(f"{t}({w:.0%})" for t, w in sorted(alloc.items(), key=lambda x: -x[1]))
        logger.info("  %s: %d positions, %.0f%% cash | %s", dt, positions, cash * 100, tickers)


if __name__ == "__main__":
    asyncio.run(main())
