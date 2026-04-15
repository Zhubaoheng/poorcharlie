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
            dur = ""
            if s.phase.seconds_since_activity is not None:
                m, sec = divmod(s.phase.seconds_since_activity, 60)
                dur = f" ({m}m{sec:02d}s ago)" if m else f" ({sec}s ago)"
            ph += f"  ▸ {s.phase.current_activity}{dur}"
        if s.phase.seconds_since_last_log is not None and s.phase.seconds_since_last_log > 120:
            ph += f"  ⚠ log idle {s.phase.seconds_since_last_log//60}m"
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

    # Opportunity queue
    if s.opp_queue:
        q = s.opp_queue
        avg_m = f"{q.avg_duration_s/60:.0f}min avg" if q.avg_duration_s else ""
        eta = ""
        if q.eta_remaining_s:
            eh, em = divmod(int(q.eta_remaining_s // 60), 60)
            eta = f"  ETA {eh}h{em:02d}m" if eh else f"  ETA {em}m"
        print(f"\nOpp Queue ({q.completed}/{q.total_detected})  {avg_m}{eta}")
        for it in q.items:
            glyph = {"done": "✓", "running": "▶", "pending": "○"}.get(it.status, "?")
            if it.duration_s:
                tstr = f" {int(it.duration_s//60)}m{int(it.duration_s%60):02d}s"
            elif it.status == "running":
                tstr = " running"
            else:
                tstr = ""
            res = f"  → {it.result_summary}" if it.result_summary else ""
            print(f"  {glyph} {it.ticker} {it.name[:8]:<8} @{it.trigger_date}{tstr}{res}")

    # Current pipeline
    if s.ticker_pipeline:
        tp = s.ticker_pipeline
        done_count = sum(1 for a in tp.agents if a.completed)
        print(f"\nPipeline ({tp.ticker} {tp.name}):  {done_count}/14 agents done", end="")
        if tp.decision_pipeline_active:
            print(" → decision_pipeline running")
        elif tp.running_agent:
            print(f" → running: {tp.running_agent}")
        else:
            print()

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
        print(f"          avg {s.llm.avg_latency_s:.1f}s · {s.llm.throughput_cpm:.1f}/min cum · "
              f"{s.llm.recent_5min_cpm:.1f}/min last 5m · "
              f"tokens {s.llm.input_tokens_k}k in / {s.llm.output_tokens_k}k out")
        if s.llm.current_call_elapsed_s is not None:
            mm, ss = divmod(s.llm.current_call_elapsed_s, 60)
            t_str = f"{mm}m{ss:02d}s" if mm else f"{ss}s"
            flag = " ⚠" if s.llm.current_call_elapsed_s > 180 else ""
            print(f"          ▸ LLM call in-flight: {t_str}{flag}")

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
