#!/usr/bin/env python3
"""Overnight large-scale A-share pipeline evaluation.

Screens top N A-share stocks by market cap through the full investagent
pipeline. Designed to run overnight with checkpoint/resume support.

Usage:
    uv run python scripts/run_overnight.py [--top 2000] [--pipeline-concurrency 5]

Resume after crash:
    # Just re-run — checkpoints auto-skip completed stocks
    uv run python scripts/run_overnight.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# Bypass proxy for Chinese domestic sites (cninfo, AkShare backends).
# Claude Code sets HTTP_PROXY automatically; these sites are faster direct.
import os
_NO_PROXY_DOMAINS = (
    "cninfo.com.cn,static.cninfo.com.cn,"
    "eastmoney.com,push2.eastmoney.com,push2his.eastmoney.com,"
    "10jqka.com.cn,sina.com.cn,finance.sina.com.cn,"
    "csindex.com.cn,legulegu.com,"
    "hkexnews.hk,www1.hkexnews.hk,"
    "baostock.com"
)
os.environ.setdefault("NO_PROXY", _NO_PROXY_DOMAINS)
os.environ.setdefault("no_proxy", _NO_PROXY_DOMAINS)

import pandas as pd

from investagent.config import create_llm_client
from investagent.datasources.akshare_source import fetch_a_share_financials
from investagent.llm import LLMClient
from investagent.schemas.company import CompanyIntake
from investagent.schemas.filing import BalanceSheetRow, CashFlowRow, IncomeStatementRow
from investagent.screening.ratio_calc import compute_ratios, should_skip_by_ratios
from investagent.screening.screener import ScreenerAgent, ScreenerInput
from investagent.workflow.orchestrator import run_pipeline
from investagent.agents.portfolio import (
    CandidateInfo,
    PortfolioAgent,
    PortfolioInput,
)
from investagent.store.candidate_store import CandidateStore
from investagent.store.run_manager import RunManager
from investagent.datasources.cache import FilingCache, AkShareCache
from investagent.datasources.cached_fetcher import CachedFilingFetcher
from investagent.workflow.decision_pipeline import run_decision_pipeline

_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
_BASE_OUTPUT_DIR = _DATA_ROOT / "overnight"
# Will be set in main() by RunManager
OUTPUT_DIR = _BASE_OUTPUT_DIR
CHECKPOINT_DIR = _BASE_OUTPUT_DIR / "checkpoints"

_EXCHANGE_MAP = {
    "6": "SSE", "9": "SSE",
    "0": "SZSE", "3": "SZSE", "2": "SZSE",
    "4": "BSE", "8": "BSE",
}

logger = logging.getLogger("overnight")


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def _load_checkpoint(phase: str, key: str) -> dict | None:
    f = CHECKPOINT_DIR / phase / f"{key}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _save_checkpoint(phase: str, key: str, data: dict) -> None:
    d = CHECKPOINT_DIR / phase
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _count_checkpoints(phase: str) -> int:
    d = CHECKPOINT_DIR / phase
    if not d.exists():
        return 0
    return len(list(d.glob("*.json")))


# ---------------------------------------------------------------------------
# Phase 1: Universe Construction
# ---------------------------------------------------------------------------

def _build_industry_map_em(ak: Any) -> dict[str, str]:
    """Primary: eastmoney board constituents."""
    industry_map: dict[str, str] = {}
    boards = ak.stock_board_industry_name_em()
    board_names = [str(row["板块名称"]) for _, row in boards.iterrows()]
    logger.info("Fetching EM industry constituents for %d boards...", len(board_names))
    for i, board_name in enumerate(board_names):
        try:
            cons = ak.stock_board_industry_cons_em(symbol=board_name)
            for _, row in cons.iterrows():
                ticker = str(row["代码"])
                if ticker not in industry_map:
                    industry_map[ticker] = board_name
        except Exception:
            pass
        if (i + 1) % 20 == 0:
            logger.info("  EM boards: %d/%d (%d tickers)", i + 1, len(board_names), len(industry_map))
    return industry_map


def _build_industry_map_sw(ak: Any) -> dict[str, str]:
    """Fallback: Shenwan L1 industry classification."""
    industry_map: dict[str, str] = {}
    ind = ak.sw_index_first_info()
    for _, row in ind.iterrows():
        code = str(row["行业代码"])
        name = str(row["行业名称"])
        try:
            cons = ak.sw_index_third_cons(symbol=code)
            for _, c in cons.iterrows():
                # Shenwan returns "601009.SH" — strip suffix to get "601009"
                raw = str(c["股票代码"]).split(".")[0].zfill(6)
                if raw not in industry_map:
                    industry_map[raw] = name
        except Exception:
            pass
    return industry_map


def _build_industry_map() -> dict[str, str]:
    """Build ticker -> industry mapping. Shenwan first (stable), EM as fallback."""
    import akshare as ak
    try:
        m = _build_industry_map_sw(ak)
        if len(m) > 1000:
            logger.info("Industry map (Shenwan): %d tickers", len(m))
            return m
        logger.warning("Shenwan industry map too small (%d), trying EM...", len(m))
    except Exception:
        logger.warning("Shenwan industry map failed, trying EM...")
    try:
        m = _build_industry_map_em(ak)
        logger.info("Industry map (EM): %d tickers", len(m))
        return m
    except Exception:
        logger.warning("All industry map sources failed")
        return {}


def _fetch_baostock_universe(top_n: int) -> pd.DataFrame | None:
    """Primary: baostock CSI300+500 constituents (no rate limit, own server)."""
    try:
        import baostock as bs
        from investagent.datasources.historical_market_data import _ensure_baostock_login
        _ensure_baostock_login()

        frames = []
        for query_fn, label in [(bs.query_hs300_stocks, "CSI300"), (bs.query_zz500_stocks, "CSI500")]:
            rs = query_fn()
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if rows:
                df = pd.DataFrame(rows, columns=rs.fields)
                df["_index"] = label
                frames.append(df)

        if not frames:
            return None
        combined = pd.concat(frames).drop_duplicates(subset=["code"])
        # Convert baostock code (sh.600519) to plain code (600519)
        combined["ticker"] = combined["code"].str.split(".").str[1]
        combined = combined.rename(columns={"code_name": "name"})
        logger.info("[universe] baostock CSI300+500: %d stocks", len(combined))
        return combined[["ticker", "name", "_index"]]
    except Exception as e:
        logger.warning("[universe] baostock failed: %s", e)
        return None


def _fetch_index_universe(ak: Any, top_n: int) -> pd.DataFrame | None:
    """Fallback: CSI 300 + CSI 500 via csindex.com.cn."""
    try:
        frames = []
        for symbol, label in [("000300", "CSI300"), ("000905", "CSI500")]:
            df = ak.index_stock_cons_csindex(symbol=symbol)
            df = df.rename(columns={"成分券代码": "ticker", "成分券名称": "name"})
            df["_index"] = label
            frames.append(df[["ticker", "name", "_index"]])
        combined = pd.concat(frames).drop_duplicates(subset=["ticker"])
        logger.info("[universe] CSI index OK: %d stocks (csindex.com.cn)", len(combined))
        return combined
    except Exception as e:
        logger.warning("[universe] CSI index failed: %s", e)
        return None


def build_top_universe(top_n: int) -> list[dict[str, Any]]:
    """Fetch top A-shares by market cap with exclusions applied.

    Fallback chain:
      1. baostock CSI300+500 — own server, no rate limit
      2. index_stock_cons_csindex — csindex.com.cn
    """
    import akshare as ak

    logger.info("Fetching A-share universe (baostock primary, csindex fallback)...")
    df = _fetch_baostock_universe(top_n)
    source = "baostock"
    if df is None:
        df = _fetch_index_universe(ak, top_n)
        source = "csi_index"
    if df is None:
        raise RuntimeError("All universe sources failed")

    df["ticker"] = df["ticker"].astype(str)

    # 2. Industry map (bulk)
    industry_map = _build_industry_map()

    # 3. Financial sector exclusion set (substring match)
    financial_keywords = ("银行", "保险", "证券", "金融", "信托", "期货", "租赁")
    financial_tickers: set[str] = set()
    for ticker, ind in industry_map.items():
        if any(kw in ind for kw in financial_keywords):
            financial_tickers.add(ticker)
    logger.info("Financial sector tickers: %d", len(financial_tickers))

    # 4. Build stock list with exclusions
    stocks: list[dict[str, Any]] = []
    excluded_st = 0
    excluded_fin = 0
    for _, row in df.iterrows():
        ticker = str(row["ticker"])
        name = str(row["name"])
        market_cap = float(row["market_cap"]) if "market_cap" in row and pd.notna(row.get("market_cap")) else 0

        if re.match(r"^[\*]?ST", name):
            excluded_st += 1
            continue
        if ticker in financial_tickers:
            excluded_fin += 1
            continue

        stocks.append({
            "ticker": ticker,
            "name": name,
            "market_cap": market_cap,
            "industry": industry_map.get(ticker, ""),
        })

        if len(stocks) >= top_n:
            break

    logger.info(
        "Universe: %d stocks via %s (excluded: %d ST, %d financial)",
        len(stocks), source, excluded_st, excluded_fin,
    )
    return stocks


# ---------------------------------------------------------------------------
# Phase 2: Ratio Computation
# ---------------------------------------------------------------------------

async def compute_all_ratios(
    stocks: list[dict], concurrency: int = 5,
    as_of_date: date | None = None,
    rotator: Any = None,
) -> list[dict]:
    """Compute financial ratios for all stocks via AkShare.

    When as_of_date is set, filters financial data to fiscal years
    available at that date (year <= as_of_date.year - 1).
    """
    sem = asyncio.Semaphore(concurrency)
    total = len(stocks)
    done = [0]
    start = time.time()
    max_fy = str(as_of_date.year - 1) if as_of_date else None
    cached_count = _count_checkpoints("ratios")
    if cached_count:
        logger.info("Ratios: %d already cached", cached_count)
    if max_fy:
        logger.info("Ratios: backtest mode, fiscal_year <= %s", max_fy)

    async def _compute(stock: dict) -> dict:
        ticker = stock["ticker"]

        cached = _load_checkpoint("ratios", ticker)
        if cached is not None:
            done[0] += 1
            return {**stock, "ratios": cached.get("ratios", {})}

        async with sem:
            try:
                financials = await asyncio.to_thread(fetch_a_share_financials, ticker)
                is_data = financials.get("income_statement", [])
                bs_data = financials.get("balance_sheet", [])
                cf_data = financials.get("cash_flow", [])

                # Backtest: filter to fiscal years available at as_of_date
                if max_fy:
                    is_data = [r for r in is_data if r.get("fiscal_year", "") <= max_fy]
                    bs_data = [r for r in bs_data if r.get("fiscal_year", "") <= max_fy]
                    cf_data = [r for r in cf_data if r.get("fiscal_year", "") <= max_fy]

                income = [IncomeStatementRow(**r) for r in is_data]
                balance = [BalanceSheetRow(**r) for r in bs_data]
                cash_flow = [CashFlowRow(**r) for r in cf_data]
                ratios = compute_ratios(income, balance, cash_flow)
                stock["ratios"] = ratios
                _save_checkpoint("ratios", ticker, {"ratios": ratios})
            except Exception:
                stock["ratios"] = {}

            done[0] += 1
            # Rotate proxy every 20 stocks to distribute rate limits
            if rotator and done[0] % 20 == 0:
                rotator.rotate()
            if done[0] % 100 == 0 or done[0] == total:
                elapsed = time.time() - start
                rate = done[0] / elapsed if elapsed > 0 else 1
                eta = (total - done[0]) / rate
                logger.info("Ratios: %d/%d (%.1f/s, ETA %.0fm)", done[0], total, rate, eta / 60)
            return stock

    results = await asyncio.gather(*[_compute(s) for s in stocks])
    return list(results)


# ---------------------------------------------------------------------------
# Phase 3: LLM Screening
# ---------------------------------------------------------------------------

async def screen_all(
    stocks: list[dict], llm: LLMClient, concurrency: int = 20,
) -> tuple[list[dict], list[dict]]:
    """Screen all stocks with ScreenerAgent. Returns (proceed, skipped)."""
    screener = ScreenerAgent(llm=llm)
    sem = asyncio.Semaphore(concurrency)
    total = len(stocks)
    done = [0]
    start = time.time()
    cached_count = _count_checkpoints("screening")
    if cached_count:
        logger.info("Screening: %d already cached", cached_count)

    async def _screen(stock: dict) -> dict:
        ticker = stock["ticker"]

        cached = _load_checkpoint("screening", ticker)
        if cached is not None:
            done[0] += 1
            return cached

        async with sem:
            market_cap_str = ""
            mc = stock.get("market_cap")
            if mc and mc > 0:
                market_cap_str = f"{mc / 1e8:.0f}亿"

            inp = ScreenerInput(
                ticker=ticker,
                name=stock.get("name", ""),
                industry=stock.get("industry", ""),
                market_cap=market_cap_str,
                ratios=stock.get("ratios", {}),
            )
            try:
                result = await screener.run(inp)
                r = {
                    "ticker": ticker,
                    "name": stock.get("name", ""),
                    "market_cap": stock.get("market_cap"),
                    "industry": stock.get("industry", ""),
                    "decision": result.decision,
                    "reason": result.reason,
                    "industry_context": getattr(result, "industry_context", ""),
                }
            except Exception as e:
                logger.warning("Screening failed for %s %s: %s", ticker, stock.get("name", ""), e)
                r = {
                    "ticker": ticker, "name": stock.get("name", ""),
                    "market_cap": stock.get("market_cap"),
                    "industry": stock.get("industry", ""),
                    "decision": "SKIP", "reason": "screener_error",
                }

            _save_checkpoint("screening", ticker, r)
            done[0] += 1
            if done[0] % 50 == 0 or done[0] == total:
                elapsed = time.time() - start
                rate = done[0] / elapsed if elapsed > 0 else 1
                eta = (total - done[0]) / rate
                logger.info("Screening: %d/%d (%.1f/s, ETA %.0fm)", done[0], total, rate, eta / 60)
            return r

    results = await asyncio.gather(*[_screen(s) for s in stocks])

    proceed = [r for r in results if r.get("decision") in ("PROCEED", "SPECIAL_CASE")]
    skipped = [r for r in results if r.get("decision") not in ("PROCEED", "SPECIAL_CASE")]

    logger.info("Screening complete: %d PROCEED + %d SKIP = %d total",
                len(proceed), len(skipped), len(results))
    return proceed, skipped


# ---------------------------------------------------------------------------
# Phase 4: Full Pipeline
# ---------------------------------------------------------------------------

def _extract_result(ctx: Any, stock: dict) -> dict:
    """Extract pipeline results for serialization."""
    result: dict[str, Any] = {
        "ticker": stock["ticker"],
        "name": stock.get("name", ""),
        "market_cap": stock.get("market_cap"),
        "industry": stock.get("industry", ""),
        "stopped": ctx.is_stopped(),
        "stop_reason": ctx.stop_reason,
        "agents_completed": len(ctx.completed_agents()),
        "completed_agents": ctx.completed_agents(),
    }

    try:
        committee = ctx.get_result("committee")
        result["final_label"] = (
            committee.final_label.value
            if hasattr(committee.final_label, "value")
            else str(committee.final_label)
        )
        result["thesis"] = getattr(committee, "thesis", "")
        result["anti_thesis"] = getattr(committee, "anti_thesis", "")
        result["largest_unknowns"] = getattr(committee, "largest_unknowns", [])
        result["why_now_or_why_not_now"] = getattr(committee, "why_now_or_why_not_now", "")
        result["next_action"] = getattr(committee, "next_action", "")
    except (KeyError, AttributeError):
        result["final_label"] = "STOPPED" if ctx.is_stopped() else "ERROR"

    try:
        val = ctx.get_result("valuation")
        result["price_vs_value"] = getattr(val, "price_vs_value", "")
        result["margin_of_safety_pct"] = getattr(val, "margin_of_safety_pct", None)
        result["meets_hurdle_rate"] = getattr(val, "meets_hurdle_rate", False)
    except (KeyError, AttributeError):
        pass

    try:
        fq = ctx.get_result("financial_quality")
        result["enterprise_quality"] = getattr(fq, "enterprise_quality", "")
        result["pass_minimum"] = getattr(fq, "pass_minimum_standard", False)
    except (KeyError, AttributeError):
        pass

    return result


async def pipeline_all(
    stocks: list[dict], llm: LLMClient, concurrency: int = 5,
    as_of_date: date | None = None,
    filing_cache: "FilingCache | None" = None,
) -> list[dict]:
    """Run full pipeline on all stocks with progress tracking."""
    sem = asyncio.Semaphore(concurrency)
    total = len(stocks)
    done = [0]
    start = time.time()
    label_counts: dict[str, int] = {}
    cached_count = _count_checkpoints("pipeline")
    if cached_count:
        logger.info("Pipeline: %d already cached", cached_count)

    async def _run(stock: dict) -> dict:
        ticker = stock["ticker"]

        cached = _load_checkpoint("pipeline", ticker)
        if cached is not None:
            done[0] += 1
            label = cached.get("final_label", "?")
            label_counts[label] = label_counts.get(label, 0) + 1
            return cached

        async with sem:
            t0 = time.time()
            exchange = _EXCHANGE_MAP.get(ticker[0], "SSE")
            intake = CompanyIntake(
                ticker=ticker,
                name=stock.get("name", ticker),
                exchange=exchange,
                sector=stock.get("industry"),
                as_of_date=as_of_date,
            )
            try:
                ctx = await run_pipeline(intake, llm=llm, filing_cache=filing_cache)
                result = _extract_result(ctx, stock)
            except BaseException as e:
                logger.error("Pipeline FAILED for %s %s: %s", ticker, stock.get("name", ""), e)
                result = {
                    "ticker": ticker, "name": stock.get("name", ""),
                    "market_cap": stock.get("market_cap"),
                    "industry": stock.get("industry", ""),
                    "final_label": "ERROR", "error": str(e),
                    "stopped": True, "agents_completed": 0,
                }

            elapsed_one = time.time() - t0
            result["pipeline_seconds"] = round(elapsed_one, 1)
            # Don't checkpoint errors (rate limits etc) so they get retried on resume
            if result.get("final_label") != "ERROR":
                _save_checkpoint("pipeline", ticker, result)

            done[0] += 1
            label = result.get("final_label", "?")
            label_counts[label] = label_counts.get(label, 0) + 1

            total_elapsed = time.time() - start
            rate = done[0] / total_elapsed if total_elapsed > 0 else 0.01
            eta_h = (total - done[0]) / rate / 3600

            dist = " ".join(f"{k}:{v}" for k, v in sorted(label_counts.items()))
            logger.info(
                "Pipeline %d/%d: %s %s -> %s (%.0fs) | ETA %.1fh | %s",
                done[0], total, ticker, stock.get("name", ""), label,
                elapsed_one, eta_h, dist,
            )
            # Print LLM stats every 5 completions
            if done[0] % 5 == 0:
                from investagent.llm import get_llm_stats
                s = get_llm_stats()
                logger.info(
                    "LLM stats: %d calls (%d ok / %d err / %d retry) | "
                    "avg %.1fs | in=%dk out=%dk tokens | throughput=%.1f calls/min",
                    s["calls"], s["successes"], s["errors"], s["retries"],
                    s["avg_latency"],
                    s["total_input_tokens"] / 1000, s["total_output_tokens"] / 1000,
                    s["successes"] / (total_elapsed / 60) if total_elapsed > 0 else 0,
                )
            return result

    results = await asyncio.gather(*[_run(s) for s in stocks], return_exceptions=True)
    # Log any exceptions that slipped through
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            ticker = stocks[i].get("ticker", "?")
            logger.error("Pipeline gather exception for %s: %s", ticker, r, exc_info=r)
            results[i] = {
                "ticker": ticker, "name": stocks[i].get("name", ""),
                "final_label": "ERROR", "error": str(r),
                "stopped": True, "agents_completed": 0,
            }
    return list(results)


# ---------------------------------------------------------------------------
# Phase 5: Portfolio
# ---------------------------------------------------------------------------

async def build_portfolio(
    results: list[dict], llm: LLMClient, as_of_date: date | None = None,
) -> dict:
    """Build portfolio via Part 2 decision pipeline (cross-comparison + strategy)."""
    store = CandidateStore(OUTPUT_DIR / "candidate_store.json")
    scan_date = as_of_date or date.today()
    store.ingest_scan_results(results, scan_date)

    actionable = store.get_actionable_candidates()
    if not actionable:
        logger.info("No actionable candidates. Portfolio: 100%% cash.")
        store.save()
        return {"allocations": [], "cash_weight": 1.0}

    logger.info("Building portfolio from %d actionable candidates", len(actionable))

    try:
        allocations = await run_decision_pipeline(store, llm, scan_date=scan_date)
        portfolio = {
            "allocations": [
                {"ticker": t, "weight": w}
                for t, w in allocations.items()
            ],
            "cash_weight": 1.0 - sum(allocations.values()),
        }
        return portfolio
    except Exception as e:
        logger.error("Portfolio construction failed: %s", e)
        return {"allocations": [], "cash_weight": 1.0, "error": str(e)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(
    top_n: int = 2000,
    pipeline_concurrency: int = 5,
    screening_concurrency: int = 20,
    ratio_concurrency: int = 5,
    as_of_date: date | None = None,
) -> None:
    global OUTPUT_DIR, CHECKPOINT_DIR

    # ---- Run isolation via RunManager ----
    rm = RunManager(_DATA_ROOT)
    as_of_str = as_of_date.isoformat() if as_of_date else None
    resumable = rm.find_resumable("overnight", as_of_date=as_of_str)
    if resumable:
        run_meta = resumable
        run_dir = rm.get_run_dir(run_meta.run_id)
    else:
        run_meta = rm.create_run(
            "overnight",
            config={"top_n": top_n, "pipeline_concurrency": pipeline_concurrency},
            as_of_date=as_of_str,
        )
        run_dir = rm.get_run_dir(run_meta.run_id)

    OUTPUT_DIR = run_dir
    CHECKPOINT_DIR = run_dir / "checkpoints"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- Shared filing/AkShare cache ----
    filing_cache = FilingCache(_DATA_ROOT / "cache" / "filings")
    akshare_cache = AkShareCache(_DATA_ROOT / "cache" / "akshare")

    # Logging: file gets everything, console gets overnight + warnings
    file_handler = logging.FileHandler(
        str(OUTPUT_DIR / "overnight.log"), encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"),
    )
    logging.getLogger().addHandler(file_handler)
    logging.getLogger().setLevel(logging.WARNING)
    # Allow per-agent timing logs from runner
    logging.getLogger("investagent.workflow.runner").setLevel(logging.INFO)
    logging.getLogger("investagent.llm").setLevel(logging.INFO)

    # Overnight logger: visible on console
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s"),
    )
    logger.addHandler(console)
    logger.setLevel(logging.INFO)

    # Match PDF extraction concurrency to pipeline concurrency
    from investagent.executors import set_cpu_concurrency
    set_cpu_concurrency(pipeline_concurrency)

    total_start = time.time()
    logger.info("=" * 60)
    logger.info("OVERNIGHT A-SHARE EVALUATION")
    logger.info("  Run: %s%s", run_meta.run_id, " (resumed)" if resumable else "")
    logger.info("  Top %d by market cap", top_n)
    logger.info("  Pipeline concurrency: %d", pipeline_concurrency)
    if as_of_date:
        logger.info("  BACKTEST MODE: as_of_date=%s", as_of_date)
    logger.info("  Output: %s", OUTPUT_DIR)
    logger.info("=" * 60)

    llm = create_llm_client("minimax", extra_body={
        "context_window_size": 200000,
        "effort": "high",
    })

    # ---- Proxy rotation (bypass AkShare rate limits) ----
    try:
        from investagent.datasources.proxy_rotator import ClashRotator
        rotator = ClashRotator()
        if rotator.available:
            # Health check all nodes before starting — drop dead ones
            report = rotator.health_check()
            if not rotator.available:
                logger.warning("All proxy nodes failed health check, using direct connection")
                rotator = None
            else:
                rotator.patch_requests()
                rotator.rotate()
                logger.info("Clash proxy rotation enabled (%d healthy nodes)", len(rotator._nodes))
        else:
            rotator = None
    except Exception:
        logger.info("Clash proxy not available, using direct connection")
        rotator = None

    # ---- Phase 1: Universe ----
    cached_universe = _load_checkpoint("_meta", "universe")
    if cached_universe and cached_universe.get("stocks"):
        universe = cached_universe["stocks"]
        logger.info("Phase 1: loaded %d stocks from cache", len(universe))
    else:
        logger.info("Phase 1: Building universe...")
        t0 = time.time()
        universe = await asyncio.to_thread(build_top_universe, top_n)
        logger.info("Phase 1 done: %d stocks (%.1fs)", len(universe), time.time() - t0)
        _save_checkpoint("_meta", "universe", {
            "count": len(universe),
            "stocks": [{"ticker": s["ticker"], "name": s["name"],
                         "market_cap": s["market_cap"], "industry": s["industry"]}
                        for s in universe],
        })

    # ---- Phase 2: Ratios ----
    logger.info("Phase 2: Computing ratios for %d stocks...", len(universe))
    t0 = time.time()
    universe = await compute_all_ratios(universe, concurrency=ratio_concurrency, as_of_date=as_of_date, rotator=rotator)
    has_ratios = sum(1 for s in universe if s.get("ratios"))
    logger.info("Phase 2 done: %d/%d have ratios (%.1fs)", has_ratios, len(universe), time.time() - t0)

    # ---- Phase 2.5: Quantitative pre-filter ----
    pre_filtered = []
    pre_skipped = []
    for s in universe:
        reason = should_skip_by_ratios(s.get("ratios", {}))
        if reason:
            s["prefilter_skip"] = reason
            pre_skipped.append(s)
        else:
            pre_filtered.append(s)
    logger.info("Pre-filter: %d pass, %d skip (consecutive loss/low ROE/shrinking rev/poor cash)",
                len(pre_filtered), len(pre_skipped))

    # ---- Phase 3: Screening ----
    logger.info("Phase 3: LLM screening %d stocks...", len(pre_filtered))
    t0 = time.time()
    proceed, skipped = await screen_all(pre_filtered, llm, concurrency=screening_concurrency)
    logger.info("Phase 3 done: %d PROCEED, %d SKIP (%.1fs)",
                len(proceed), len(skipped), time.time() - t0)

    # Save screening summary
    _save_checkpoint("_meta", "screening_summary", {
        "total": len(universe),
        "proceed": len(proceed),
        "skipped": len(skipped),
        "pass_rate": f"{len(proceed) / max(len(universe), 1) * 100:.1f}%",
        "proceed_tickers": [s["ticker"] for s in proceed],
        "skip_sample": [
            {"ticker": s["ticker"], "name": s["name"], "reason": s.get("reason", "")}
            for s in skipped[:50]
        ],
    })

    # ---- Phase 4: Full pipeline ----
    logger.info("Phase 4: Running full pipeline on %d stocks (concurrency=%d)...",
                len(proceed), pipeline_concurrency)
    t0 = time.time()
    pipeline_results = await pipeline_all(proceed, llm, concurrency=pipeline_concurrency, as_of_date=as_of_date, filing_cache=filing_cache)
    phase4_elapsed = time.time() - t0
    logger.info("Phase 4 done: %d analyzed (%.1fh)", len(pipeline_results), phase4_elapsed / 3600)

    # ---- Phase 5: Portfolio (Part 2 Decision Pipeline) ----
    logger.info("Phase 5: Portfolio construction (cross-comparison + strategy)...")
    portfolio = await build_portfolio(pipeline_results, llm, as_of_date=as_of_date)

    # ---- Phase 6: Report ----
    total_elapsed = time.time() - total_start

    label_counts: dict[str, int] = {}
    for r in pipeline_results:
        label = r.get("final_label", "?")
        label_counts[label] = label_counts.get(label, 0) + 1

    top_candidates = sorted(
        [r for r in pipeline_results
         if r.get("final_label") in ("INVESTABLE", "DEEP_DIVE", "WATCHLIST", "SPECIAL_SITUATION")],
        key=lambda r: (
            {"INVESTABLE": 0, "DEEP_DIVE": 1, "SPECIAL_SITUATION": 2, "WATCHLIST": 3}
            .get(r.get("final_label", ""), 9),
            -(r.get("margin_of_safety_pct") or -999),
        ),
    )

    summary = {
        "run_date": date.today().isoformat(),
        "as_of_date": as_of_date.isoformat() if as_of_date else None,
        "config": {
            "top_n": top_n,
            "pipeline_concurrency": pipeline_concurrency,
            "screening_concurrency": screening_concurrency,
        },
        "timing": {
            "total_seconds": round(total_elapsed, 1),
            "total_hours": round(total_elapsed / 3600, 2),
            "pipeline_hours": round(phase4_elapsed / 3600, 2),
        },
        "universe_size": len(universe),
        "screening": {
            "proceed": len(proceed),
            "skipped": len(skipped),
            "pass_rate": f"{len(proceed) / max(len(universe), 1) * 100:.1f}%",
        },
        "pipeline": {
            "total_analyzed": len(pipeline_results),
            "label_distribution": label_counts,
        },
        "top_candidates": [
            {
                "ticker": r["ticker"],
                "name": r.get("name", ""),
                "market_cap_yi": round(r.get("market_cap", 0) / 1e8, 1),
                "industry": r.get("industry", ""),
                "final_label": r.get("final_label"),
                "enterprise_quality": r.get("enterprise_quality"),
                "price_vs_value": r.get("price_vs_value"),
                "margin_of_safety_pct": r.get("margin_of_safety_pct"),
                "meets_hurdle": r.get("meets_hurdle_rate"),
                "thesis": (r.get("thesis") or "")[:300],
            }
            for r in top_candidates[:50]
        ],
        "portfolio": portfolio,
    }

    output_file = OUTPUT_DIR / "results.json"
    output_file.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    full_file = OUTPUT_DIR / "full_pipeline_results.json"
    full_file.write_text(
        json.dumps(pipeline_results, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    screening_file = OUTPUT_DIR / "screening_results.json"
    screening_file.write_text(
        json.dumps(proceed + skipped, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # Print summary
    logger.info("=" * 60)
    logger.info("OVERNIGHT EVALUATION COMPLETE")
    logger.info("=" * 60)
    logger.info("Total time: %.1fh (pipeline: %.1fh)", total_elapsed / 3600, phase4_elapsed / 3600)
    logger.info("Universe: %d -> Screening: %d PROCEED -> Pipeline: %d analyzed",
                len(universe), len(proceed), len(pipeline_results))
    logger.info("Label distribution: %s",
                " | ".join(f"{k}: {v}" for k, v in sorted(label_counts.items())))
    if top_candidates:
        logger.info("Top candidates:")
        for r in top_candidates[:15]:
            logger.info(
                "  %s %s [%s] %s | Q=%s V=%s MoS=%.1f%%",
                r["ticker"], r.get("name", ""),
                r.get("industry", ""),
                r.get("final_label", "?"),
                r.get("enterprise_quality", "?"),
                r.get("price_vs_value", "?"),
                r.get("margin_of_safety_pct") or 0,
            )
    logger.info("Portfolio: %d positions, %.0f%% cash",
                len(portfolio.get("allocations", [])),
                portfolio.get("cash_weight", 1) * 100)
    logger.info("Results: %s", output_file)

    # Mark run as completed
    rm.complete_run(run_meta.run_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Overnight A-share evaluation")
    parser.add_argument("--top", type=int, default=2000,
                        help="Top N stocks by market cap (default: 2000)")
    parser.add_argument("--pipeline-concurrency", type=int, default=5,
                        help="Concurrent pipeline runs (default: 5)")
    parser.add_argument("--screening-concurrency", type=int, default=20,
                        help="Concurrent screening calls (default: 20)")
    parser.add_argument("--ratio-concurrency", type=int, default=5,
                        help="Concurrent AkShare ratio fetches (default: 5)")
    parser.add_argument("--as-of-date", type=str, default=None,
                        help="Backtest mode: use data as of this date (YYYY-MM-DD)")
    args = parser.parse_args()

    backtest_date = None
    if args.as_of_date:
        backtest_date = date.fromisoformat(args.as_of_date)

    import signal
    import traceback

    def _signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logging.getLogger("overnight").error(
            "Received signal %s (%d) — exiting.\n%s",
            sig_name, signum, "".join(traceback.format_stack(frame)),
        )
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, _signal_handler)

    try:
        asyncio.run(main(
            top_n=args.top,
            pipeline_concurrency=args.pipeline_concurrency,
            screening_concurrency=args.screening_concurrency,
            ratio_concurrency=args.ratio_concurrency,
            as_of_date=backtest_date,
        ))
    except Exception:
        logging.getLogger("overnight").error("Fatal exception", exc_info=True)
        sys.exit(1)
