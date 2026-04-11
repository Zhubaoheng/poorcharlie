#!/usr/bin/env python3
"""Full backtest orchestrator: S0→S4 with trigger detection.

Runs the complete backtest pipeline:
1. S0 cold start: screen top N stocks, full pipeline, portfolio decision
2. S1-S4 incremental: re-analyze holdings + WATCHLIST+, portfolio rebalance
3. Between scans: price triggers (held) + valuation triggers (watchlist)
4. Generate all_decisions.json for backtrader replay

Each scan is a separate run_overnight.py invocation with checkpoint/resume.
Can be interrupted and resumed at any point.

Usage:
    uv run python scripts/backtest/run_full_backtest.py --top 2500
    uv run python scripts/backtest/run_full_backtest.py --top 200   # quick test
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

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
logger = logging.getLogger("full_backtest")

# Scan dates: 2-3 weeks after earnings deadlines
SCAN_DATES = [
    date(2023, 11, 18),   # S0: cold start
    date(2024, 5, 20),    # S1: FY2023 年报
    date(2024, 9, 23),    # S2: H1 2024 半年报
    date(2025, 5, 19),    # S3: FY2024 年报
    date(2025, 9, 22),    # S4: H1 2025 半年报
]

DATA_DIR = PROJECT_ROOT / "data" / "full_backtest"
OVERNIGHT_SCRIPT = PROJECT_ROOT / "scripts" / "run_overnight.py"


def _find_latest_run(as_of_date: str) -> Path | None:
    """Find the latest completed run for a given as_of_date."""
    runs_dir = PROJECT_ROOT / "data" / "runs"
    if not runs_dir.exists():
        return None
    candidates = []
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        run_json = run_dir / "run.json"
        if not run_json.exists():
            continue
        try:
            meta = json.loads(run_json.read_text())
            if meta.get("as_of_date") == as_of_date and meta.get("status") == "completed":
                candidates.append(run_dir)
        except Exception:
            pass
    if not candidates:
        return None
    return sorted(candidates)[-1]


def _run_overnight(
    top_n: int,
    as_of_date: date,
    pipeline_concurrency: int,
    screening_concurrency: int,
    incremental_from: str | None = None,
) -> Path | None:
    """Run run_overnight.py as subprocess. Returns run directory on success."""
    cmd = [
        sys.executable, str(OVERNIGHT_SCRIPT),
        "--top", str(top_n),
        "--as-of-date", as_of_date.isoformat(),
        "--pipeline-concurrency", str(pipeline_concurrency),
        "--screening-concurrency", str(screening_concurrency),
    ]
    if incremental_from:
        cmd.extend(["--incremental", incremental_from])

    logger.info("=" * 70)
    logger.info("Running: %s", " ".join(cmd[-8:]))  # show key args
    logger.info("=" * 70)

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))

    if result.returncode != 0:
        logger.error("run_overnight.py failed with exit code %d", result.returncode)
        return None

    # Find the completed run
    run_dir = _find_latest_run(as_of_date.isoformat())
    if run_dir:
        logger.info("Completed: %s", run_dir.name)
    return run_dir


def _detect_triggers(
    prev_store_path: Path,
    scan_start: date,
    scan_end: date,
    all_decisions: dict,
) -> None:
    """Detect price + valuation triggers between scan dates."""
    from investagent.store.candidate_store import CandidateStore
    from run_precompute import detect_valuation_triggers
    from data_feeds import fetch_daily_prices

    store = CandidateStore(prev_store_path)
    holdings = store.get_current_holdings()
    held_tickers = {h.ticker: h.target_weight for h in holdings}

    logger.info("--- Triggers: %s → %s ---", scan_start, scan_end)

    # Price triggers for held stocks
    if held_tickers:
        logger.info("Monitoring %d held stocks for price triggers", len(held_tickers))
        for ticker in held_tickers:
            try:
                df = fetch_daily_prices(ticker, scan_start, scan_end)
                if df.empty:
                    continue
                entry_close = df.iloc[0]["close"]
                for _, row in df.iterrows():
                    close = row["close"]
                    dt = str(row["date"])[:10]
                    pct = (close - entry_close) / entry_close
                    if pct <= -0.20:
                        logger.info("Price trigger DOWN: %s on %s (%.1f%%)", ticker, dt, pct * 100)
                        break
                    elif pct >= 0.50:
                        logger.info("Price trigger UP: %s on %s (+%.1f%%)", ticker, dt, pct * 100)
                        break
            except Exception:
                logger.warning("Price trigger failed for %s", ticker, exc_info=True)

    # Valuation triggers for WATCHLIST+ unheld
    watchlist = store.get_valuation_watchlist()
    if watchlist:
        logger.info("Monitoring %d WATCHLIST+ stocks for valuation triggers", len(watchlist))
        try:
            val_triggers = detect_valuation_triggers(watchlist, scan_start, scan_end)
            for trigger_date, ticker in val_triggers:
                c = store._state.candidates.get(ticker)
                if c and c.final_label in ("INVESTABLE", "DEEP_DIVE"):
                    trigger_alloc = dict(
                        (h.ticker, h.target_weight) for h in holdings
                    )
                    trigger_alloc[ticker] = 0.05
                    all_decisions[trigger_date.isoformat()] = trigger_alloc
                    logger.info("Valuation trigger: %s on %s → 5%% trial", ticker, trigger_date)
        except Exception:
            logger.warning("Valuation trigger detection failed", exc_info=True)
    else:
        logger.info("No valuation watchlist")


def _extract_allocations(run_dir: Path) -> dict[str, float]:
    """Extract portfolio allocations from a completed run."""
    store_path = run_dir / "candidate_store.json"
    if not store_path.exists():
        return {}
    try:
        data = json.loads(store_path.read_text())
        return {h["ticker"]: h["target_weight"] for h in data.get("holdings", [])}
    except Exception:
        return {}


def main():
    parser = argparse.ArgumentParser(description="Full backtest S0→S4")
    parser.add_argument("--top", type=int, default=2500, help="Top N stocks for cold start")
    parser.add_argument("--pipeline-concurrency", type=int, default=10)
    parser.add_argument("--screening-concurrency", type=int, default=20)
    parser.add_argument("--start-from", type=int, default=0,
                        help="Start from scan index (0=S0, 1=S1, ...). Use to resume.")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    all_decisions: dict[str, dict[str, float]] = {}

    # Load existing decisions if resuming
    decisions_file = DATA_DIR / "all_decisions.json"
    if decisions_file.exists() and args.start_from > 0:
        all_decisions = json.loads(decisions_file.read_text())
        logger.info("Loaded %d existing decisions", len(all_decisions))

    prev_run_dir: Path | None = None

    for i, scan_date in enumerate(SCAN_DATES):
        if i < args.start_from:
            # Find existing run for skipped scans
            prev_run_dir = _find_latest_run(scan_date.isoformat())
            if prev_run_dir:
                alloc = _extract_allocations(prev_run_dir)
                all_decisions[scan_date.isoformat()] = alloc
                logger.info("Skipped S%d (%s): loaded from %s (%d positions)",
                            i, scan_date, prev_run_dir.name, len(alloc))
            continue

        logger.info("")
        logger.info("=" * 70)
        logger.info("SCAN S%d: %s %s", i, scan_date,
                     "(COLD START)" if i == 0 else "(INCREMENTAL)")
        logger.info("=" * 70)

        # Run pipeline
        if i == 0:
            # Cold start: full screening
            run_dir = _run_overnight(
                top_n=args.top,
                as_of_date=scan_date,
                pipeline_concurrency=args.pipeline_concurrency,
                screening_concurrency=args.screening_concurrency,
            )
        else:
            # Incremental: only WATCHLIST+ from previous run
            if prev_run_dir is None:
                logger.error("No previous run found for incremental scan")
                break
            store_path = prev_run_dir / "candidate_store.json"
            if not store_path.exists():
                logger.error("No candidate_store.json in %s", prev_run_dir)
                break
            run_dir = _run_overnight(
                top_n=args.top,
                as_of_date=scan_date,
                pipeline_concurrency=args.pipeline_concurrency,
                screening_concurrency=args.screening_concurrency,
                incremental_from=str(store_path),
            )

        if run_dir is None:
            logger.error("Scan S%d failed", i)
            break

        # Record allocations
        alloc = _extract_allocations(run_dir)
        all_decisions[scan_date.isoformat()] = alloc
        logger.info("S%d portfolio: %d positions, %.0f%% cash",
                     i, len(alloc), (1 - sum(alloc.values())) * 100)

        # Detect triggers between this scan and next
        if i < len(SCAN_DATES) - 1:
            next_date = SCAN_DATES[i + 1]
            store_path = run_dir / "candidate_store.json"
            if store_path.exists():
                _detect_triggers(store_path, scan_date, next_date, all_decisions)

        prev_run_dir = run_dir

        # Save progress after each scan
        decisions_file.write_text(
            json.dumps(all_decisions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Saved %d decision points to %s", len(all_decisions), decisions_file)

    # Final summary
    logger.info("")
    logger.info("=" * 70)
    logger.info("BACKTEST COMPLETE: %d decision points", len(all_decisions))
    logger.info("=" * 70)
    for dt in sorted(all_decisions.keys()):
        alloc = all_decisions[dt]
        n = len(alloc)
        cash = 1 - sum(alloc.values())
        tickers = ", ".join(f"{t}({w:.0%})" for t, w in sorted(alloc.items(), key=lambda x: -x[1])[:5])
        more = f" +{n-5} more" if n > 5 else ""
        logger.info("  %s: %d positions, %.0f%% cash | %s%s", dt, n, cash * 100, tickers, more)

    logger.info("")
    logger.info("Next step: run backtrader replay:")
    logger.info("  uv run python scripts/backtest/run_backtest.py")


if __name__ == "__main__":
    main()
