"""Run backtrader replay from pre-computed decisions.

Phase 2 of the backtest: read all_decisions.json, set up backtrader with
historical price data, and generate performance reports.

Usage:
    uv run python scripts/backtest/run_backtest.py [--initial-cash 1000000]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import backtrader as bt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy import MungerStrategy, BacktestCommission
from data_feeds import fetch_daily_prices, fetch_benchmark, fetch_risk_free_rate
from metrics import compute_metrics, compute_trade_stats
from report import generate_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("backtest")

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "backtest"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "backtest_results"

START_DATE = date(2024, 5, 1)
END_DATE = date(2025, 9, 30)


def _load_decisions() -> dict[str, dict[str, float]]:
    """Load all_decisions.json from precompute phase."""
    decisions_file = DATA_DIR / "all_decisions.json"
    if not decisions_file.exists():
        logger.error("No decisions file found at %s. Run run_precompute.py first.", decisions_file)
        return {}
    data = json.loads(decisions_file.read_text(encoding="utf-8"))
    logger.info("Loaded decisions for %d dates: %s", len(data), sorted(data.keys()))
    return data


def _collect_tickers(decisions: dict[str, dict[str, float]]) -> set[str]:
    tickers = set()
    for alloc in decisions.values():
        tickers.update(alloc.keys())
    return tickers


def _build_nav_from_cerebro(cerebro, strategy, initial_cash: float) -> pd.Series:
    """Extract daily NAV series from backtrader analyzers."""
    returns_analysis = strategy.analyzers.returns.get_analysis()
    if not returns_analysis:
        return pd.Series(dtype=float)

    dates = sorted(returns_analysis.keys())
    nav_values = [initial_cash]
    for d in dates:
        nav_values.append(nav_values[-1] * (1 + returns_analysis[d]))

    # Convert dates to proper datetime
    dt_index = []
    for d in dates:
        if isinstance(d, datetime):
            dt_index.append(d)
        else:
            dt_index.append(pd.Timestamp(d))

    return pd.Series(nav_values[1:], index=pd.DatetimeIndex(dt_index))


def _extract_trades(strategy) -> list[dict]:
    """Extract closed trade records from backtrader."""
    trades = []
    try:
        analysis = strategy.analyzers.trades.get_analysis()
        # TradeAnalyzer provides aggregate stats; individual trades come from notify_trade
        # For now return summary stats
        total = analysis.get("total", {})
        if total:
            trades.append({
                "type": "summary",
                "total_closed": total.get("closed", 0),
                "total_open": total.get("open", 0),
            })
    except Exception:
        pass
    return trades


def run_backtest(initial_cash: float = 1_000_000) -> None:
    """Execute the backtrader backtest."""
    decisions = _load_decisions()
    if not decisions:
        return

    tickers = _collect_tickers(decisions)
    logger.info("Need price data for %d tickers: %s", len(tickers), sorted(tickers)[:10])

    cerebro = bt.Cerebro()
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.addcommissioninfo(BacktestCommission())

    # Load price data for each ticker
    loaded = 0
    for ticker in sorted(tickers):
        logger.info("Fetching prices for %s...", ticker)
        df = fetch_daily_prices(ticker, START_DATE, END_DATE)
        if df.empty or len(df) < 10:
            logger.warning("Insufficient price data for %s (%d rows), skipping", ticker, len(df))
            continue

        df = df.set_index("date")
        data = bt.feeds.PandasData(
            dataname=df,
            name=ticker,
            fromdate=datetime.combine(START_DATE, datetime.min.time()),
            todate=datetime.combine(END_DATE, datetime.min.time()),
        )
        cerebro.adddata(data)
        loaded += 1

    if loaded == 0:
        logger.error("No price data loaded. Cannot run backtest.")
        return

    logger.info("Loaded price data for %d / %d tickers", loaded, len(tickers))

    # Strategy + analyzers
    cerebro.addstrategy(MungerStrategy, decisions=decisions)
    cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="returns", timeframe=bt.TimeFrame.Days)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

    # Run
    logger.info("Starting backtest: cash=%.0f, %s to %s", initial_cash, START_DATE, END_DATE)
    results = cerebro.run()
    strategy = results[0]

    final_value = cerebro.broker.getvalue()
    total_return = final_value / initial_cash - 1
    logger.info("Final value: %.2f (return: %.2f%%)", final_value, total_return * 100)

    # Build NAV series
    nav = _build_nav_from_cerebro(cerebro, strategy, initial_cash)
    if nav.empty:
        logger.warning("No NAV data available for reporting")
        return

    # Fetch benchmarks
    logger.info("Fetching benchmark data...")
    benchmarks: dict[str, pd.Series] = {}
    for name, code in [("CSI 300", "000300"), ("Hang Seng", "HSI"), ("S&P 500", "SPX")]:
        try:
            bench = fetch_benchmark(code, START_DATE, END_DATE)
            if not bench.empty:
                benchmarks[name] = bench.set_index("date")["close"]
                logger.info("Loaded benchmark %s: %d rows", name, len(bench))
        except Exception:
            logger.warning("Failed to load benchmark %s", name, exc_info=True)

    # Generate report
    trades = _extract_trades(strategy)
    rfr = fetch_risk_free_rate(2024)

    generate_report(
        nav=nav,
        benchmarks=benchmarks,
        decisions=decisions,
        trades=trades,
        output_path=OUTPUT_DIR,
        params={
            "initial_cash": f"¥{initial_cash:,.0f}",
            "start_date": str(START_DATE),
            "end_date": str(END_DATE),
            "model": "DeepSeek R1 250528 (知识截止 2023.10, 禁用联网)",
            "slippage": "~0.15% per side",
            "risk_free_rate": f"{rfr:.2%}",
            "scan_dates": ", ".join(sorted(decisions.keys())),
            "total_decision_points": len(decisions),
        },
    )

    logger.info("Backtest complete. Results saved to %s", OUTPUT_DIR)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run backtrader backtest replay")
    parser.add_argument("--initial-cash", type=float, default=1_000_000)
    args = parser.parse_args()
    run_backtest(initial_cash=args.initial_cash)
