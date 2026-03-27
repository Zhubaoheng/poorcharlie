"""Filing Structuring Skill — per-filing extraction with validation retry.

Processes one annual report at a time:
1. Download PDF → pymupdf4llm markdown → section extraction
2. LLM call to produce FilingOutput for that single report (2-3 years)
3. Validate critical fields; retry with hints if >30% null
4. Merge results across multiple reports (deduplicate by fiscal_year)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
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
from investagent.schemas.common import AgentMeta, BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.filing import FilingOutput

logger = logging.getLogger(__name__)

# Critical fields to validate after extraction
_CRITICAL_IS_FIELDS = ("revenue", "net_income")
_CRITICAL_BS_FIELDS = ("total_assets", "shareholders_equity")
_CRITICAL_CF_FIELDS = ("operating_cash_flow",)
_CRITICAL_EXTRA = ("shares_basic",)  # checked across all IS rows

_NULL_RATE_THRESHOLD = 0.30  # >30% null triggers retry
_TARGET_ANNUAL_REPORTS = 5  # 5 years of annual reports
_MAX_INTERIM = 1  # plus latest interim for most recent data


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
            "You are the Filing Structuring Skill. You extract structured "
            "financial data from a single annual report. You must extract ALL "
            "years of data visible in the report (current year + comparative "
            "periods). Only extract what appears in the provided text — "
            "do NOT invent numbers. Leave unknown fields as null."
        )

    def _build_user_context(
        self, input_data: BaseModel, ctx: Any = None,
    ) -> dict[str, Any]:
        assert isinstance(input_data, CompanyIntake)
        return {
            "ticker": input_data.ticker,
            "name": input_data.name,
            "exchange": input_data.exchange,
            "market": getattr(self, "_market", ""),
            "filing_sections": getattr(self, "_current_sections", {}),
            "source_filing_year": getattr(self, "_current_year", ""),
        }

    # ------------------------------------------------------------------
    # Per-filing processing
    # ------------------------------------------------------------------

    async def _download_one(self, doc: FilingDocument) -> dict[str, str]:
        """Download a single filing and extract sections."""
        fetcher = self._filing_fetcher
        if fetcher is None and doc.raw_content is None and doc.text_content is None:
            market_to_exchange = {"A_SHARE": "SSE", "HK": "HKEX", "US_ADR": "NYSE"}
            try:
                fetcher = resolve_filing_fetcher(
                    market_to_exchange.get(doc.market, "")
                )
            except ValueError:
                return {}

        try:
            if doc.raw_content is None and doc.text_content is None:
                if fetcher is not None:
                    doc = await fetcher.download_filing(doc)
                else:
                    return {}

            if doc.text_content:
                text = doc.text_content
            elif doc.raw_content and doc.content_type == "pdf":
                text = extract_pdf_markdown(doc.raw_content)
            elif doc.raw_content:
                text = doc.raw_content.decode("utf-8", errors="replace")
            else:
                return {}

            if not text:
                return {}

            return extract_sections(text, self._market)

        except Exception:
            logger.warning("Failed to process %s %s", doc.filing_type, doc.fiscal_year, exc_info=True)
            return {}

    async def _extract_from_single_filing(
        self,
        sections: dict[str, str],
        source_year: str,
        extra_instructions: str = "",
    ) -> FilingOutput | None:
        """Call LLM once to extract structured data from a single report."""
        self._current_sections = sections
        self._current_year = source_year

        system = self._render_system_prompt()
        user_prompt = self._render_user_prompt(
            CompanyIntake(
                ticker=self._intake.ticker,
                name=self._intake.name,
                exchange=self._intake.exchange,
            ),
        )

        if extra_instructions:
            user_prompt = extra_instructions + "\n\n" + user_prompt

        tool_schema = self._prepare_tool_schema()

        for attempt in range(3):  # max 3 retries for tool_use
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
                continue

            tool_input = _repair_json_strings(tool_input)
            tool_input = _coerce_lists_to_strings(
                tool_input, FilingOutput.model_json_schema(),
            )

            meta = self._build_meta(self.name, response)
            tool_input["meta"] = meta.model_dump(mode="json")

            try:
                return FilingOutput.model_validate(tool_input)
            except Exception as exc:
                logger.warning("Validation failed (attempt %d): %s", attempt + 1, exc)
                continue

        return None

    # ------------------------------------------------------------------
    # Validation + retry
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_extraction(output: FilingOutput) -> list[str]:
        """Check critical fields. Returns list of problem descriptions."""
        problems: list[str] = []
        total = 0
        nulls = 0

        for row in output.income_statement:
            for field in _CRITICAL_IS_FIELDS:
                total += 1
                if getattr(row, field, None) is None:
                    nulls += 1
                    problems.append(f"{field} is null for {row.fiscal_year}")

        for row in output.balance_sheet:
            for field in _CRITICAL_BS_FIELDS:
                total += 1
                if getattr(row, field, None) is None:
                    nulls += 1
                    problems.append(f"{field} is null for {row.fiscal_year}")

        for row in output.cash_flow:
            for field in _CRITICAL_CF_FIELDS:
                total += 1
                if getattr(row, field, None) is None:
                    nulls += 1
                    problems.append(f"{field} is null for {row.fiscal_year}")

        # Check shares across all IS rows
        if output.income_statement:
            has_shares = any(
                getattr(r, "shares_basic", None) is not None
                for r in output.income_statement
            )
            if not has_shares:
                problems.append("shares_basic is null for ALL years")

        if total == 0:
            return problems

        null_rate = nulls / total
        if null_rate > _NULL_RATE_THRESHOLD:
            return problems
        return []

    async def _retry_with_hints(
        self,
        sections: dict[str, str],
        source_year: str,
        problems: list[str],
    ) -> FilingOutput | None:
        """Retry extraction with specific hints about missing fields."""
        hints = "## 校验反馈（上次提取遗漏了以下关键字段，请重新查找原文）\n\n"
        for p in problems[:10]:
            hints += f"- {p}\n"
        hints += "\n请保留上次已正确提取的值，仅补充遗漏字段。\n"

        return await self._extract_from_single_filing(sections, source_year, hints)

    # ------------------------------------------------------------------
    # Merge multiple partial outputs
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_filing_outputs(outputs: list[FilingOutput]) -> FilingOutput:
        """Merge FilingOutputs from multiple reports.

        For quantitative rows (three statements, segments): deduplicate by
        fiscal_year, preferring data from the newer report.
        For qualitative content (accounting policies, footnotes, risk factors):
        keep ALL entries from every report — each year's narrative is unique.
        """
        if len(outputs) == 1:
            return outputs[0]

        # For quantitative: keep the row with the MOST non-null fields.
        # When a year appears in multiple reports, the one from its own
        # annual report is typically more complete than as a prior-year
        # comparative in a newer report.
        def _count_filled(row: Any) -> int:
            if hasattr(row, "model_dump"):
                d = row.model_dump()
            else:
                d = row if isinstance(row, dict) else {}
            return sum(1 for v in d.values() if v is not None)

        def _dedup_rows(all_rows: list, key_fn) -> list:
            seen: dict[str, Any] = {}
            for row in all_rows:
                k = key_fn(row)
                if k not in seen or _count_filled(row) > _count_filled(seen[k]):
                    seen[k] = row
            return list(seen.values())

        # Collect rows, newest report first
        all_is = [r for o in outputs for r in o.income_statement]
        all_bs = [r for o in outputs for r in o.balance_sheet]
        all_cf = [r for o in outputs for r in o.cash_flow]
        all_seg = [r for o in outputs for r in o.segments]
        all_buy = [r for o in outputs for r in o.buyback_history]
        all_acq = [r for o in outputs for r in o.acquisition_history]

        # Qualitative: keep ALL (each report's narrative is unique)
        all_ap = [p for o in outputs for p in o.accounting_policies]
        all_si = [s for o in outputs for s in o.special_items]
        all_fn = [f for o in outputs for f in o.footnote_extracts]
        all_rf = [r for o in outputs for r in o.risk_factors]
        all_div = [d for o in outputs for d in o.dividend_per_share_history]

        # Debt: collect from all reports (different years have different loans)
        all_debt = [d for o in outputs for d in o.debt_schedule]
        all_cov = [c for o in outputs for c in o.covenant_status]

        # Concentration: newest non-None
        concentration = next(
            (o.concentration for o in outputs if o.concentration is not None),
            None,
        )

        # Filing meta: union of years and types
        newest = outputs[0]
        all_years = sorted(set(
            y for o in outputs for y in o.filing_meta.fiscal_years_covered
        ))
        all_types = sorted(set(
            t for o in outputs for t in o.filing_meta.filing_types
        ))

        total_tokens = sum(o.meta.token_usage for o in outputs)
        merged_meta = AgentMeta(
            agent_name="filing",
            timestamp=newest.meta.timestamp,
            model_used=newest.meta.model_used,
            token_usage=total_tokens,
        )

        return FilingOutput(
            meta=merged_meta,
            filing_meta=newest.filing_meta.model_copy(update={
                "fiscal_years_covered": all_years,
                "filing_types": all_types,
            }),
            # Quantitative: dedup by year (newer report preferred)
            income_statement=_dedup_rows(all_is, lambda r: f"{r.fiscal_year}_{getattr(r, 'fiscal_period', 'FY')}"),
            balance_sheet=_dedup_rows(all_bs, lambda r: r.fiscal_year),
            cash_flow=_dedup_rows(all_cf, lambda r: r.fiscal_year),
            segments=_dedup_rows(all_seg, lambda r: f"{r.fiscal_year}_{r.segment_name}"),
            buyback_history=_dedup_rows(all_buy, lambda r: r.fiscal_year),
            acquisition_history=_dedup_rows(all_acq, lambda r: f"{r.fiscal_year}_{getattr(r, 'target', '')}"),
            # Qualitative: keep all (unique per report year)
            accounting_policies=all_ap,
            special_items=all_si,
            footnote_extracts=all_fn,
            risk_factors=all_rf,
            dividend_per_share_history=all_div,
            # Debt: keep all instruments
            debt_schedule=all_debt,
            covenant_status=all_cov,
            concentration=concentration,
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def _process_one_filing(self, doc: FilingDocument) -> FilingOutput | None:
        """Download, extract, validate, and optionally retry one filing."""
        logger.info("Processing %s %s", doc.filing_type, doc.fiscal_year)

        sections = await self._download_one(doc)
        if not sections:
            logger.warning("No sections extracted from %s %s", doc.filing_type, doc.fiscal_year)
            return None

        output = await self._extract_from_single_filing(sections, doc.fiscal_year)
        if output is None:
            logger.warning("LLM extraction failed for %s %s", doc.filing_type, doc.fiscal_year)
            return None

        # Validation + retry
        problems = self._validate_extraction(output)
        if problems:
            logger.info(
                "%s %s: %d critical nulls, retrying",
                doc.filing_type, doc.fiscal_year, len(problems),
            )
            retry_output = await self._retry_with_hints(sections, doc.fiscal_year, problems)
            if retry_output is not None:
                retry_problems = self._validate_extraction(retry_output)
                if len(retry_problems) < len(problems):
                    output = retry_output

        return output

    async def run(
        self, input_data: BaseModel, ctx: Any = None,
    ) -> FilingOutput:
        assert isinstance(input_data, CompanyIntake)
        self._intake = input_data

        try:
            self._market = resolve_market(input_data.exchange)
        except ValueError:
            self._market = "HK"

        # Get filing documents from context
        filing_docs: list[FilingDocument] = []
        if ctx is not None:
            try:
                filing_docs = ctx.get_data("filing_documents")
            except KeyError:
                pass

        # Split: annual reports + latest interim
        annuals = sorted(
            [
                d for d in filing_docs
                if d.fiscal_period == "FY"
                or "Annual" in d.filing_type
                or "年报" in d.filing_type
            ],
            key=lambda d: d.fiscal_year,
            reverse=True,
        )[:_TARGET_ANNUAL_REPORTS]

        interims = sorted(
            [
                d for d in filing_docs
                if d.fiscal_period == "H1"
                or "Interim" in d.filing_type
                or "半年" in d.filing_type
            ],
            key=lambda d: d.fiscal_year,
            reverse=True,
        )[:_MAX_INTERIM]

        docs_to_process = annuals + interims
        if not docs_to_process:
            docs_to_process = sorted(filing_docs, key=lambda d: d.fiscal_year, reverse=True)[:3]

        logger.info(
            "Processing %d filings in parallel (%d annual + %d interim)",
            len(docs_to_process), len(annuals), len(interims),
        )

        # Process ALL filings in parallel — each gets its own full LLM call
        import asyncio
        tasks = [self._process_one_filing(doc) for doc in docs_to_process]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        partial_outputs: list[FilingOutput] = []
        for i, result in enumerate(results):
            if isinstance(result, FilingOutput):
                partial_outputs.append(result)
            elif isinstance(result, Exception):
                logger.warning("Filing %s failed: %s", docs_to_process[i].fiscal_year, result)

        # Fallback: if nothing worked, one empty LLM call
        if not partial_outputs:
            self._current_sections = {}
            self._current_year = ""
            fallback = await self._extract_from_single_filing({}, "")
            if fallback is None:
                raise AgentOutputError(f"{self.name}: all extraction attempts failed")
            partial_outputs.append(fallback)

        # Merge — newest first (already sorted)
        result = self._merge_filing_outputs(partial_outputs)

        # Inject market_currency from info_capture
        if ctx is not None:
            try:
                info = ctx.get_result("info_capture")
                market_currency = info.market_snapshot.currency
                if market_currency:
                    result = result.model_copy(update={
                        "filing_meta": result.filing_meta.model_copy(update={
                            "market_currency": market_currency,
                        }),
                    })
            except (KeyError, AttributeError):
                pass

        return result
