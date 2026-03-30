"""Backtest report generation — charts and summary."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

from metrics import compute_metrics

logger = logging.getLogger(__name__)


def plot_nav_curve(
    nav: pd.Series,
    benchmarks: dict[str, pd.Series],
    output_path: Path,
) -> None:
    """Plot NAV curve vs benchmarks."""
    fig, ax = plt.subplots(figsize=(14, 7))

    # Normalize all to 1.0 at start
    nav_norm = nav / nav.iloc[0]
    ax.plot(nav_norm.index, nav_norm.values, label="Strategy", linewidth=2, color="darkblue")

    colors = ["grey", "orange", "green"]
    for i, (name, bench) in enumerate(benchmarks.items()):
        if len(bench) > 0:
            bench_norm = bench / bench.iloc[0]
            ax.plot(bench_norm.index, bench_norm.values,
                    label=name, linewidth=1, alpha=0.7, color=colors[i % len(colors)])

    ax.set_title("Portfolio NAV vs Benchmarks", fontsize=14)
    ax.set_ylabel("Normalized Value (start = 1.0)")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path / "nav_curve.png", dpi=150)
    plt.close(fig)
    logger.info("Saved NAV curve to %s", output_path / "nav_curve.png")


def plot_drawdown(nav: pd.Series, output_path: Path) -> None:
    """Plot drawdown chart."""
    cummax = nav.cummax()
    drawdown = (nav - cummax) / cummax

    fig, ax = plt.subplots(figsize=(14, 4))
    ax.fill_between(drawdown.index, drawdown.values, 0, alpha=0.4, color="red")
    ax.set_title("Drawdown", fontsize=14)
    ax.set_ylabel("Drawdown %")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path / "drawdown.png", dpi=150)
    plt.close(fig)
    logger.info("Saved drawdown chart to %s", output_path / "drawdown.png")


def generate_report(
    nav: pd.Series,
    benchmarks: dict[str, pd.Series],
    decisions: dict[str, dict],
    trades: list[dict],
    output_path: Path,
    params: dict | None = None,
) -> None:
    """Generate full backtest report: charts + markdown summary."""
    output_path.mkdir(parents=True, exist_ok=True)

    # Charts
    plot_nav_curve(nav, benchmarks, output_path)
    plot_drawdown(nav, output_path)

    # Metrics
    csi300 = benchmarks.get("CSI 300")
    metrics = compute_metrics(nav, csi300)

    # Write markdown report
    lines = ["# Backtest Report\n"]

    if params:
        lines.append("## Parameters\n")
        for k, v in params.items():
            lines.append(f"- **{k}**: {v}")
        lines.append("")

    lines.append("## Performance Metrics\n")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    fmt_map = {
        "cumulative_return": ("Cumulative Return", "{:.2%}"),
        "cagr": ("CAGR", "{:.2%}"),
        "volatility": ("Volatility", "{:.2%}"),
        "sharpe_ratio": ("Sharpe Ratio", "{:.2f}"),
        "max_drawdown": ("Max Drawdown", "{:.2%}"),
        "alpha": ("Alpha", "{:.2%}"),
        "beta": ("Beta", "{:.2f}"),
        "information_ratio": ("Information Ratio", "{:.2f}"),
        "benchmark_return": ("CSI 300 Return", "{:.2%}"),
    }
    for key, (label, fmt) in fmt_map.items():
        val = metrics.get(key)
        if val is not None:
            lines.append(f"| {label} | {fmt.format(val)} |")
    lines.append("")

    # Decisions timeline
    lines.append("## Decision Timeline\n")
    for dt, portfolio in sorted(decisions.items()):
        lines.append(f"### {dt}\n")
        if isinstance(portfolio, dict):
            for ticker, weight in portfolio.items():
                lines.append(f"- {ticker}: {weight:.0%}")
        lines.append("")

    # Trade log
    if trades:
        lines.append("## Trade Log\n")
        lines.append("| Date | Ticker | Action | Size | Price | PnL |")
        lines.append("|------|--------|--------|------|-------|-----|")
        for t in trades[:50]:  # limit to first 50
            lines.append(
                f"| {t.get('date', '')} | {t.get('ticker', '')} | "
                f"{t.get('action', '')} | {t.get('size', '')} | "
                f"{t.get('price', '')} | {t.get('pnl', '')} |"
            )
        lines.append("")

    report_text = "\n".join(lines)
    (output_path / "report.md").write_text(report_text, encoding="utf-8")
    logger.info("Saved report to %s", output_path / "report.md")

    # Also save raw metrics as JSON
    with open(output_path / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2, default=str)
