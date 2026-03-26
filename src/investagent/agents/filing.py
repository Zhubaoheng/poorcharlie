"""Filing Structuring Skill — standardize financials into structured tables.

This is a *hybrid* agent: it first downloads and extracts text from real
financial filings (PDF or HTML), then calls the LLM to produce structured
output (FilingOutput) grounded in the actual filing content.

For US ADR, XBRL data can auto-populate the three financial statements.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from investagent.agents.base import (
    AgentOutputError,
    BaseAgent,
    _coerce_lists_to_strings,
    _repair_json_strings,
)
from investagent.datasources.base import FilingDocument, FilingFetcher
from investagent.datasources.pdf_extract import extract_pdf_markdown, extract_sections
from investagent.datasources.resolver import resolve_filing_fetcher, resolve_market
from investagent.llm import LLMClient
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.filing import FilingOutput

logger = logging.getLogger(__name__)


class FilingAgent(BaseAgent):
    name: str = "filing"

    def __init__(
        self,
        llm: LLMClient,
        filing_fetcher: FilingFetcher | None = None,
    ) -> None:
        super().__init__(llm)
        self._filing_fetcher = filing_fetcher

    def _output_type(self) -> type[BaseAgentOutput]:
        return FilingOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Filing Structuring Skill. Your role is to transform "
            "raw financial filing excerpts into a standardized, structured data "
            "layer. You are given actual text extracted from financial reports. "
            "You extract the three financial statements (income, balance sheet, "
            "cash flow), segment data, accounting policies with raw text, debt "
            "schedules, special items, concentration data, capital allocation "
            "records, footnote extracts, and risk factors. You must preserve "
            "critical raw text for accounting policies, footnotes, and risk factors. "
            "Only extract data that appears in the provided text — do NOT invent "
            "or hallucinate numbers."
        )

    def _build_user_context(
        self, input_data: BaseModel, ctx: Any = None,
    ) -> dict[str, Any]:
        assert isinstance(input_data, CompanyIntake)
        result: dict[str, Any] = {
            "ticker": input_data.ticker,
            "name": input_data.name,
            "exchange": input_data.exchange,
            "market": getattr(self, "_market", ""),
            "filing_sections": getattr(self, "_extracted_sections", {}),
            "num_filings": getattr(self, "_num_filings", 0),
            "xbrl_data": getattr(self, "_xbrl_data", None),
        }
        return result

    # ------------------------------------------------------------------
    # Content acquisition
    # ------------------------------------------------------------------

    async def _download_and_extract(
        self,
        filing_docs: list[FilingDocument],
        market: str,
    ) -> dict[str, str]:
        """Download filings and extract text sections.

        Returns a merged dict of section_key -> text across all filings.
        """
        fetcher = self._filing_fetcher
        if fetcher is None and filing_docs:
            # Resolve fetcher from the market field of the first filing
            market = filing_docs[0].market
            market_to_exchange = {"A_SHARE": "SSE", "HK": "HKEX", "US_ADR": "NYSE"}
            try:
                fetcher = resolve_filing_fetcher(
                    market_to_exchange.get(market, "")
                )
            except ValueError:
                fetcher = None

        all_sections: dict[str, str] = {}

        for doc in filing_docs:
            try:
                # Download if content not yet available
                if doc.raw_content is None and doc.text_content is None:
                    if fetcher is not None:
                        doc = await fetcher.download_filing(doc)
                    else:
                        logger.warning("No fetcher to download %s", doc.source_url)
                        continue

                # Extract text
                text = ""
                if doc.text_content:
                    text = doc.text_content
                elif doc.raw_content and doc.content_type == "pdf":
                    text = extract_pdf_markdown(doc.raw_content)
                elif doc.raw_content:
                    text = doc.raw_content.decode("utf-8", errors="replace")

                if not text:
                    logger.warning("No text extracted from %s", doc.source_url)
                    continue

                # Extract sections
                sections = extract_sections(text, market)
                for key, content in sections.items():
                    label = f"[{doc.filing_type} {doc.fiscal_year}]"
                    if key in all_sections:
                        all_sections[key] += f"\n\n---\n{label}\n{content}"
                    else:
                        all_sections[key] = f"{label}\n{content}"

                logger.info(
                    "Extracted %d sections from %s %s",
                    len(sections), doc.filing_type, doc.fiscal_year,
                )

            except Exception:
                logger.warning(
                    "Failed to process filing %s %s",
                    doc.filing_type, doc.fiscal_year, exc_info=True,
                )

        return all_sections

    # ------------------------------------------------------------------
    # Override run() — hybrid agent
    # ------------------------------------------------------------------

    async def run(
        self, input_data: BaseModel, ctx: Any = None,
    ) -> FilingOutput:
        assert isinstance(input_data, CompanyIntake)

        # Resolve market
        try:
            self._market = resolve_market(input_data.exchange)
        except ValueError:
            self._market = "HK"

        # Phase 1: Acquire and extract filing content
        filing_docs: list[FilingDocument] = []
        if ctx is not None:
            try:
                filing_docs = ctx.get_data("filing_documents")
            except KeyError:
                pass

        # Filter to annual reports only (most data-rich), limit to 3 most recent
        annual_docs = [
            d for d in filing_docs
            if d.fiscal_period == "FY" or "Annual" in d.filing_type or "年报" in d.filing_type
        ][:5]

        # If no annual reports, try all filings
        if not annual_docs:
            annual_docs = filing_docs[:3]

        self._extracted_sections = await self._download_and_extract(
            annual_docs, self._market,
        )
        self._num_filings = len(annual_docs)
        self._xbrl_data = None  # TODO: XBRL for US ADR

        # Phase 2: LLM call with retry
        system = self._render_system_prompt()
        user_prompt = self._render_user_prompt(input_data, ctx)
        tool_schema = self._prepare_tool_schema()
        max_retries = 2

        last_error: Exception | None = None
        for attempt in range(1 + max_retries):
            response = await self._llm.create_message(
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[tool_schema],
                max_tokens=16384,
            )

            tool_input = None
            for block in response.content:
                if block.type == "tool_use":
                    tool_input = block.input
                    break

            if tool_input is None:
                last_error = AgentOutputError(
                    f"{self.name}: no tool_use block "
                    f"(attempt {attempt + 1}/{1 + max_retries})"
                )
                continue

            # Repair LLM quirks
            tool_input = _repair_json_strings(tool_input)
            tool_input = _coerce_lists_to_strings(
                tool_input, FilingOutput.model_json_schema(),
            )

            # Inject server-managed meta
            meta = self._build_meta(self.name, response)
            tool_input["meta"] = meta.model_dump(mode="json")

            try:
                return FilingOutput.model_validate(tool_input)
            except Exception as exc:
                last_error = AgentOutputError(
                    f"{self.name}: failed to validate "
                    f"(attempt {attempt + 1}/{1 + max_retries}): {exc}"
                )
                continue

        raise last_error  # type: ignore[misc]
