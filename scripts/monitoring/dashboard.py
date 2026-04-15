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
            ago = ""
            if s.phase.seconds_since_activity is not None:
                m, sec = divmod(s.phase.seconds_since_activity, 60)
                ago = f"  [{m}m{sec:02d}s ago]" if m else f"  [{sec}s ago]"
            t.append(f"\n  ▸ {s.phase.current_activity}", style="dim")
            t.append(ago, style="yellow" if (s.phase.seconds_since_activity or 0) > 180 else "dim")
        if s.phase.seconds_since_last_log is not None and s.phase.seconds_since_last_log > 120:
            t.append(f"\n  ⚠ log idle {s.phase.seconds_since_last_log//60}m — process may be stuck",
                     style="red")

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
    # Recent 5min vs cumulative — highlight degradation
    recent = s.llm.recent_5min_cpm
    recent_style = "red" if recent < 2 else ("yellow" if recent < 5 else "green")
    t.append(f"Through. {s.llm.throughput_cpm:.1f} cum · ", style="dim")
    t.append(f"{recent:.1f} recent 5m", style=recent_style)
    t.append(" cpm\n")
    # Current call elapsed — red if > 3min
    if s.llm.current_call_elapsed_s is not None:
        secs = s.llm.current_call_elapsed_s
        mm, ss = divmod(secs, 60)
        call_str = f"{mm}m{ss:02d}s" if mm else f"{ss}s"
        style = "red" if secs > 180 else ("yellow" if secs > 60 else "cyan")
        t.append(f"Current  ", style="bold")
        t.append(f"in-flight {call_str}\n", style=style)
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


def _opp_queue_panel(s: Snapshot) -> Panel:
    q = s.opp_queue
    if q is None:
        t = Text("(no opportunity queue active)", style="dim")
        return Panel(t, title="Opportunity Queue", border_style="magenta")

    table = Table(show_header=True, header_style="bold", show_edge=False, expand=True, pad_edge=False)
    table.add_column("", width=1)
    table.add_column("Ticker", style="bold", no_wrap=True, width=7)
    table.add_column("Name", no_wrap=True, width=8)
    table.add_column("@", style="dim", no_wrap=True, width=10)
    table.add_column("Time", justify="right", no_wrap=True, width=6)
    table.add_column("Result", overflow="ellipsis")

    glyphs = {"done": ("✓", "green"), "running": ("▶", "bold yellow"), "pending": ("○", "dim")}
    for it in q.items:
        glyph, style = glyphs.get(it.status, ("?", "white"))
        if it.status == "done":
            mm = int(it.duration_s // 60) if it.duration_s else 0
            ss = int(it.duration_s % 60) if it.duration_s else 0
            time_str = f"{mm}m{ss:02d}s"
        elif it.status == "running" and it.started_ts:
            elapsed = _seconds_since(it.started_ts)
            time_str = f"{elapsed // 60}m{elapsed % 60:02d}s" if elapsed else "-"
        else:
            time_str = "-"
        table.add_row(
            Text(glyph, style=style),
            it.ticker,
            it.name[:8] if it.name else "",
            it.trigger_date,
            time_str,
            it.result_summary or "",
            style=style if it.status == "running" else None,
        )

    # Footer: avg + ETA
    header_parts = [f"{q.completed}/{q.total_detected} done"]
    if q.avg_duration_s:
        avg_m = q.avg_duration_s / 60
        header_parts.append(f"avg {avg_m:.0f}min")
    if q.eta_remaining_s:
        eta_min = q.eta_remaining_s / 60
        eta_h, eta_m = divmod(int(eta_min), 60)
        eta_fmt = f"{eta_h}h{eta_m:02d}m" if eta_h else f"{eta_m}m"
        header_parts.append(f"ETA {eta_fmt}")
    title = f"Opportunity Queue · {' · '.join(header_parts)}"
    return Panel(table, title=title, border_style="magenta")


def _ticker_pipeline_panel(s: Snapshot) -> Panel:
    tp = s.ticker_pipeline
    if tp is None:
        t = Text("(no single-ticker pipeline running)", style="dim")
        return Panel(t, title="Current Pipeline", border_style="cyan")

    t = Text()
    t.append(f"{tp.ticker}  {tp.name}  @{tp.as_of}\n", style="bold")
    if tp.started_ts:
        elapsed = _seconds_since(tp.started_ts)
        if elapsed:
            mm, ss = divmod(elapsed, 60)
            t.append(f"  running {mm}m{ss:02d}s\n", style="dim")

    for a in tp.agents:
        if a.completed:
            t.append("  ✓ ", style="green")
            t.append(f"{a.name:<20}", style="dim")
            t.append(f" {a.duration_s:.0f}s\n", style="dim")
        elif a.name == tp.running_agent:
            t.append("  ▶ ", style="bold yellow")
            t.append(f"{a.name:<20}", style="bold")
            t.append(f" running...\n", style="yellow")
        else:
            t.append("  ○ ", style="dim")
            t.append(f"{a.name:<20}\n", style="dim")

    if tp.decision_pipeline_active:
        t.append("  ▶ ", style="bold yellow")
        t.append("decision_pipeline      ", style="bold")
        t.append("CrossCompare + PortfolioStrategy\n", style="cyan")

    return Panel(t, title=f"Pipeline · {tp.ticker}", border_style="cyan")


def _seconds_since(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        import datetime as _dt
        dt = _dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return int((_dt.datetime.now() - dt).total_seconds())
    except Exception:
        return None


def _events_panel(s: Snapshot) -> Panel:
    t = Text()
    if not s.recent:
        t.append("(no events)", style="dim")
    for e in s.recent[:10]:
        ago = _seconds_since(e.ts)
        if ago is None:
            age_str = e.ts[11:19] if len(e.ts) >= 19 else e.ts
        elif ago < 60:
            age_str = f"{ago:>2}s"
        elif ago < 3600:
            age_str = f"{ago//60:>2}m"
        else:
            age_str = f"{ago//3600:>2}h"
        t.append(f"{age_str:>4} ago  ", style="dim")
        t.append(e.message[:65] + "\n", style=_level_color(e.level))
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
    in_opp_phase = s.phase and s.phase.phase == 6 and s.opp_queue is not None

    if in_opp_phase:
        # Phase 6: dedicate a large panel to opportunity queue + current pipeline
        root.split_column(
            Layout(_header(s), name="header", size=4),
            Layout(_scans_panel(s), name="scans", size=5),
            Layout(name="opp", ratio=2),
            Layout(name="mid", ratio=2),
            Layout(name="bot", ratio=1),
        )
        root["opp"].split_row(
            Layout(_opp_queue_panel(s), name="opp_queue", ratio=3),
            Layout(_ticker_pipeline_panel(s), name="ticker_pipeline", ratio=2),
        )
    else:
        # Scan phase or idle: default layout
        root.split_column(
            Layout(_header(s), name="header", size=4),
            Layout(_scans_panel(s), name="scans", size=5),
            Layout(name="mid", ratio=2),
            Layout(name="bot", ratio=1),
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
