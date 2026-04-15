#!/usr/bin/env python3
"""Live terminal dashboard for the running backtest.

Usage:
    uv run python scripts/monitoring/dashboard.py
    uv run python scripts/monitoring/dashboard.py --refresh 3

Auto-refreshes every 5 seconds. Ctrl-C to exit.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console  # noqa: E402
from rich.layout import Layout  # noqa: E402
from rich.live import Live  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402

from scripts.monitoring.backtest_state import (  # noqa: E402
    Snapshot,
    get_snapshot,
)


# --- colour helpers --------------------------------------------------------

def _label_color(label: str) -> str:
    return {
        "INVESTABLE": "bold cyan",
        "DEEP_DIVE": "cyan",
        "WATCHLIST": "yellow",
        "SPECIAL_SITUATION": "magenta",
        "REJECT": "red",
        "TOO_HARD": "dim",
        "STOPPED": "dim",
        "ERROR": "red",
        "INTERRUPTED": "yellow",
    }.get(label, "white")


def _status_color(status: str) -> str:
    return {
        "completed": "green",
        "running": "bold yellow",
        "failed": "red",
        "pending": "dim",
        "done": "green",
    }.get(status, "white")


def _level_color(level: str) -> str:
    return {"ERROR": "red", "WARNING": "yellow", "INFO": "white"}.get(level, "white")


# --- panels ----------------------------------------------------------------

def _header(s: Snapshot) -> Panel:
    t = Text()
    if s.process is None:
        t.append("⚠ no orchestrator running", style="red")
    else:
        t.append(f"PID {s.process.pid}", style="bold")
        t.append(f"  ·  {s.process.elapsed} elapsed", style="dim")
        t.append(f"  ·  RSS {s.process.rss_mb}MB", style="dim")
        t.append(f"  ·  stat={s.process.stat}", style="dim")
    t.append(f"\n{s.now_iso}", style="dim")
    run_text = Text()
    if s.run:
        run_text.append(f"{s.run.run_id}", style="bold")
        run_text.append(f"  status=", style="dim")
        run_text.append(f"{s.run.status}", style=_status_color(s.run.status))
        run_text.append(f"  as_of={s.run.as_of_date}", style="dim")
    else:
        run_text.append("(no runs yet)", style="dim")
    return Panel(Text.assemble(t, "\n", run_text),
                 title="[bold]PoorCharlie Backtest Monitor[/]",
                 border_style="blue")


def _scans_panel(s: Snapshot) -> Panel:
    t = Text()
    if not s.scans:
        t.append("(no scan schedule found)", style="dim")
    else:
        for i, sp in enumerate(s.scans):
            glyph = {"done": "✓", "running": "▶", "pending": "○"}.get(sp.status, "?")
            style = _status_color(sp.status)
            t.append(f" {sp.scan_id}", style="bold " + style)
            t.append(f"{glyph}", style=style)
            t.append(f"({sp.scan_date}) ", style="dim")
            if i < len(s.scans) - 1:
                t.append(" ")

    if s.phase:
        t.append("\n")
        phase_style = "yellow" if s.phase.phase in (5, 6) else "cyan"
        t.append(f"Phase {s.phase.phase} · {s.phase.phase_name}", style=phase_style)
        if s.phase.current_activity:
            t.append(f"\n  ▸ {s.phase.current_activity}", style="dim")

    if s.progress and s.progress.total > 0:
        pct = 100 * s.progress.done / s.progress.total
        filled = int(pct / 100 * 30)
        bar = "█" * filled + "░" * (30 - filled)
        eta = f"  ETA {s.progress.eta_hours:.1f}h" if s.progress.eta_hours else ""
        t.append(f"\n[{bar}] {s.progress.done}/{s.progress.total} ({pct:.0f}%){eta}")

    return Panel(t, title="Scan Timeline", border_style="blue")


def _holdings_panel(s: Snapshot) -> Panel:
    table = Table(
        show_header=True,
        header_style="bold",
        show_edge=False,
        expand=True,
        pad_edge=False,
        collapse_padding=True,
    )
    table.add_column("", style="bold", no_wrap=True, width=14)
    table.add_column("Wt", justify="right", style="bold", width=6)
    table.add_column("Label", no_wrap=True, width=10)
    table.add_column("Q·V·MoS", no_wrap=True, width=14)
    table.add_column("Entry", no_wrap=True, style="dim")

    if not s.holdings:
        table.add_row("(no holdings)", "", "", "", "")
    else:
        total_w = sum(h.weight for h in s.holdings)
        for h in sorted(s.holdings, key=lambda x: -x.weight):
            mos_str = f"{h.margin_of_safety_pct:+.0f}%" if h.margin_of_safety_pct is not None else "?"
            qvmos = f"{h.enterprise_quality or '?'[:1]}·{h.price_vs_value or '?':<5}·{mos_str}"
            entry = f"{h.entry_date} @{h.entry_price:.2f}" if h.entry_price else h.entry_date
            ticker_name = f"{h.ticker} {h.name[:6]}"
            table.add_row(
                ticker_name,
                f"{h.weight*100:.1f}%",
                Text(h.final_label or "?", style=_label_color(h.final_label or "")),
                qvmos,
                entry,
            )
        table.add_row(
            "CASH", f"{(1-total_w)*100:.1f}%", "", "", "",
            style="dim",
        )
    return Panel(table, title=f"Holdings ({len(s.holdings)})", border_style="green")


def _llm_panel(s: Snapshot) -> Panel:
    t = Text()
    if not s.llm:
        t.append("(no LLM stats yet)", style="dim")
        return Panel(t, title="LLM", border_style="blue")
    t.append(f"Calls    {s.llm.calls}\n", style="bold")
    t.append(f"  ok {s.llm.ok}  err ", style="white")
    t.append(f"{s.llm.err}", style="red" if s.llm.err else "white")
    t.append(f"  retry {s.llm.retry}\n")
    t.append(f"Avg      {s.llm.avg_latency_s:.1f}s\n")
    t.append(f"Through. {s.llm.throughput_cpm:.1f} calls/min\n")
    t.append(f"Tokens   {s.llm.input_tokens_k}k in / {s.llm.output_tokens_k}k out\n", style="dim")

    if s.labels:
        t.append("\nLabels\n", style="bold")
        for label, count in s.labels.items():
            t.append(f"  {label:<12}", style=_label_color(label))
            t.append(f" {count}\n", style="bold")

    if s.errors:
        t.append("\nErrors\n", style="bold")
        total = s.errors.quota_2056 + s.errors.api_connection + s.errors.pipeline_errors
        tag = "green" if total == 0 else "yellow"
        t.append(f"  2056      ", style=_level_color("WARNING") if s.errors.quota_2056 else "white")
        t.append(f"{s.errors.quota_2056}\n", style=tag)
        t.append(f"  APIConn   ", style=_level_color("ERROR") if s.errors.api_connection else "white")
        t.append(f"{s.errors.api_connection}\n", style=tag)
        t.append(f"  pipeline  ", style=_level_color("ERROR") if s.errors.pipeline_errors else "white")
        t.append(f"{s.errors.pipeline_errors}\n", style=tag)

    if s.quota:
        t.append("\nQuota   ", style="bold")
        if s.quota.in_block:
            t.append("IN BLOCK", style="red")
            t.append(f" ({s.quota.cum_waited_min}min waited)\n", style="dim")
        else:
            t.append("healthy\n", style="green")

    return Panel(t, title="LLM / Metrics", border_style="blue")


def _events_panel(s: Snapshot) -> Panel:
    t = Text()
    if not s.recent:
        t.append("(no events)", style="dim")
    for e in s.recent[:10]:
        hhmmss = e.ts[11:19] if len(e.ts) >= 19 else e.ts
        t.append(f"{hhmmss}  ", style="dim")
        t.append(e.message[:70] + "\n", style=_level_color(e.level))
    return Panel(t, title="Recent Events", border_style="blue")


def _decisions_panel(s: Snapshot) -> Panel:
    table = Table(show_header=True, header_style="bold", show_edge=False, expand=True, pad_edge=False)
    table.add_column("Date", style="bold", no_wrap=True)
    table.add_column("Source", no_wrap=True)
    table.add_column("Pos", justify="right", style="cyan")
    table.add_column("Cash", justify="right")
    table.add_column("Note", overflow="ellipsis")
    for d in s.decisions:
        src_label = d.source if not d.scan_id else f"{d.source}:{d.scan_id}"
        note = f"trigger={d.trigger_ticker}" if d.trigger_ticker else (d.run_id or "")
        table.add_row(
            d.date_str,
            src_label,
            str(d.n_positions),
            f"{d.cash*100:.0f}%",
            note,
        )
    if not s.decisions:
        table.add_row("(none)", "", "", "", "")
    return Panel(table, title=f"Decisions ({len(s.decisions)})", border_style="blue")


# --- layout assembly -------------------------------------------------------

def _render(s: Snapshot) -> Layout:
    root = Layout()
    root.split_column(
        Layout(_header(s), name="header", size=4),
        Layout(_scans_panel(s), name="scans", size=6),
        Layout(name="mid"),
        Layout(name="bot"),
    )
    root["mid"].split_row(
        Layout(_holdings_panel(s), name="holdings", ratio=2),
        Layout(_llm_panel(s), name="llm", ratio=1),
    )
    root["bot"].split_row(
        Layout(_events_panel(s), name="events", ratio=1),
        Layout(_decisions_panel(s), name="decisions", ratio=1),
    )
    return root


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", type=float, default=5.0,
                        help="Refresh interval in seconds (default: 5)")
    parser.add_argument("--once", action="store_true",
                        help="Render once and exit (no live refresh)")
    args = parser.parse_args()

    console = Console()

    if args.once:
        console.print(_render(get_snapshot()))
        return

    try:
        with Live(_render(get_snapshot()), console=console, refresh_per_second=1,
                  screen=True, transient=False) as live:
            import time
            while True:
                time.sleep(args.refresh)
                live.update(_render(get_snapshot()))
    except KeyboardInterrupt:
        console.print("[dim]dashboard exited.[/]")


if __name__ == "__main__":
    main()
