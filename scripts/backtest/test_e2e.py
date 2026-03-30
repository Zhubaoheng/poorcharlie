"""End-to-end integration test: screening → pipeline → portfolio.

Uses real MiniMax API to verify the full backtest pipeline works.
Runs on 3 stocks to keep costs minimal.

Usage:
    export MINIMAX_API_KEY="your-key"
    uv run python scripts/backtest/test_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

# Load .env from project root
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from investagent.config import create_llm_client
from investagent.llm import LLMClient
from investagent.schemas.company import CompanyIntake
from investagent.schemas.filing import BalanceSheetRow, CashFlowRow, IncomeStatementRow
from investagent.screening.ratio_calc import compute_ratios
from investagent.screening.screener import ScreenerAgent, ScreenerInput
from investagent.workflow.orchestrator import run_pipeline
from investagent.agents.portfolio import (
    CandidateInfo,
    PortfolioAgent,
    PortfolioInput,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("e2e")

# 3 test stocks: high-quality, average, weak
TEST_STOCKS = [
    {"ticker": "600519", "name": "贵州茅台", "exchange": "SSE", "industry": "食品饮料"},
    {"ticker": "000333", "name": "美的集团", "exchange": "SZSE", "industry": "家用电器"},
    {"ticker": "002594", "name": "比亚迪", "exchange": "SZSE", "industry": "汽车"},
]


def _create_llm() -> LLMClient:
    """Create MiniMax LLM client."""
    return create_llm_client("minimax", extra_body={
        "context_window_size": 200000,
        "effort": "high",
    })


async def step1_screening(llm: LLMClient) -> list[dict]:
    """Run screening on test stocks."""
    logger.info("=" * 50)
    logger.info("STEP 1: SCREENING")
    logger.info("=" * 50)

    screener = ScreenerAgent(llm=llm)
    results = []

    for stock in TEST_STOCKS:
        ticker = stock["ticker"]
        logger.info("Screening %s %s ...", ticker, stock["name"])

        # Fetch real financial data
        try:
            from investagent.datasources.akshare_source import fetch_a_share_financials
            financials = fetch_a_share_financials(ticker)
            income = [IncomeStatementRow(**r) for r in financials.get("income_statement", [])]
            balance = [BalanceSheetRow(**r) for r in financials.get("balance_sheet", [])]
            cash_flow = [CashFlowRow(**r) for r in financials.get("cash_flow", [])]
            ratios = compute_ratios(income, balance, cash_flow)
            logger.info("  Fetched %d years of financials", len(ratios.get("fiscal_years", [])))
        except Exception as e:
            logger.warning("  Failed to fetch financials: %s", e)
            ratios = {}

        inp = ScreenerInput(
            ticker=ticker,
            name=stock["name"],
            industry=stock["industry"],
            ratios=ratios,
        )

        try:
            result = await screener.run(inp)
            logger.info(
                "  → %s: %s (%s)",
                result.decision, result.reason, result.industry_context,
            )
            results.append({
                **stock,
                "decision": result.decision,
                "reason": result.reason,
            })
        except Exception as e:
            logger.error("  → FAILED: %s", e)
            results.append({**stock, "decision": "PROCEED", "reason": f"error: {e}"})

    proceed = [r for r in results if r["decision"] in ("PROCEED", "SPECIAL_CASE")]
    logger.info("Screening result: %d PROCEED out of %d", len(proceed), len(results))
    return proceed


async def step2_pipeline(stocks: list[dict], llm: LLMClient) -> list[dict]:
    """Run full pipeline on screened stocks."""
    logger.info("=" * 50)
    logger.info("STEP 2: FULL PIPELINE")
    logger.info("=" * 50)

    results = []
    for stock in stocks:
        ticker = stock["ticker"]
        logger.info("Pipeline %s %s ...", ticker, stock["name"])
        start = time.time()

        intake = CompanyIntake(
            ticker=ticker,
            name=stock["name"],
            exchange=stock["exchange"],
            sector=stock.get("industry"),
        )

        try:
            ctx = await run_pipeline(intake, llm=llm)
            elapsed = time.time() - start
            agents = ctx.completed_agents()
            logger.info("  Completed %d agents in %.1fs", len(agents), elapsed)
            logger.info("  Agents: %s", ", ".join(agents))

            if ctx.is_stopped():
                logger.info("  → STOPPED: %s", ctx.stop_reason)
                results.append({
                    **stock,
                    "final_label": "STOPPED",
                    "stop_reason": ctx.stop_reason,
                    "agents_completed": len(agents),
                })
                continue

            # Extract key results
            result_data = {**stock, "agents_completed": len(agents)}
            try:
                committee = ctx.get_result("committee")
                label = committee.final_label.value if hasattr(committee.final_label, "value") else str(committee.final_label)
                result_data["final_label"] = label
                result_data["confidence"] = getattr(committee, "confidence", "")
                result_data["thesis"] = getattr(committee, "thesis", "")
                logger.info("  → %s (confidence: %s)", label, result_data["confidence"])
            except KeyError:
                result_data["final_label"] = "NO_COMMITTEE"

            try:
                fq = ctx.get_result("financial_quality")
                result_data["enterprise_quality"] = getattr(fq, "enterprise_quality", "")
                logger.info("  → Quality: %s", result_data["enterprise_quality"])
            except KeyError:
                pass

            try:
                val = ctx.get_result("valuation")
                result_data["price_vs_value"] = getattr(val, "price_vs_value", "")
                result_data["margin_of_safety_pct"] = getattr(val, "margin_of_safety_pct", None)
                result_data["meets_hurdle_rate"] = getattr(val, "meets_hurdle_rate", False)
                logger.info("  → Valuation: %s (MoS: %s)", result_data["price_vs_value"], result_data.get("margin_of_safety_pct"))
            except KeyError:
                pass

            results.append(result_data)

        except Exception as e:
            elapsed = time.time() - start
            logger.error("  → FAILED after %.1fs: %s", elapsed, e, exc_info=True)
            results.append({**stock, "final_label": "ERROR", "error": str(e)})

    investable = [r for r in results if r.get("final_label") == "INVESTABLE"]
    logger.info("Pipeline result: %d INVESTABLE out of %d", len(investable), len(results))
    return results


async def step3_portfolio(pipeline_results: list[dict], llm: LLMClient) -> dict:
    """Run portfolio construction."""
    logger.info("=" * 50)
    logger.info("STEP 3: PORTFOLIO CONSTRUCTION")
    logger.info("=" * 50)

    candidates = []
    for r in pipeline_results:
        if r.get("final_label") != "INVESTABLE":
            continue
        candidates.append(CandidateInfo(
            ticker=r["ticker"],
            name=r["name"],
            industry=r.get("industry", ""),
            enterprise_quality=r.get("enterprise_quality", ""),
            price_vs_value=r.get("price_vs_value", ""),
            margin_of_safety_pct=r.get("margin_of_safety_pct"),
            meets_hurdle_rate=r.get("meets_hurdle_rate", False),
            thesis=r.get("thesis", ""),
        ))

    if not candidates:
        logger.info("No INVESTABLE candidates — portfolio is all cash")
        return {"allocations": {}, "cash_weight": 1.0}

    logger.info("Building portfolio from %d candidates", len(candidates))
    agent = PortfolioAgent(llm=llm)
    inp = PortfolioInput(candidates=candidates, available_cash_pct=1.0)

    try:
        result = await agent.run(inp)
        allocations = {a.ticker: a.target_weight for a in result.allocations}
        logger.info("Portfolio allocations:")
        for a in result.allocations:
            logger.info("  %s %s: %.0f%% — %s", a.ticker, a.name, a.target_weight * 100, a.reason)
        logger.info("Cash: %.0f%%", result.cash_weight * 100)
        logger.info("Actions: %s", result.rebalance_actions)
        return {
            "allocations": allocations,
            "cash_weight": result.cash_weight,
            "industry_distribution": result.industry_distribution,
        }
    except Exception as e:
        logger.error("Portfolio construction failed: %s", e, exc_info=True)
        return {"allocations": {}, "cash_weight": 1.0, "error": str(e)}


async def main() -> None:
    total_start = time.time()
    logger.info("Starting E2E integration test with MiniMax API")

    llm = _create_llm()

    # Step 1: Screen
    proceed = await step1_screening(llm)

    # Step 2: Pipeline (on stocks that passed screening)
    pipeline_results = await step2_pipeline(proceed, llm)

    # Step 3: Portfolio
    portfolio = await step3_portfolio(pipeline_results, llm)

    # Save results
    output_dir = Path(__file__).resolve().parents[2] / "data" / "e2e_test"
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results = {
        "test_date": date.today().isoformat(),
        "stocks": TEST_STOCKS,
        "screening": proceed,
        "pipeline": pipeline_results,
        "portfolio": portfolio,
        "elapsed_seconds": time.time() - total_start,
    }
    output_file = output_dir / "results.json"
    output_file.write_text(
        json.dumps(all_results, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    total_elapsed = time.time() - total_start
    logger.info("=" * 50)
    logger.info("E2E TEST COMPLETE in %.1fs", total_elapsed)
    logger.info("Results saved to %s", output_file)
    logger.info("=" * 50)

    # Summary
    logger.info("Summary:")
    logger.info("  Screened: %d → %d PROCEED", len(TEST_STOCKS), len(proceed))
    logger.info("  Pipeline: %d analyzed", len(pipeline_results))
    for r in pipeline_results:
        logger.info("    %s %s → %s", r["ticker"], r["name"], r.get("final_label", "?"))
    logger.info("  Portfolio: %d positions, %.0f%% cash",
                len(portfolio.get("allocations", {})),
                portfolio.get("cash_weight", 1) * 100)


if __name__ == "__main__":
    asyncio.run(main())
