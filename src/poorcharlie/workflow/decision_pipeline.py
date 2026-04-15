"""Part 2 Decision Pipeline — candidate pool → cross-comparison → portfolio strategy.

Chains CandidateStore, CrossComparisonAgent, and PortfolioStrategyAgent
into a single async pipeline. Output is {ticker: target_weight} compatible
with both overnight reports and backtest replay.
"""

from __future__ import annotations

import logging
from datetime import date

from poorcharlie.agents.cross_comparison import (
    CrossComparisonAgent,
    CrossComparisonInput,
)
from poorcharlie.agents.portfolio_strategy import (
    PortfolioStrategyAgent,
    PortfolioStrategyInput,
    StrategyHoldingInfo,
)
from poorcharlie.llm import LLMClient
from poorcharlie.schemas.candidate import CandidateState, PortfolioHolding
from poorcharlie.schemas.portfolio_strategy import ActionType
from poorcharlie.store.candidate_store import CandidateStore

logger = logging.getLogger(__name__)


async def run_decision_pipeline(
    store: CandidateStore,
    llm: LLMClient,
    scan_date: date | None = None,
) -> dict[str, float]:
    """Run Part 2 decision pipeline.

    1. Load actionable candidates from store
    2. Run CrossComparisonAgent (if >= 2 candidates)
    3. Run PortfolioStrategyAgent
    4. Update store with new holdings
    5. Return {ticker: target_weight}
    """
    candidates = store.get_actionable_candidates()
    logger.info("Decision pipeline: %d actionable candidates", len(candidates))

    if not candidates:
        logger.info("No actionable candidates — 100%% cash")
        store.update_holdings([], scan_date=scan_date)
        store.save()
        return {}

    # Build candidate detail lookup (for PortfolioStrategyAgent)
    candidate_details = {
        c.ticker: c.model_dump(mode="json") for c in candidates
    }

    # ------------------------------------------------------------------
    # Step 1: Cross-comparison (skip if only 1 candidate)
    # ------------------------------------------------------------------
    if len(candidates) >= 2:
        logger.info("Running CrossComparisonAgent on %d candidates", len(candidates))
        comparison_input = CrossComparisonInput(
            candidates=[c.model_dump(mode="json") for c in candidates],
        )
        comparison_agent = CrossComparisonAgent(llm)
        try:
            comparison_output = await comparison_agent.run(comparison_input)
            ranked = [r.model_dump(mode="json") for r in comparison_output.ranked_candidates]
            if comparison_output.concentration_warnings:
                for w in comparison_output.concentration_warnings:
                    logger.warning("Concentration: %s", w)
        except Exception:
            logger.error(
                "CrossComparison failed — preserving current holdings as-is "
                "instead of forcing a PortfolioStrategy rewrite (would almost "
                "certainly cause unjustified churn).",
                exc_info=True,
            )
            # Safer fallback: don't pretend we have a ranking. Just return
            # the current holdings unchanged; next scheduled scan can retry.
            store.save()
            return store.to_portfolio_decisions()
    else:
        c = candidates[0]
        ranked = [{
            "ticker": c.ticker,
            "name": c.name,
            "rank": 1,
            "conviction_score": 7,
            "strengths_vs_peers": [],
            "weaknesses_vs_peers": [],
            "portfolio_fit_notes": "唯一候选标的",
        }]

    # ------------------------------------------------------------------
    # Step 2: Portfolio strategy
    # ------------------------------------------------------------------
    current_holdings = store.get_current_holdings()
    current_weight = sum(h.target_weight for h in current_holdings)

    strategy_input = PortfolioStrategyInput(
        ranked_candidates=ranked,
        candidate_details=candidate_details,
        current_holdings=[
            StrategyHoldingInfo(
                ticker=h.ticker,
                name=h.name,
                weight=h.target_weight,
                industry=h.industry,
                entry_reason=h.entry_reason,
            )
            for h in current_holdings
        ],
        available_cash_pct=1.0 - current_weight,
    )

    logger.info("Running PortfolioStrategyAgent")
    strategy_agent = PortfolioStrategyAgent(llm)
    try:
        strategy_output = await strategy_agent.run(strategy_input)
    except Exception:
        logger.error("PortfolioStrategy failed, keeping current holdings", exc_info=True)
        store.save()
        return store.to_portfolio_decisions()

    # ------------------------------------------------------------------
    # Step 3: Update store and return decisions
    # ------------------------------------------------------------------
    effective_date = scan_date or date.today()
    # Preserve entry_price/date for existing holdings (don't reset on re-eval)
    existing_entries = {
        h.ticker: (h.entry_date, h.entry_price)
        for h in store.get_current_holdings()
    }
    new_holdings = []
    for d in strategy_output.position_decisions:
        if d.action == ActionType.EXIT or d.target_weight <= 0:
            continue
        detail = candidate_details.get(d.ticker, {})
        prev_date, prev_price = existing_entries.get(d.ticker, (None, None))
        entry_date = prev_date or effective_date
        entry_price = prev_price if prev_price is not None else detail.get("scan_close_price")
        new_holdings.append(PortfolioHolding(
            ticker=d.ticker,
            name=d.name,
            industry=detail.get("industry", ""),
            target_weight=d.target_weight,
            entry_date=entry_date,
            entry_reason=d.reason,
            entry_price=entry_price,
        ))

    store.update_holdings(new_holdings, scan_date=effective_date)
    store.save()

    allocations = {h.ticker: h.target_weight for h in new_holdings}
    logger.info(
        "Decision pipeline complete: %d positions, %.0f%% cash",
        len(allocations),
        (1.0 - sum(allocations.values())) * 100,
    )
    for d in strategy_output.position_decisions:
        logger.info("  %s %s: %s %.0f%% - %s",
                     d.ticker, d.name, d.action.value,
                     d.target_weight * 100, d.reason)

    return allocations


def _fallback_ranking(candidates: list) -> list[dict]:
    """Simple deterministic ranking when CrossComparison fails."""
    quality_order = {"GREAT": 0, "GOOD": 1, "AVERAGE": 2, "BELOW_AVERAGE": 3, "POOR": 4}

    def sort_key(c):
        q = quality_order.get(c.enterprise_quality, 5)
        mos = c.margin_of_safety_pct if c.margin_of_safety_pct is not None else -100
        return (q, -mos)

    sorted_candidates = sorted(candidates, key=sort_key)
    return [
        {
            "ticker": c.ticker,
            "name": c.name,
            "rank": i + 1,
            "conviction_score": max(1, 8 - sort_key(c)[0]),
            "strengths_vs_peers": [],
            "weaknesses_vs_peers": [],
            "portfolio_fit_notes": "",
        }
        for i, c in enumerate(sorted_candidates)
    ]
