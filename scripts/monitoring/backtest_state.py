"""Read-only state inspector for a running / completed backtest.

All functions are pure reads of filesystem + `ps`. They never mutate
run data or send LLM calls. Used by both ``status.py`` (one-shot) and
``dashboard.py`` (live refresh) so UI decisions stay out of this layer.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# -- paths ------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data"
RUNS_DIR = DATA_ROOT / "runs"
FULL_BACKTEST_DIR = DATA_ROOT / "full_backtest"
DECISIONS_FILE = FULL_BACKTEST_DIR / "all_decisions.json"
ORCHESTRATOR_LOG = Path("/tmp/full_backtest.log")

# Make script imports available (decision_schema, run_full_backtest.SCAN_DATES)
for p in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts", PROJECT_ROOT / "scripts" / "backtest"):
    ps = str(p)
    if ps not in sys.path:
        sys.path.insert(0, ps)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ProcessInfo:
    pid: int
    parent_pid: int | None
    elapsed: str
    rss_mb: int
    stat: str
    cmdline: str


@dataclass
class RunMeta:
    run_dir: Path
    run_id: str
    status: str              # "running" | "completed" | "failed"
    as_of_date: str | None
    started_at: str | None
    finished_at: str | None
    top_n: int | None
    concurrency: int | None


@dataclass
class PhaseInfo:
    phase: int               # 1..5 (0 = unknown / idle)
    phase_name: str
    current_activity: str    # free-form: e.g. "Pipeline 199/199" or "CrossComparison"


@dataclass
class PipelineProgress:
    done: int
    total: int
    in_progress: int         # tickers currently in flight (best effort)
    eta_hours: float | None  # None if unknown/complete


@dataclass
class HoldingDetail:
    ticker: str
    name: str
    industry: str
    weight: float
    entry_date: str
    entry_price: float | None
    final_label: str
    enterprise_quality: str
    price_vs_value: str
    margin_of_safety_pct: float | None
    scan_close_price: float | None
    entry_reason: str


@dataclass
class LLMStats:
    calls: int
    ok: int
    err: int
    retry: int
    avg_latency_s: float
    input_tokens_k: int
    output_tokens_k: int
    throughput_cpm: float


@dataclass
class Decision:
    date_str: str
    source: str              # scan / opportunity_trigger / legacy
    scan_id: str | None
    trigger_ticker: str | None
    n_positions: int
    cash: float
    run_id: str | None


@dataclass
class ScanPoint:
    scan_id: str
    scan_date: date
    status: str              # "done" | "running" | "pending"


@dataclass
class ErrorCounters:
    quota_2056: int
    api_connection: int
    pipeline_errors: int


@dataclass
class QuotaState:
    in_block: bool
    cum_waited_min: int
    last_poll_ts: str | None


@dataclass
class Event:
    ts: str
    level: str               # INFO / WARNING / ERROR
    message: str


# ---------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------

_PROCESS_PATTERNS = ("run_full_backtest.py", "run_overnight.py")


def get_active_process() -> ProcessInfo | None:
    """Return the orchestrator process (prefer run_full_backtest, else run_overnight)."""
    try:
        raw = subprocess.check_output(
            ["ps", "-eo", "pid,ppid,etime,rss,stat,command"],
            text=True,
        )
    except Exception:
        return None

    candidates: list[ProcessInfo] = []
    for line in raw.splitlines()[1:]:
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        pid, ppid, etime, rss_kb, stat, cmd = parts
        if "grep" in cmd:
            continue
        if any(p in cmd for p in _PROCESS_PATTERNS):
            try:
                candidates.append(ProcessInfo(
                    pid=int(pid),
                    parent_pid=int(ppid) if ppid.isdigit() else None,
                    elapsed=etime.strip(),
                    rss_mb=int(rss_kb) // 1024,
                    stat=stat.strip(),
                    cmdline=cmd.strip(),
                ))
            except ValueError:
                continue
    if not candidates:
        return None
    # Prefer run_full_backtest (the orchestrator) over the child run_overnight
    for c in candidates:
        if "run_full_backtest.py" in c.cmdline:
            return c
    return candidates[0]


# ---------------------------------------------------------------------------
# Run directory / metadata
# ---------------------------------------------------------------------------

def list_runs() -> list[RunMeta]:
    """All overnight runs, sorted by started_at descending."""
    out: list[RunMeta] = []
    if not RUNS_DIR.exists():
        return out
    for d in sorted(RUNS_DIR.iterdir(), key=lambda x: x.name, reverse=True):
        if not d.is_dir():
            continue
        rj = d / "run.json"
        if not rj.exists():
            continue
        try:
            meta = json.loads(rj.read_text(encoding="utf-8"))
        except Exception:
            continue
        cfg = meta.get("config") or {}
        out.append(RunMeta(
            run_dir=d,
            run_id=meta.get("run_id", d.name),
            status=meta.get("status", "?"),
            as_of_date=meta.get("as_of_date"),
            started_at=meta.get("started_at"),
            finished_at=meta.get("finished_at"),
            top_n=cfg.get("top_n"),
            concurrency=cfg.get("pipeline_concurrency"),
        ))
    return out


def get_latest_run() -> RunMeta | None:
    """Latest run by started_at (whether completed or running)."""
    runs = list_runs()
    if not runs:
        return None
    # Prefer a currently-running one over completed
    running = [r for r in runs if r.status == "running"]
    if running:
        return max(running, key=lambda r: r.started_at or "")
    return max(runs, key=lambda r: r.started_at or "")


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

_PHASE_PATTERNS: list[tuple[int, str, re.Pattern[str]]] = [
    (5, "Portfolio construction", re.compile(r"Phase 5: Portfolio construction")),
    (4, "Full pipeline", re.compile(r"Phase 4: Full pipeline|Pipeline \d+/\d+")),
    (3, "LLM screening", re.compile(r"Phase 3: (LLM )?Screening")),
    (2, "Ratio filter", re.compile(r"Phase 2: (Ratio|ratio)")),
    (1, "Universe", re.compile(r"Phase 1: (Universe|Building)")),
]

_PIPELINE_LINE_RE = re.compile(
    r"Pipeline (?P<done>\d+)/(?P<total>\d+): (?P<ticker>\S+) .*? -> (?P<label>\S+).* ETA (?P<eta>[\d.]+)h"
)

_LLM_STATS_RE = re.compile(
    r"LLM stats: (?P<calls>\d+) calls \((?P<ok>\d+) ok / (?P<err>\d+) err / (?P<retry>\d+) retry\)"
    r".*?avg (?P<avg>[\d.]+)s.*?in=(?P<in_k>\d+)k out=(?P<out_k>\d+)k.*?throughput=(?P<thru>[\d.]+)"
)

_QUOTA_POLL_RE = re.compile(
    r"usage limit \(2056\), polling again in (?P<poll>\d+)s \(total waited (?P<total>\d+)min\)"
)
_QUOTA_OLD_RE = re.compile(
    r"usage limit exceeded \(2056\), sleeping (?P<sleep>\S+)"
)


def _read_tail(path: Path, bytes_limit: int = 200_000) -> str:
    """Read last ~200KB of a file as text. Safe on partial reads."""
    if not path.exists():
        return ""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > bytes_limit:
                f.seek(size - bytes_limit)
                f.readline()  # discard partial first line
            return f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def get_current_phase(run_dir: Path) -> PhaseInfo:
    """Best-effort current phase + activity.

    The orchestrator runs scans (Phase 1-5 inside run_overnight) and then,
    between scans, runs opportunity trigger re-evaluations whose activity
    only shows up in the orchestrator log at /tmp/full_backtest.log. We
    merge both logs' tails to get the most recent state.
    """
    run_lines = _read_tail(run_dir / "overnight.log").splitlines()
    orch_lines = _read_tail(ORCHESTRATOR_LOG).splitlines()
    phase_num, phase_name = 0, "unknown"
    activity = ""

    # Determine if we're between scans (opportunity trigger processing)
    between_scans = False
    for line in reversed(orch_lines[-200:]):
        if "Opportunity re-eval" in line or "Opportunity trigger" in line or \
           "Processing" in line and "opportunity triggers" in line:
            between_scans = True
            break
        if "SCAN S" in line and "===" in line:
            # Most recent phase marker is a scan banner — we're inside a scan
            break
        if "EVALUATION COMPLETE" in line:
            # A scan just finished; triggers likely running next
            between_scans = True
            break

    # Reverse scan for the most recent phase marker (run log only)
    lines = run_lines
    for line in reversed(lines):
        for num, name, pat in _PHASE_PATTERNS:
            if pat.search(line):
                phase_num, phase_name = num, name
                break
        if phase_num:
            break

    if between_scans:
        phase_num = 6
        phase_name = "Between-scan triggers"

    # Most recent activity (tail-most relevant line)
    activity_patterns = [
        (re.compile(r"Opportunity trigger: running pipeline for (\S+) (\S+) as_of=(\S+)"),
         lambda m: f"Opportunity re-eval: {m.group(1)} {m.group(2)} @{m.group(3)}"),
        (re.compile(r"Processing (\d+) opportunity triggers"),
         lambda m: f"Processing {m.group(1)} opportunity triggers"),
        (re.compile(r"Running PortfolioStrategyAgent"),
         lambda m: "PortfolioStrategyAgent"),
        (re.compile(r"Running CrossComparisonAgent on (\d+)"),
         lambda m: f"CrossComparisonAgent ({m.group(1)} candidates)"),
        (_PIPELINE_LINE_RE,
         lambda m: f"Pipeline {m.group('done')}/{m.group('total')} {m.group('ticker')} → {m.group('label')}"),
    ]
    # Combine both logs and sort by timestamp so the most recent wins across
    # both sources (opportunity triggers live in orchestrator log; scan
    # pipeline events in run log).
    combined = [l for l in (orch_lines[-60:] + run_lines[-60:]) if len(l) >= 19]
    combined.sort(key=lambda l: l[:19])
    for line in reversed(combined):
        for pat, render in activity_patterns:
            m = pat.search(line)
            if m:
                activity = render(m)
                break
        if activity:
            break
    if not activity:
        # Fall back to last 'info_capture' / 'committee' style agent step
        for line in reversed(lines[-20:]):
            if "[" in line and "took" in line:
                activity = line.split(" INFO ")[-1].strip() if " INFO " in line else line.strip()[:80]
                break

    return PhaseInfo(phase=phase_num, phase_name=phase_name, current_activity=activity)


def get_pipeline_progress(run_dir: Path) -> PipelineProgress:
    ckpt_dir = run_dir / "checkpoints" / "pipeline"
    done_by_ckpt = len(list(ckpt_dir.glob("*.json"))) if ckpt_dir.exists() else 0

    total = done_by_ckpt  # sensible lower bound
    eta: float | None = None
    log_tail = _read_tail(run_dir / "overnight.log")
    for line in reversed(log_tail.splitlines()):
        m = _PIPELINE_LINE_RE.search(line)
        if m:
            total = int(m.group("total"))
            try:
                eta = float(m.group("eta"))
            except ValueError:
                pass
            break
    in_progress = max(0, total - done_by_ckpt) if total >= done_by_ckpt else 0
    # Acquired semaphore events minus completions -> in-flight
    in_flight_acquired = log_tail.count("acquired semaphore, starting pipeline")
    # If we have a valid recent 'Pipeline N/total' line, in_progress is better estimated
    # as min(10, total - done) for concurrency.
    return PipelineProgress(
        done=done_by_ckpt,
        total=max(total, done_by_ckpt),
        in_progress=min(10, in_progress) if in_progress > 0 else 0,
        eta_hours=eta if (eta is not None and done_by_ckpt < (total or 0)) else None,
    )


def get_llm_stats(run_dir: Path) -> LLMStats | None:
    log_tail = _read_tail(run_dir / "overnight.log", bytes_limit=400_000)
    # iterate in reverse to find most recent stats
    latest: re.Match[str] | None = None
    for line in reversed(log_tail.splitlines()):
        m = _LLM_STATS_RE.search(line)
        if m:
            latest = m
            break
    if not latest:
        return None
    return LLMStats(
        calls=int(latest["calls"]),
        ok=int(latest["ok"]),
        err=int(latest["err"]),
        retry=int(latest["retry"]),
        avg_latency_s=float(latest["avg"]),
        input_tokens_k=int(latest["in_k"]),
        output_tokens_k=int(latest["out_k"]),
        throughput_cpm=float(latest["thru"]),
    )


def get_label_distribution(run_dir: Path) -> dict[str, int]:
    ckpt_dir = run_dir / "checkpoints" / "pipeline"
    if not ckpt_dir.exists():
        return {}
    counts: Counter[str] = Counter()
    for p in ckpt_dir.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            counts[d.get("final_label", "?")] += 1
        except Exception:
            counts["?"] += 1
    return dict(counts.most_common())


# ---------------------------------------------------------------------------
# Holdings (enriched with latest candidate snapshot)
# ---------------------------------------------------------------------------

def get_holdings_enriched(run_dir: Path) -> list[HoldingDetail]:
    store_path = run_dir / "candidate_store.json"
    if not store_path.exists():
        return []
    try:
        store = json.loads(store_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    cands = store.get("candidates", {})
    holdings = store.get("holdings", [])
    out: list[HoldingDetail] = []
    for h in holdings:
        ticker = h.get("ticker", "")
        c = cands.get(ticker, {})
        out.append(HoldingDetail(
            ticker=ticker,
            name=h.get("name", ""),
            industry=h.get("industry", "") or c.get("industry", ""),
            weight=float(h.get("target_weight", 0.0)),
            entry_date=h.get("entry_date", ""),
            entry_price=h.get("entry_price"),
            final_label=c.get("final_label", ""),
            enterprise_quality=c.get("enterprise_quality", ""),
            price_vs_value=c.get("price_vs_value", ""),
            margin_of_safety_pct=c.get("margin_of_safety_pct"),
            scan_close_price=c.get("scan_close_price"),
            entry_reason=h.get("entry_reason", ""),
        ))
    return out


# ---------------------------------------------------------------------------
# Errors / quota
# ---------------------------------------------------------------------------

def get_error_counters(run_dir: Path, since_iso: str | None = None) -> ErrorCounters:
    """Count WARNING/ERROR events, optionally only after a given ISO timestamp.

    ``since_iso`` accepts either "YYYY-MM-DD HH:MM:SS" (log format) or
    "YYYY-MM-DDTHH:MM:SS..." (run.json format). It is normalized to log's
    space-separated form for prefix compare.
    """
    if since_iso:
        since_iso = since_iso.replace("T", " ")[:19]
    log_tail = _read_tail(run_dir / "overnight.log", bytes_limit=400_000)
    q = 0
    api = 0
    err = 0
    for line in log_tail.splitlines():
        if since_iso and line[:19] < since_iso:
            continue
        if "usage limit" in line or "2056" in line:
            q += 1
        elif "APIConnectionError" in line:
            api += 1
        elif " ERROR " in line and "Pipeline FAILED" in line:
            err += 1
    return ErrorCounters(quota_2056=q, api_connection=api, pipeline_errors=err)


def get_quota_state(run_dir: Path) -> QuotaState:
    log_tail = _read_tail(run_dir / "overnight.log", bytes_limit=200_000)
    lines = log_tail.splitlines()
    last_poll_ts: str | None = None
    last_total = 0
    last_success_ts: str | None = None
    last_quota_ts: str | None = None
    for line in lines:
        m = _QUOTA_POLL_RE.search(line)
        if m:
            last_poll_ts = line[:19]
            last_quota_ts = last_poll_ts
            last_total = int(m["total"])
            continue
        if "LLM call #" in line:
            last_success_ts = line[:19]
    # Consider "in block" if the most recent notable event was a 2056 poll
    in_block = bool(
        last_quota_ts
        and (last_success_ts is None or last_quota_ts > last_success_ts)
    )
    return QuotaState(
        in_block=in_block,
        cum_waited_min=last_total,
        last_poll_ts=last_poll_ts,
    )


# ---------------------------------------------------------------------------
# Recent events (for dashboard feed)
# ---------------------------------------------------------------------------

_INTERESTING_PATTERNS = (
    re.compile(r"Pipeline \d+/\d+"),
    re.compile(r"Phase \d+"),
    re.compile(r"EVALUATION COMPLETE"),
    re.compile(r"usage limit"),
    re.compile(r"APIConnectionError"),
    re.compile(r"Pipeline FAILED"),
    re.compile(r"LLM rate limit"),
    re.compile(r"LLM call timeout"),
    re.compile(r"Decision pipeline complete"),
    re.compile(r"Opportunity trigger"),
    re.compile(r"Valuation trigger"),
    re.compile(r"Price obs"),
    re.compile(r"Running CrossComparisonAgent"),
    re.compile(r"Running PortfolioStrategyAgent"),
    re.compile(r"Reusing existing completed run"),
    re.compile(r"Saved \d+ decision points"),
)


def get_recent_events(n: int = 10) -> list[Event]:
    """Pull recent interesting events from orchestrator + latest run log."""
    events: list[Event] = []

    orch = _read_tail(ORCHESTRATOR_LOG).splitlines()
    latest = get_latest_run()
    run_lines: list[str] = []
    if latest:
        run_lines = _read_tail(latest.run_dir / "overnight.log").splitlines()

    def _event_from(line: str) -> Event | None:
        for pat in _INTERESTING_PATTERNS:
            if pat.search(line):
                # Standard log format: "YYYY-MM-DD HH:MM:SS,ms LEVEL msg"
                level = "INFO"
                if " WARNING " in line:
                    level = "WARNING"
                elif " ERROR " in line:
                    level = "ERROR"
                return Event(
                    ts=line[:19] if len(line) >= 19 else "",
                    level=level,
                    message=line[24:].strip() if len(line) > 24 else line,
                )
        return None

    # Scan both sources from the tail, dedupe by timestamp+message
    seen: set[tuple[str, str]] = set()
    for line in reversed(run_lines + orch):
        ev = _event_from(line)
        if ev is None:
            continue
        key = (ev.ts, ev.message[:80])
        if key in seen:
            continue
        seen.add(key)
        events.append(ev)
        if len(events) >= n:
            break
    return events


# ---------------------------------------------------------------------------
# Scan schedule + decisions
# ---------------------------------------------------------------------------

def _import_scan_dates() -> list[date]:
    try:
        # run_full_backtest defines SCAN_DATES at module load
        import importlib

        mod = importlib.import_module("run_full_backtest")
        return list(mod.SCAN_DATES)
    except Exception:
        return []


def get_scan_schedule() -> list[ScanPoint]:
    """Return the 5 SCAN_DATES tagged as done/running/pending."""
    scan_dates = _import_scan_dates()
    if not scan_dates:
        return []
    done: set[str] = set()
    running: str | None = None

    # 1) completed runs → done
    for rm in list_runs():
        if rm.as_of_date and rm.status == "completed":
            done.add(rm.as_of_date)
    # 2) currently-running run → running
    for rm in list_runs():
        if rm.as_of_date and rm.status == "running":
            running = rm.as_of_date
            break

    result: list[ScanPoint] = []
    for i, d in enumerate(scan_dates):
        ds = d.isoformat()
        if ds in done:
            status = "done"
        elif ds == running:
            status = "running"
        else:
            status = "pending"
        result.append(ScanPoint(scan_id=f"S{i}", scan_date=d, status=status))
    return result


def get_decision_timeline() -> list[Decision]:
    if not DECISIONS_FILE.exists():
        return []
    try:
        # Reuse decision_schema loader (handles v1.0 legacy)
        from decision_schema import load_decisions  # type: ignore
        data = load_decisions(DECISIONS_FILE)
    except Exception:
        # Bare read as fallback
        try:
            raw = json.loads(DECISIONS_FILE.read_text(encoding="utf-8"))
            data = raw.get("decisions", raw) if isinstance(raw, dict) else {}
        except Exception:
            return []
    out: list[Decision] = []
    for date_str in sorted(data.keys()):
        rec = data[date_str]
        weights = rec.get("weights", {})
        out.append(Decision(
            date_str=date_str,
            source=rec.get("source", "?"),
            scan_id=rec.get("scan_id"),
            trigger_ticker=rec.get("trigger_ticker"),
            n_positions=len(weights),
            cash=float(rec.get("cash", 1.0 - sum(weights.values()))),
            run_id=rec.get("run_id"),
        ))
    return out


# ---------------------------------------------------------------------------
# Aggregate "snapshot" — convenient for status.py
# ---------------------------------------------------------------------------

@dataclass
class Snapshot:
    now_iso: str
    process: ProcessInfo | None
    run: RunMeta | None
    phase: PhaseInfo | None
    progress: PipelineProgress | None
    llm: LLMStats | None
    labels: dict[str, int]
    holdings: list[HoldingDetail]
    errors: ErrorCounters | None
    quota: QuotaState | None
    recent: list[Event]
    scans: list[ScanPoint]
    decisions: list[Decision]


def get_snapshot() -> Snapshot:
    process = get_active_process()
    run = get_latest_run()
    phase = progress = llm = errors = quota = None
    labels: dict[str, int] = {}
    holdings: list[HoldingDetail] = []
    if run is not None:
        phase = get_current_phase(run.run_dir)
        progress = get_pipeline_progress(run.run_dir)
        llm = get_llm_stats(run.run_dir)
        labels = get_label_distribution(run.run_dir)
        holdings = get_holdings_enriched(run.run_dir)
        errors = get_error_counters(run.run_dir, since_iso=run.started_at[:19] if run.started_at else None)
        quota = get_quota_state(run.run_dir)
    return Snapshot(
        now_iso=datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        process=process,
        run=run,
        phase=phase,
        progress=progress,
        llm=llm,
        labels=labels,
        holdings=holdings,
        errors=errors,
        quota=quota,
        recent=get_recent_events(n=10),
        scans=get_scan_schedule(),
        decisions=get_decision_timeline(),
    )
