#!/usr/bin/env python3
"""One-shot backtest status snapshot.

Usage:
    uv run python scripts/monitoring/status.py

Prints a concise summary of the current backtest state. Use this when you
want to glance at progress without starting the live dashboard.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.monitoring.backtest_state import Snapshot, get_snapshot  # noqa: E402


def _fmt_weight(w: float) -> str:
    return f"{w*100:.1f}%"


def _fmt_money(v: float | None) -> str:
    return f"@{v:.2f}" if v is not None else "@?"


def _fmt_mos(v: float | None) -> str:
    if v is None:
        return "  ?"
    return f"{v:>+4.0f}%"


def print_snapshot(s: Snapshot) -> None:
    print(f"=== PoorCharlie Backtest @ {s.now_iso} ===\n")

    # Process
    if s.process is None:
        print("⚠️  No orchestrator process running")
    else:
        print(f"Process : PID {s.process.pid} · {s.process.elapsed} elapsed · "
              f"RSS {s.process.rss_mb}MB · stat={s.process.stat}")

    # Run
    if s.run is None:
        print("\n(no runs found)\n")
        return
    print(f"Run     : {s.run.run_id}  status={s.run.status}  "
          f"as_of={s.run.as_of_date or 'None'}")

    # Phase / progress
    if s.phase:
        ph = f"Phase {s.phase.phase} · {s.phase.phase_name}" if s.phase.phase else s.phase.phase_name
        if s.phase.current_activity:
            ph += f"  ▸ {s.phase.current_activity}"
        print(f"Now     : {ph}")
    if s.progress:
        pct = 100.0 * s.progress.done / s.progress.total if s.progress.total else 0.0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        eta = f"  ETA {s.progress.eta_hours:.1f}h" if s.progress.eta_hours else ""
        print(f"Pipeline: {bar} {s.progress.done}/{s.progress.total} ({pct:.0f}%){eta}")

    # Scan timeline
    if s.scans:
        glyphs = {"done": "✓", "running": "▶", "pending": "○"}
        parts = [f"{p.scan_id}{glyphs.get(p.status, '?')}({p.scan_date})" for p in s.scans]
        print(f"\nScans   : " + "  ".join(parts))

    # Holdings
    if s.holdings:
        total_w = sum(h.weight for h in s.holdings)
        print(f"\nHoldings ({len(s.holdings)}):  equity {_fmt_weight(total_w)} / cash {_fmt_weight(1-total_w)}")
        for h in sorted(s.holdings, key=lambda x: -x.weight):
            print(f"  {h.ticker} {h.name:<10}  {_fmt_weight(h.weight):>6}  "
                  f"{h.final_label or '?':<12}  Q={h.enterprise_quality or '?':<5}  "
                  f"V={h.price_vs_value or '?':<9}  MoS={_fmt_mos(h.margin_of_safety_pct)}  "
                  f"entry {h.entry_date} {_fmt_money(h.entry_price)}")

    # LLM
    if s.llm:
        print(f"\nLLM     : {s.llm.calls} calls · ok {s.llm.ok} · err {s.llm.err} · retry {s.llm.retry}")
        print(f"          avg {s.llm.avg_latency_s:.1f}s · throughput {s.llm.throughput_cpm:.1f}/min · "
              f"tokens {s.llm.input_tokens_k}k in / {s.llm.output_tokens_k}k out")

    # Labels
    if s.labels:
        print(f"\nLabels  : " + " · ".join(f"{k} {v}" for k, v in s.labels.items()))

    # Errors / quota
    if s.errors:
        tag = "🟢" if (s.errors.quota_2056 + s.errors.api_connection + s.errors.pipeline_errors) == 0 else "🟡"
        print(f"\nErrors  : {tag} 2056: {s.errors.quota_2056}  APIConn: {s.errors.api_connection}  "
              f"pipeline_ERROR: {s.errors.pipeline_errors}")
    if s.quota:
        state = "IN BLOCK" if s.quota.in_block else "healthy"
        extra = f" (waited {s.quota.cum_waited_min}min)" if s.quota.in_block else ""
        print(f"Quota   : {state}{extra}")

    # Decisions
    if s.decisions:
        print(f"\nDecisions ({len(s.decisions)}):")
        for d in s.decisions[-6:]:
            tag = f"[{d.source}{':' + d.scan_id if d.scan_id else ''}]"
            extra = f" trigger={d.trigger_ticker}" if d.trigger_ticker else ""
            print(f"  {d.date_str}  {tag:<24}  {d.n_positions} pos · cash {_fmt_weight(d.cash)}{extra}")

    # Recent events
    if s.recent:
        print(f"\nRecent events:")
        for e in s.recent[:6]:
            lv_mark = {"ERROR": "❌", "WARNING": "⚠️ ", "INFO": "  "}.get(e.level, "  ")
            print(f"  {lv_mark} {e.ts}  {e.message[:80]}")

    print()


if __name__ == "__main__":
    print_snapshot(get_snapshot())
