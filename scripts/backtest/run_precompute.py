"""Pre-compute investment decisions for all scan dates.

Phase 1 of the backtest: run the poorcharlie pipeline for each decision
point (scheduled scans + price triggers), serialize results to JSON for
Phase 2 (backtrader replay).

Usage:
    uv run python scripts/backtest/run_precompute.py [--concurrency 5]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

# Bypass proxy for Chinese domestic sites (cninfo, AkShare backends).
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

from poorcharlie.config import create_llm_client
from poorcharlie.llm import LLMClient
from poorcharlie.schemas.company import CompanyIntake
from poorcharlie.schemas.filing import BalanceSheetRow, CashFlowRow, IncomeStatementRow
from poorcharlie.screening.ratio_calc import compute_ratios
from poorcharlie.screening.screener import ScreenerAgent, ScreenerInput
from poorcharlie.screening.universe import build_universe
from poorcharlie.workflow.orchestrator import run_pipeline
from poorcharlie.agents.portfolio import (
    CandidateInfo,
    HoldingInfo,
    PortfolioAgent,
    PortfolioInput,
)
from poorcharlie.store.candidate_store import CandidateStore
from poorcharlie.store.run_manager import RunManager
from poorcharlie.datasources.cache import FilingCache, AkShareCache
from poorcharlie.workflow.decision_pipeline import run_decision_pipeline

from temporal import TemporalValidator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("precompute")

# Scan dates per spec
# Scan dates: 2-3 weeks after earnings filing deadlines to let
# the market fully digest new disclosures.
#   年报/一季报 deadline: 4/30 → scan mid-May
#   半年报 deadline: 8/31 → scan mid-Sep
#   三季报 deadline: 10/31 → scan mid-Nov
SCAN_DATES = [
    date(2023, 11, 18),   # S0: 三季报截止 10/31 + 18 天
    date(2024, 5, 20),    # S1: 年报截止 4/30 + 20 天
    date(2024, 9, 23),    # S2: 半年报截止 8/31 + 23 天
    date(2025, 5, 19),    # S3: 年报截止 4/30 + 19 天
    date(2025, 9, 22),    # S4: 半年报截止 8/31 + 22 天
]

_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
DATA_DIR = _DATA_ROOT / "backtest"

# Exchange mapping for A-shares
_EXCHANGE_MAP = {
    "6": "SSE", "9": "SSE",
    "0": "SZSE", "3": "SZSE", "2": "SZSE",
    "4": "BSE", "8": "BSE",
}

# Price trigger thresholds
PRICE_TRIGGER_DOWN = 0.20
PRICE_TRIGGER_UP = 0.50


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_checkpoint(scan_dir: Path) -> dict[str, dict]:
    """Return {ticker: result_dict} for already-processed companies."""
    done = {}
    if scan_dir.exists():
        for f in scan_dir.glob("*.json"):
            if f.stem.startswith("_"):
                continue
            try:
                done[f.stem] = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
    return done


def _save_result(scan_dir: Path, key: str, result: dict) -> None:
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / f"{key}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _ticker_to_exchange(ticker: str) -> str:
    first = ticker[0] if ticker else "6"
    return _EXCHANGE_MAP.get(first, "SSE")


# ---------------------------------------------------------------------------
# Stage 1: Screening
# ---------------------------------------------------------------------------

async def _screen_one(
    stock: dict,
    screener: ScreenerAgent,
) -> dict:
    """Screen a single stock. Returns result dict."""
    ticker = stock["ticker"]
    try:
        from poorcharlie.datasources.akshare_source import fetch_a_share_financials
        financials = await asyncio.to_thread(fetch_a_share_financials, ticker)
        income = [IncomeStatementRow(**r) for r in financials.get("income_statement", [])]
        balance = [BalanceSheetRow(**r) for r in financials.get("balance_sheet", [])]
        cash_flow = [CashFlowRow(**r) for r in financials.get("cash_flow", [])]
        ratios = compute_ratios(income, balance, cash_flow)
    except Exception:
        logger.warning("Failed to compute ratios for %s", ticker, exc_info=True)
        ratios = {}

    inp = ScreenerInput(
        ticker=ticker,
        name=stock.get("name", ""),
        industry=stock.get("industry", ""),
        main_business=stock.get("main_business", ""),
        listing_date=stock.get("listing_date", ""),
        ratios=ratios,
    )
    try:
        result = await screener.run(inp)
        return {
            "ticker": ticker,
            "name": stock.get("name", ""),
            "industry": stock.get("industry", ""),
            "decision": result.decision,
            "reason": result.reason,
            "stage": "screener",
        }
    except Exception:
        logger.warning("Screener failed for %s, defaulting SKIP", ticker, exc_info=True)
        return {
            "ticker": ticker, "name": stock.get("name", ""),
            "decision": "SKIP", "reason": "screener_error", "stage": "screener",
        }


async def run_screening(
    universe: list[dict],
    analysis_llm: LLMClient,
    scan_dir: Path,
    checkpoint: dict[str, dict],
    concurrency: int,
) -> dict[str, dict]:
    """Run Stage 1 screening on the universe. Returns {ticker: result}."""
    screener = ScreenerAgent(llm=analysis_llm)
    results: dict[str, dict] = {}
    sem = asyncio.Semaphore(concurrency)

    async def _process(stock: dict) -> tuple[str, dict]:
        ticker = stock["ticker"]
        if ticker in checkpoint and checkpoint[ticker].get("stage") in ("screener", "pipeline"):
            return ticker, checkpoint[ticker]
        async with sem:
            result = await _screen_one(stock, screener)
            _save_result(scan_dir, ticker, result)
            return ticker, result

    tasks = [_process(s) for s in universe]
    for i, coro in enumerate(asyncio.as_completed(tasks)):
        ticker, result = await coro
        results[ticker] = result
        done = i + 1
        if done % 100 == 0 or done == len(tasks):
            logger.info("Screening progress: %d / %d", done, len(tasks))

    proceed = sum(1 for r in results.values() if r.get("decision") in ("PROCEED", "SPECIAL_CASE"))
    logger.info("Screening: %d SKIP, %d PROCEED/SPECIAL_CASE", len(results) - proceed, proceed)
    return results


# ---------------------------------------------------------------------------
# Stage 2: Full pipeline
# ---------------------------------------------------------------------------

VALUATION_MOS_THRESHOLD = 0.80  # base_iv * 0.8 → 20% margin of safety


def _compute_valuation_trigger_ratio(result: dict) -> float | None:
    """Compute (base_iv * 0.8) / scan_close for valuation trigger detection.

    Returns a dimensionless ratio that is stable across forward-adjustment
    basis changes (splits/dividends). At trigger-check time, multiply the
    re-fetched anchor close by this ratio to get the adjusted trigger price.
    """
    base_iv = result.get("intrinsic_value_base")
    scan_close = result.get("scan_close_price")
    if not base_iv or not scan_close or scan_close <= 0 or base_iv <= 0:
        return None
    trigger_price = base_iv * VALUATION_MOS_THRESHOLD
    ratio = round(trigger_price / scan_close, 4)
    # Only meaningful if trigger price < current price (stock not already cheap enough)
    if ratio >= 1.0:
        return None
    return ratio


def _extract_pipeline_result(ctx: Any) -> dict:
    """Extract key fields from PipelineContext for serialization."""
    result: dict[str, Any] = {
        "stopped": ctx.is_stopped(),
        "stop_reason": ctx.stop_reason,
        "completed_agents": ctx.completed_agents(),
    }

    # Extract committee output if available
    try:
        committee = ctx.get_result("committee")
        result["final_label"] = committee.final_label.value if hasattr(committee.final_label, "value") else str(committee.final_label)
        result["thesis"] = getattr(committee, "thesis", "")
        result["anti_thesis"] = getattr(committee, "anti_thesis", "")
        result["why_now"] = getattr(committee, "why_now_or_why_not_now", "")
        result["next_action"] = getattr(committee, "next_action", "")
        result["largest_unknowns"] = getattr(committee, "largest_unknowns", [])
        result["expected_return_summary"] = getattr(committee, "expected_return_summary", "")
    except KeyError:
        result["final_label"] = "STOPPED"
        result["thesis"] = ctx.stop_reason or ""

    # Extract valuation output if available
    try:
        valuation = ctx.get_result("valuation")
        result["price_vs_value"] = getattr(valuation, "price_vs_value", "")
        result["margin_of_safety_pct"] = getattr(valuation, "margin_of_safety_pct", None)
        result["meets_hurdle_rate"] = getattr(valuation, "meets_hurdle_rate", False)
        iv = getattr(valuation, "intrinsic_value_per_share", None)
        if iv:
            result["intrinsic_value_base"] = iv.base
    except KeyError:
        pass

    # Extract scan-date market price for valuation trigger ratio
    try:
        info = ctx.get_result("info_capture")
        ms = getattr(info, "market_snapshot", None)
        if ms and ms.price:
            result["scan_close_price"] = ms.price
    except KeyError:
        pass

    # Compute valuation trigger ratio: (base_iv * 0.8) / scan_close
    result["valuation_trigger_ratio"] = _compute_valuation_trigger_ratio(result)

    # Extract financial quality if available
    try:
        fq = ctx.get_result("financial_quality")
        result["enterprise_quality"] = getattr(fq, "enterprise_quality", "")
    except KeyError:
        pass

    return result


async def run_full_pipeline(
    stocks_to_analyze: list[dict],
    analysis_llm: LLMClient,
    scan_dir: Path,
    checkpoint: dict[str, dict],
    concurrency: int,
    pipeline_concurrency: int = 5,
    scan_date: date | None = None,
) -> dict[str, dict]:
    """Run Stage 2 full pipeline on screened companies."""
    results: dict[str, dict] = {}
    sem = asyncio.Semaphore(pipeline_concurrency)

    async def _run_one(stock: dict) -> tuple[str, dict]:
        ticker = stock["ticker"]

        # Check checkpoint — skip if already has pipeline result
        if ticker in checkpoint and checkpoint[ticker].get("stage") == "pipeline":
            return ticker, checkpoint[ticker]

        async with sem:
            intake = CompanyIntake(
                ticker=ticker,
                name=stock.get("name", ticker),
                exchange=_ticker_to_exchange(ticker),
                sector=stock.get("industry"),
                as_of_date=scan_date,
            )
            try:
                ctx = await run_pipeline(intake, llm=analysis_llm)
                result = _extract_pipeline_result(ctx)
                result.update({
                    "ticker": ticker,
                    "name": stock.get("name", ""),
                    "industry": stock.get("industry", ""),
                    "stage": "pipeline",
                })
                _save_result(scan_dir, ticker, result)
                logger.info(
                    "Pipeline %s: %s (stopped=%s)",
                    ticker, result.get("final_label", "?"), result.get("stopped"),
                )
                return ticker, result
            except Exception:
                logger.error("Pipeline failed for %s", ticker, exc_info=True)
                result = {
                    "ticker": ticker, "name": stock.get("name", ""),
                    "stage": "pipeline", "final_label": "ERROR",
                    "stopped": True, "stop_reason": "pipeline_error",
                }
                _save_result(scan_dir, ticker, result)
                return ticker, result

    tasks = [_run_one(s) for s in stocks_to_analyze]
    for i, coro in enumerate(asyncio.as_completed(tasks)):
        ticker, result = await coro
        results[ticker] = result
        done = i + 1
        if done % 10 == 0 or done == len(tasks):
            logger.info("Pipeline progress: %d / %d", done, len(tasks))

    investable = sum(1 for r in results.values() if r.get("final_label") == "INVESTABLE")
    logger.info("Pipeline: %d INVESTABLE out of %d analyzed", investable, len(results))
    return results


# ---------------------------------------------------------------------------
# Portfolio construction
# ---------------------------------------------------------------------------

async def run_portfolio_construction(
    pipeline_results: dict[str, dict],
    analysis_llm: LLMClient,
    current_holdings: list[dict],
    scan_dir: Path,
) -> dict[str, float]:
    """Run portfolio construction agent. Returns {ticker: target_weight}."""
    candidates = []
    for ticker, r in pipeline_results.items():
        if r.get("final_label") != "INVESTABLE":
            continue
        candidates.append(CandidateInfo(
            ticker=ticker,
            name=r.get("name", ""),
            industry=r.get("industry", ""),
            enterprise_quality=r.get("enterprise_quality", ""),
            price_vs_value=r.get("price_vs_value", ""),
            margin_of_safety_pct=r.get("margin_of_safety_pct"),
            meets_hurdle_rate=r.get("meets_hurdle_rate", False),
            thesis=r.get("thesis", ""),
        ))

    holdings = [
        HoldingInfo(
            ticker=h["ticker"],
            name=h.get("name", ""),
            weight=h.get("weight", 0),
            industry=h.get("industry", ""),
        )
        for h in current_holdings
    ]

    current_weight = sum(h.get("weight", 0) for h in current_holdings)
    inp = PortfolioInput(
        candidates=candidates,
        current_holdings=holdings,
        available_cash_pct=1.0 - current_weight,
    )

    agent = PortfolioAgent(llm=analysis_llm)
    try:
        result = await agent.run(inp)
        allocations = {a.ticker: a.target_weight for a in result.allocations}

        # Save portfolio decision
        _save_result(scan_dir, "_portfolio", {
            "allocations": allocations,
            "cash_weight": result.cash_weight,
            "industry_distribution": result.industry_distribution,
            "rebalance_actions": result.rebalance_actions,
            "candidates_count": len(candidates),
        })

        logger.info(
            "Portfolio: %d positions, %.0f%% cash",
            len(allocations), result.cash_weight * 100,
        )
        return allocations
    except Exception:
        logger.error("Portfolio construction failed", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Price triggers
# ---------------------------------------------------------------------------

def detect_price_triggers(
    holdings: dict[str, float],
    entry_prices: dict[str, float],
    scan_start: date,
    scan_end: date,
) -> list[tuple[date, str]]:
    """Detect price trigger events between two scan dates.

    Returns list of (trigger_date, ticker) tuples.
    """
    from data_feeds import fetch_daily_prices

    triggers: list[tuple[date, str]] = []

    for ticker in holdings:
        if ticker not in entry_prices:
            continue
        entry_price = entry_prices[ticker]

        try:
            df = fetch_daily_prices(ticker, scan_start, scan_end)
            for _, row in df.iterrows():
                close = row.get("close")
                dt = row.get("date")
                if close is None or dt is None:
                    continue
                change = (close - entry_price) / entry_price
                if change <= -PRICE_TRIGGER_DOWN:
                    triggers.append((date.fromisoformat(str(dt)[:10]), ticker))
                    break  # only first trigger per stock per period
                elif change >= PRICE_TRIGGER_UP:
                    triggers.append((date.fromisoformat(str(dt)[:10]), ticker))
                    break
        except Exception:
            logger.warning("Price trigger check failed for %s", ticker, exc_info=True)

    logger.info("Detected %d price triggers between %s and %s", len(triggers), scan_start, scan_end)
    return triggers


async def handle_price_triggers(
    triggers: list[tuple[date, str]],
    holdings: dict[str, float],
    analysis_llm: LLMClient,
    pipeline_results: dict[str, dict],
) -> dict[str, dict[str, float]]:
    """Re-run pipeline for triggered stocks, return {date_str: {ticker: weight}}."""
    trigger_decisions: dict[str, dict[str, float]] = {}

    for trigger_date, ticker in triggers:
        trigger_dir = DATA_DIR / f"trigger_{trigger_date.isoformat()}"
        stock = {"ticker": ticker, "name": pipeline_results.get(ticker, {}).get("name", "")}

        logger.info("Price trigger: %s on %s, re-running pipeline", ticker, trigger_date)
        result = await run_full_pipeline(
            [stock], analysis_llm, trigger_dir, {}, concurrency=1,
        )

        # Re-run portfolio with updated info
        updated_results = dict(pipeline_results)
        updated_results.update(result)

        current_holdings = [
            {"ticker": t, "weight": w, "name": pipeline_results.get(t, {}).get("name", "")}
            for t, w in holdings.items()
        ]

        allocations = await run_portfolio_construction(
            updated_results, analysis_llm, current_holdings, trigger_dir,
        )
        if allocations:
            trigger_decisions[trigger_date.isoformat()] = allocations
            holdings = allocations  # update for subsequent triggers

    return trigger_decisions


# ---------------------------------------------------------------------------
# Valuation triggers (WATCHLIST+ opportunity detection)
# ---------------------------------------------------------------------------

def detect_valuation_triggers(
    watchlist: dict[str, dict],
    scan_start: date,
    scan_end: date,
) -> list[tuple[date, str]]:
    """Detect valuation-based price triggers for WATCHLIST+ stocks.

    For each monitored stock, fetch daily prices between scan dates.
    Trigger when close price drops to the 20%-MoS level (base_iv * 0.8).

    Uses a dimensionless trigger_ratio to handle forward-adjustment drift:
    re-fetch prices so all dates share the same qfq basis, then compare
    close against anchor_close * trigger_ratio.

    Returns list of (trigger_date, ticker) tuples, one per stock (first only).
    """
    from data_feeds import fetch_daily_prices

    triggers: list[tuple[date, str]] = []

    for ticker, info in watchlist.items():
        trigger_ratio = info.get("trigger_ratio")
        if trigger_ratio is None or trigger_ratio <= 0:
            continue

        try:
            df = fetch_daily_prices(ticker, scan_start, scan_end)
            if df.empty:
                continue

            # First available close as anchor (same qfq basis as all rows)
            anchor_close = df.iloc[0].get("close")
            if anchor_close is None or anchor_close <= 0:
                continue

            adjusted_trigger_price = anchor_close * trigger_ratio

            for _, row in df.iterrows():
                close = row.get("close")
                dt = row.get("date")
                if close is None or dt is None:
                    continue
                if close <= adjusted_trigger_price:
                    trigger_date = date.fromisoformat(str(dt)[:10])
                    triggers.append((trigger_date, ticker))
                    logger.info(
                        "Valuation trigger: %s on %s, close=%.2f <= trigger=%.2f "
                        "(ratio=%.4f, anchor=%.2f)",
                        ticker, trigger_date, close, adjusted_trigger_price,
                        trigger_ratio, anchor_close,
                    )
                    break  # only first trigger per stock per period
        except Exception:
            logger.warning("Valuation trigger check failed for %s", ticker, exc_info=True)

    logger.info(
        "Detected %d valuation triggers between %s and %s (monitored %d stocks)",
        len(triggers), scan_start, scan_end, len(watchlist),
    )
    return triggers


async def handle_valuation_triggers(
    triggers: list[tuple[date, str]],
    watchlist: dict[str, dict],
    current_holdings: dict[str, float],
    analysis_llm: LLMClient,
    pipeline_results: dict[str, dict],
) -> dict[str, dict[str, float]]:
    """Re-run pipeline for valuation-triggered stocks.

    If re-evaluation upgrades to INVESTABLE, run portfolio construction
    to integrate into portfolio. If still WATCHLIST+, update trigger_ratio
    in pipeline_results for continued monitoring. If downgraded to
    REJECT/TOO_HARD, remove from monitoring.

    Returns {date_str: {ticker: weight}} for triggered portfolio changes.
    """
    trigger_decisions: dict[str, dict[str, float]] = {}

    for trigger_date, ticker in sorted(triggers):
        trigger_dir = DATA_DIR / f"valuation_trigger_{trigger_date.isoformat()}"
        stock = {
            "ticker": ticker,
            "name": watchlist.get(ticker, {}).get("name", ""),
        }

        logger.info("Valuation trigger: %s on %s, re-running pipeline", ticker, trigger_date)
        result = await run_full_pipeline(
            [stock], analysis_llm, trigger_dir, {}, concurrency=1,
        )

        if not result or ticker not in result:
            continue

        new_result = result[ticker]
        new_label = new_result.get("final_label", "")

        # Update pipeline_results with new analysis
        pipeline_results[ticker] = {**pipeline_results.get(ticker, {}), **new_result}

        if new_label == "INVESTABLE":
            logger.info(
                "Valuation trigger: %s upgraded to INVESTABLE on %s, running portfolio construction",
                ticker, trigger_date,
            )
            updated_results = dict(pipeline_results)
            holdings_list = [
                {"ticker": t, "weight": w, "name": pipeline_results.get(t, {}).get("name", "")}
                for t, w in current_holdings.items()
            ]
            allocations = await run_portfolio_construction(
                updated_results, analysis_llm, holdings_list, trigger_dir,
            )
            if allocations:
                trigger_decisions[trigger_date.isoformat()] = allocations
                current_holdings = allocations
        elif new_label in ("WATCHLIST", "DEEP_DIVE", "SPECIAL_SITUATION"):
            logger.info(
                "Valuation trigger: %s remains %s on %s, updating trigger ratio",
                ticker, new_label, trigger_date,
            )
            # trigger_ratio already recomputed in _extract_pipeline_result
        else:
            logger.info(
                "Valuation trigger: %s downgraded to %s on %s, removing from watchlist",
                ticker, new_label, trigger_date,
            )

    return trigger_decisions


# ---------------------------------------------------------------------------
# Incremental universe building
# ---------------------------------------------------------------------------

def build_incremental_universe(
    previous_results: dict[str, dict],
    current_holdings: dict[str, float],
) -> list[dict]:
    """Build the incremental universe for S2-S4 scans.

    Includes:
    - Current holdings (need re-evaluation with new financials)
    - Previous WATCHLIST / DEEP_DIVE / SPECIAL_SITUATION (potential upgrades)
    - Does NOT include previous SKIP (per spec: no looking back)
    """
    universe = []
    seen = set()

    # Current holdings
    for ticker in current_holdings:
        if ticker not in seen:
            info = previous_results.get(ticker, {})
            universe.append({
                "ticker": ticker,
                "name": info.get("name", ""),
                "industry": info.get("industry", ""),
            })
            seen.add(ticker)

    # Previous watchlist / deep_dive / special_situation
    for ticker, result in previous_results.items():
        if ticker in seen:
            continue
        label = result.get("final_label", "")
        if label in ("WATCHLIST", "DEEP_DIVE", "SPECIAL_SITUATION"):
            universe.append({
                "ticker": ticker,
                "name": result.get("name", ""),
                "industry": result.get("industry", ""),
            })
            seen.add(ticker)

    logger.info(
        "Incremental universe: %d holdings + %d watchlist = %d total",
        len(current_holdings), len(universe) - len(current_holdings), len(universe),
    )
    return universe


# ---------------------------------------------------------------------------
# Main scan orchestration
# ---------------------------------------------------------------------------

async def run_scan(
    scan_date: date,
    is_cold_start: bool,
    previous_results: dict[str, dict],
    current_holdings: dict[str, float],
    concurrency: int = 5,
) -> tuple[dict[str, dict], dict[str, float]]:
    """Run a full scan for one decision date.

    Returns: (all_results, portfolio_allocations)
    """
    scan_dir = DATA_DIR / scan_date.isoformat()
    checkpoint = _load_checkpoint(scan_dir)
    logger.info("Scan %s: %d checkpointed results", scan_date, len(checkpoint))

    exclusion_llm = create_llm_client()
    analysis_llm = create_llm_client()

    # Build universe (no LLM exclusion — filter by market cap first)
    if is_cold_start:
        logger.info("Cold start: building full universe")
        universe = await build_universe("A_SHARE", llm=None)
        # Keep only top 500 by market cap, then apply LLM exclusion
        universe.sort(key=lambda s: s.get("market_cap", 0), reverse=True)
        universe = universe[:500]
        logger.info("Trimmed to top 500 by market cap")
        # Skip LLM exclusion if screening already done (checkpoint has results)
        screening_done = sum(1 for v in checkpoint.values() if v.get("decision"))
        if screening_done >= len(universe) * 0.9:
            logger.info("Screening checkpoint covers %d/%d, skipping LLM exclusion", screening_done, len(universe))
        elif exclusion_llm is not None:
            from poorcharlie.screening.universe import apply_llm_exclusions
            universe = await apply_llm_exclusions(universe, exclusion_llm)
            logger.info("After LLM exclusion: %d stocks", len(universe))
    else:
        universe = build_incremental_universe(previous_results, current_holdings)

    logger.info("Universe size: %d", len(universe))

    # Stage 1: Screening (cold start only — incremental skips screening)
    if is_cold_start:
        screen_results = await run_screening(
            universe, analysis_llm, scan_dir, checkpoint, concurrency,
        )
        stocks_for_pipeline = [
            {"ticker": t, "name": r.get("name", ""), "industry": r.get("industry", "")}
            for t, r in screen_results.items()
            if r.get("decision") in ("PROCEED", "SPECIAL_CASE")
        ]
    else:
        screen_results = {}
        stocks_for_pipeline = universe  # incremental: all go to pipeline

    # Stage 2: Full pipeline
    pipeline_results = await run_full_pipeline(
        stocks_for_pipeline, analysis_llm, scan_dir, checkpoint, concurrency,
        scan_date=scan_date,
    )

    # Merge all results
    all_results = {**screen_results, **pipeline_results}

    # Stage 3: Portfolio construction (Part 2 Decision Pipeline)
    store = CandidateStore(DATA_DIR / "candidate_store.json")
    store.ingest_scan_results(list(pipeline_results.values()), scan_date)
    allocations = await run_decision_pipeline(store, analysis_llm, scan_date=scan_date)

    # Save portfolio decision for checkpoint
    _save_result(scan_dir, "_portfolio", {
        "allocations": allocations,
        "cash_weight": 1.0 - sum(allocations.values()),
        "candidates_count": sum(
            1 for r in pipeline_results.values()
            if r.get("final_label") == "INVESTABLE"
        ),
    })

    # Save scan summary
    _save_result(scan_dir, "_summary", {
        "scan_date": scan_date.isoformat(),
        "is_cold_start": is_cold_start,
        "universe_size": len(universe),
        "screened": len(screen_results),
        "pipeline_ran": len(pipeline_results),
        "investable": sum(1 for r in pipeline_results.values() if r.get("final_label") == "INVESTABLE"),
        "portfolio_positions": len(allocations),
    })

    return all_results, allocations


async def main(concurrency: int = 5) -> None:
    """Run pre-computation for all scan dates + price triggers."""
    all_decisions: dict[str, dict[str, float]] = {}  # date_str -> allocations
    previous_results: dict[str, dict] = {}
    current_holdings: dict[str, float] = {}
    entry_prices: dict[str, float] = {}  # ticker -> price at entry

    # Match PDF extraction concurrency to pipeline concurrency
    from poorcharlie.executors import set_cpu_concurrency
    set_cpu_concurrency(concurrency)

    # Run isolation via RunManager
    rm = RunManager(_DATA_ROOT)
    resumable = rm.find_resumable("backtest")
    if resumable:
        run_meta = resumable
        logger.info("Resuming run %s", run_meta.run_id)
    else:
        run_meta = rm.create_run("backtest", config={"concurrency": concurrency})
        logger.info("Created run %s", run_meta.run_id)

    # Shared filing cache
    filing_cache = FilingCache(_DATA_ROOT / "cache" / "filings")
    akshare_cache = AkShareCache(_DATA_ROOT / "cache" / "akshare")

    # CandidateStore persists across scans for incremental state management
    store = CandidateStore(DATA_DIR / "candidate_store.json")

    for i, scan_date in enumerate(SCAN_DATES):
        logger.info("=" * 60)
        logger.info("SCAN %d/%d: %s", i + 1, len(SCAN_DATES), scan_date)
        logger.info("=" * 60)

        results, allocations = await run_scan(
            scan_date=scan_date,
            is_cold_start=(i == 0),
            previous_results=previous_results,
            current_holdings=current_holdings,
            concurrency=concurrency,
        )

        all_decisions[scan_date.isoformat()] = allocations
        previous_results.update(results)
        current_holdings = allocations

        # Estimate entry prices (use scan date close — actual prices fetched by backtrader)
        for ticker in allocations:
            if ticker not in entry_prices:
                entry_prices[ticker] = 0  # placeholder, will be updated from price data

        # Price triggers DISABLED per Munger-style redesign: volatility != risk.
        # Only fundamental checkpoints (scheduled scans) drive decisions on holdings.
        # The detect_price_triggers function is retained for diagnostic/attribution
        # analysis only. To re-enable, restore the call block from git history.
        if i < len(SCAN_DATES) - 1:
            next_scan = SCAN_DATES[i + 1]
            try:
                triggers = await asyncio.to_thread(
                    detect_price_triggers,
                    current_holdings, entry_prices,
                    scan_date + timedelta(days=1), next_scan - timedelta(days=1),
                )
                if triggers:
                    for td, tk in triggers:
                        logger.info(
                            "Price trigger observed (diagnostic, no action): %s on %s",
                            tk, td,
                        )
            except Exception:
                logger.warning("Price trigger diagnostic scan failed", exc_info=True)

            # Valuation triggers: monitor WATCHLIST+ non-held stocks
            try:
                watchlist = store.get_valuation_watchlist(
                    exclude_tickers=set(current_holdings.keys()),
                )
                if watchlist:
                    logger.info(
                        "Checking valuation triggers for %d WATCHLIST+ stocks: %s to %s",
                        len(watchlist), scan_date, next_scan,
                    )
                    val_triggers = await asyncio.to_thread(
                        detect_valuation_triggers,
                        watchlist,
                        scan_date + timedelta(days=1), next_scan - timedelta(days=1),
                    )
                    if val_triggers:
                        analysis_llm = create_llm_client()
                        val_decisions = await handle_valuation_triggers(
                            val_triggers, watchlist, current_holdings,
                            analysis_llm, previous_results,
                        )
                        all_decisions.update(val_decisions)
                        if val_decisions:
                            last_val = sorted(val_decisions.keys())[-1]
                            current_holdings = val_decisions[last_val]
                        # Re-ingest updated results into store
                        updated = [
                            {**previous_results[t], "ticker": t}
                            for _, t in val_triggers
                            if t in previous_results
                        ]
                        if updated:
                            store.ingest_scan_results(updated, scan_date)
                            store.save()
            except Exception:
                logger.error("Valuation trigger processing failed", exc_info=True)

    # Save all decisions for backtrader
    decisions_file = DATA_DIR / "all_decisions.json"
    decisions_file.write_text(
        json.dumps(all_decisions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Saved %d decision points to %s", len(all_decisions), decisions_file)
    logger.info("Pre-computation complete")
    rm.complete_run(run_meta.run_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-compute backtest decisions")
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(concurrency=args.concurrency))
