"""Info Capture Agent — gather filings, market data, official sources.

This is a *hybrid* agent: it first runs real data fetchers to get
filing manifests and market snapshots, then calls the LLM to synthesize
a company profile, identify information sources, and flag gaps.

The fetcher results are injected into the LLM prompt so its reasoning
is grounded in real data, and are also written directly into the output
(filing_manifest and market_snapshot are NOT left for the LLM to invent).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.datasources.base import (
    FilingDocument,
    FilingFetcher,
    MarketDataFetcher,
    MarketQuote,
)
from investagent.datasources.resolver import (
    resolve_filing_fetcher,
    resolve_market_data_fetcher,
    to_yfinance_ticker,
)
from investagent.llm import LLMClient
from investagent.schemas.common import AgentMeta, BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.info_capture import (
    FilingRef,
    InfoCaptureOutput,
    MarketSnapshot,
)

logger = logging.getLogger(__name__)

# How many years of filings to look for
_DEFAULT_LOOKBACK_YEARS = 5


def _filing_to_ref(doc: FilingDocument) -> FilingRef:
    """Convert a datasource FilingDocument to a schema FilingRef."""
    return FilingRef(
        filing_type=doc.filing_type,
        fiscal_year=doc.fiscal_year,
        fiscal_period=doc.fiscal_period,
        filing_date=doc.filing_date.isoformat(),
        source_url=doc.source_url,
        content_type=doc.content_type,
    )


def _quote_to_snapshot(quote: MarketQuote) -> MarketSnapshot:
    """Convert a datasource MarketQuote to a schema MarketSnapshot."""
    return MarketSnapshot(
        price=quote.price,
        market_cap=quote.market_cap,
        enterprise_value=quote.enterprise_value,
        pe_ratio=quote.pe_ratio,
        pb_ratio=quote.pb_ratio,
        dividend_yield=quote.dividend_yield,
        currency=quote.currency,
    )


class InfoCaptureAgent(BaseAgent):
    name: str = "info_capture"

    def __init__(
        self,
        llm: LLMClient,
        filing_fetcher: FilingFetcher | None = None,
        market_fetcher: MarketDataFetcher | None = None,
    ) -> None:
        super().__init__(llm)
        self._filing_fetcher = filing_fetcher
        self._market_fetcher = market_fetcher

    def _output_type(self) -> type[BaseAgentOutput]:
        return InfoCaptureOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Info Capture Agent. Your role is to build a complete, "
            "reusable research package for a company entering the analysis pipeline. "
            "You are given pre-fetched filing manifests and market data. "
            "Your job is to synthesize a company profile, list official and "
            "third-party sources, and flag any missing items. "
            "Do NOT invent filing URLs or market data — use what is provided."
        )

    def _build_user_context(
        self, input_data: BaseModel, ctx: Any = None,
    ) -> dict[str, Any]:
        assert isinstance(input_data, CompanyIntake)
        # Base context — always present
        context: dict[str, Any] = {
            "ticker": input_data.ticker,
            "name": input_data.name,
            "exchange": input_data.exchange,
            "sector": input_data.sector,
            "notes": input_data.notes,
        }

        # Injected by run() after fetching
        context["filing_refs"] = getattr(self, "_fetched_filings", [])
        context["market_snapshot"] = getattr(self, "_fetched_snapshot", None)
        return context

    # ------------------------------------------------------------------
    # Data gathering
    # ------------------------------------------------------------------

    async def _fetch_filings(self, intake: CompanyIntake) -> list[FilingDocument]:
        """Fetch filing metadata from the appropriate data source."""
        fetcher = self._filing_fetcher
        if fetcher is None:
            try:
                fetcher = resolve_filing_fetcher(intake.exchange)
            except ValueError:
                logger.warning("No fetcher for exchange %s", intake.exchange)
                return []

        now = datetime.now()
        start_year = now.year - _DEFAULT_LOOKBACK_YEARS
        end_year = now.year

        try:
            docs = await fetcher.search_filings(
                ticker=intake.ticker,
                start_year=start_year,
                end_year=end_year,
            )
            logger.info(
                "Fetched %d filings for %s from %s",
                len(docs), intake.ticker, fetcher.market,
            )
            return docs
        except Exception:
            logger.warning(
                "Failed to fetch filings for %s", intake.ticker, exc_info=True,
            )
            return []

    async def _fetch_market_data(self, intake: CompanyIntake) -> MarketQuote | None:
        """Fetch current market data snapshot."""
        fetcher = self._market_fetcher
        if fetcher is None:
            fetcher = resolve_market_data_fetcher()

        yf_ticker = to_yfinance_ticker(intake.ticker, intake.exchange)

        try:
            quote = await fetcher.get_quote(yf_ticker)
            logger.info(
                "Market data for %s: price=%s, mcap=%s",
                yf_ticker, quote.price, quote.market_cap,
            )
            return quote
        except Exception:
            logger.warning(
                "Failed to fetch market data for %s", yf_ticker, exc_info=True,
            )
            return None

    # ------------------------------------------------------------------
    # Override run() to inject real data
    # ------------------------------------------------------------------

    async def run(
        self, input_data: BaseModel, ctx: Any = None,
    ) -> InfoCaptureOutput:
        assert isinstance(input_data, CompanyIntake)

        # Phase 1: Gather real data from APIs
        filing_docs, quote = await self._fetch_filings(input_data), None
        quote = await self._fetch_market_data(input_data)

        # Convert to schema types
        filing_refs = [_filing_to_ref(d) for d in filing_docs]
        snapshot = _quote_to_snapshot(quote) if quote else MarketSnapshot()

        # Stash for _build_user_context
        self._fetched_filings = filing_refs
        self._fetched_snapshot = snapshot

        # Phase 2: Call LLM for company profile + source identification
        system = self._render_system_prompt()
        user_prompt = self._render_user_prompt(input_data, ctx)
        tool_schema = self._prepare_tool_schema()

        response = await self._llm.create_message(
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[tool_schema],
        )

        # Extract tool_use block
        tool_input = None
        for block in response.content:
            if block.type == "tool_use":
                tool_input = block.input
                break

        if tool_input is None:
            from investagent.agents.base import AgentOutputError

            raise AgentOutputError(
                f"{self.name}: no tool_use block in LLM response"
            )

        # Repair JSON strings returned by some providers
        from investagent.agents.base import _repair_json_strings

        tool_input = _repair_json_strings(tool_input)

        # Phase 3: Override LLM's filing_manifest and market_snapshot
        # with real fetcher data (ground truth)
        tool_input["filing_manifest"] = [r.model_dump() for r in filing_refs]
        tool_input["market_snapshot"] = snapshot.model_dump()

        # Inject server-managed meta
        meta = self._build_meta(self.name, response)
        tool_input["meta"] = meta.model_dump(mode="json")

        try:
            output = InfoCaptureOutput.model_validate(tool_input)
        except Exception as exc:
            from investagent.agents.base import AgentOutputError

            raise AgentOutputError(
                f"{self.name}: failed to validate output: {exc}"
            ) from exc

        # Store raw FilingDocuments in context for downstream agents
        if ctx is not None and filing_docs:
            ctx.set_data("filing_documents", filing_docs)

        return output
