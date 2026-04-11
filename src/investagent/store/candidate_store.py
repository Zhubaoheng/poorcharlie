"""JSON-backed persistent candidate pool and portfolio state."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from investagent.schemas.candidate import (
    CandidateSnapshot,
    CandidateState,
    PortfolioHolding,
    StoreState,
)

logger = logging.getLogger(__name__)

# Labels worth tracking in the candidate pool
_ACTIONABLE_LABELS = frozenset({
    "INVESTABLE", "DEEP_DIVE", "WATCHLIST", "SPECIAL_SITUATION",
})


class CandidateStore:
    """Persistent candidate pool bridging Part 1 analysis to Part 2 decisions.

    Backed by a single JSON file. Tracks all analyzed companies, their latest
    analysis snapshots, and current portfolio holdings across scans.
    """

    def __init__(self, store_path: Path) -> None:
        self._path = store_path
        if store_path.exists():
            raw = json.loads(store_path.read_text(encoding="utf-8"))
            self._state = StoreState.model_validate(raw)
            logger.info(
                "Loaded CandidateStore: %d candidates, %d holdings",
                len(self._state.candidates), len(self._state.holdings),
            )
        else:
            self._state = StoreState(last_updated=datetime.now(tz=timezone.utc))
            logger.info("Created fresh CandidateStore at %s", store_path)

    # ------------------------------------------------------------------
    # Ingest Part 1 results
    # ------------------------------------------------------------------

    def ingest_scan_results(
        self, results: list[dict], scan_date: date,
    ) -> None:
        """Absorb Part 1 pipeline outputs into candidate snapshots.

        Creates or updates CandidateSnapshots for actionable labels
        (INVESTABLE, DEEP_DIVE, WATCHLIST, SPECIAL_SITUATION).
        Non-actionable results (REJECT, TOO_HARD, errors) are removed
        from the pool if previously tracked.
        """
        for r in results:
            ticker = r.get("ticker", "")
            if not ticker:
                continue

            label = r.get("final_label", "")
            if label in _ACTIONABLE_LABELS:
                # Preserve HELD state — a re-evaluated holding should stay
                # HELD so PortfolioStrategy knows it's a current position.
                prev = self._state.candidates.get(ticker)
                state = (
                    CandidateState.HELD
                    if prev is not None and prev.state == CandidateState.HELD
                    else CandidateState.ANALYZED
                )
                snapshot = CandidateSnapshot(
                    ticker=ticker,
                    name=r.get("name", ""),
                    industry=r.get("industry", ""),
                    final_label=label,
                    enterprise_quality=r.get("enterprise_quality", ""),
                    price_vs_value=r.get("price_vs_value", ""),
                    margin_of_safety_pct=r.get("margin_of_safety_pct"),
                    meets_hurdle_rate=r.get("meets_hurdle_rate", False),
                    thesis=r.get("thesis", ""),
                    anti_thesis=r.get("anti_thesis", ""),
                    largest_unknowns=r.get("largest_unknowns") or [],
                    expected_return_summary=r.get("expected_return_summary", ""),
                    why_now=r.get("why_now_or_why_not_now", r.get("why_now", "")),
                    scan_date=scan_date,
                    state=state,
                    intrinsic_value_base=r.get("intrinsic_value_base"),
                    scan_close_price=r.get("scan_close_price"),
                    valuation_trigger_ratio=r.get("valuation_trigger_ratio"),
                )
                self._state.candidates[ticker] = snapshot
            else:
                # Remove rejected/failed companies from pool
                if ticker in self._state.candidates:
                    prev = self._state.candidates[ticker]
                    # If currently held, mark as EXITED rather than removing
                    if prev.state == CandidateState.HELD:
                        self._state.candidates[ticker] = prev.model_copy(
                            update={"state": CandidateState.EXITED, "final_label": label},
                        )
                    else:
                        del self._state.candidates[ticker]

        if scan_date not in self._state.scan_history:
            self._state.scan_history.append(scan_date)
            self._state.scan_history.sort()

        self._state.last_updated = datetime.now(tz=timezone.utc)
        logger.info(
            "Ingested %d results for %s: %d candidates in pool",
            len(results), scan_date, len(self._state.candidates),
        )

    # ------------------------------------------------------------------
    # Query candidates
    # ------------------------------------------------------------------

    def get_actionable_candidates(self) -> list[CandidateSnapshot]:
        """Return candidates eligible for cross-comparison.

        Includes snapshots with actionable labels in ANALYZED or COMPARED state.
        """
        return [
            c for c in self._state.candidates.values()
            if c.final_label in _ACTIONABLE_LABELS
            and c.state in (CandidateState.ANALYZED, CandidateState.COMPARED, CandidateState.HELD)
        ]

    def get_valuation_watchlist(
        self, exclude_tickers: set[str] | None = None,
    ) -> dict[str, dict]:
        """Return WATCHLIST+ candidates with valid trigger ratios for monitoring.

        Excludes held positions (covered by price triggers) and any tickers
        in exclude_tickers.
        """
        held = {h.ticker for h in self._state.holdings}
        excluded = held | (exclude_tickers or set())
        result = {}
        for ticker, c in self._state.candidates.items():
            if ticker in excluded:
                continue
            if (c.final_label in _ACTIONABLE_LABELS
                    and c.valuation_trigger_ratio is not None):
                result[ticker] = {
                    "trigger_ratio": c.valuation_trigger_ratio,
                    "scan_close": c.scan_close_price,
                    "name": c.name,
                    "industry": c.industry,
                    "final_label": c.final_label,
                }
        return result

    def get_candidates_for_rescan(self, staleness_days: int = 180) -> list[dict]:
        """Return candidates needing re-analysis.

        Always includes current holdings. Also includes WATCHLIST/DEEP_DIVE/
        SPECIAL_SITUATION candidates whose analysis is older than staleness_days.
        Returns list[dict] with {ticker, name, industry} matching universe format.
        """
        today = date.today()
        held_tickers = {h.ticker for h in self._state.holdings}
        result = []

        for c in self._state.candidates.values():
            needs_rescan = False
            if c.ticker in held_tickers:
                needs_rescan = True
            elif c.final_label in ("WATCHLIST", "DEEP_DIVE", "SPECIAL_SITUATION"):
                days_old = (today - c.scan_date).days
                if days_old >= staleness_days:
                    needs_rescan = True

            if needs_rescan:
                result.append({
                    "ticker": c.ticker,
                    "name": c.name,
                    "industry": c.industry,
                })

        return result

    # ------------------------------------------------------------------
    # Portfolio state
    # ------------------------------------------------------------------

    def get_current_holdings(self) -> list[PortfolioHolding]:
        return list(self._state.holdings)

    def update_holdings(
        self, holdings: list[PortfolioHolding], scan_date: date | None = None,
    ) -> None:
        """Update portfolio state from DecisionPipeline output.

        Transitions candidate states: selected → HELD, dropped → EXITED.
        """
        new_tickers = {h.ticker for h in holdings}
        old_tickers = {h.ticker for h in self._state.holdings}

        # Mark newly held candidates
        for ticker in new_tickers:
            if ticker in self._state.candidates:
                c = self._state.candidates[ticker]
                self._state.candidates[ticker] = c.model_copy(
                    update={"state": CandidateState.HELD},
                )

        # Mark exited candidates
        for ticker in old_tickers - new_tickers:
            if ticker in self._state.candidates:
                c = self._state.candidates[ticker]
                if c.state == CandidateState.HELD:
                    self._state.candidates[ticker] = c.model_copy(
                        update={"state": CandidateState.EXITED},
                    )

        self._state.holdings = list(holdings)
        self._state.last_updated = datetime.now(tz=timezone.utc)

    def to_portfolio_decisions(self) -> dict[str, float]:
        """Export current holdings as {ticker: weight} for backtest consumption."""
        return {h.ticker: h.target_weight for h in self._state.holdings}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        self._state.last_updated = datetime.now(tz=timezone.utc)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                self._state.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("Saved CandidateStore to %s", self._path)
