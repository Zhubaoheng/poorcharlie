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
_UNIT_SCALE_THRESHOLD = 50  # if a value is 50x smaller than median, likely unit error


def _fix_unit_scale(rows: list[Any], field: str) -> list[Any]:
    """Fix cross-year unit inconsistencies.

    If some rows' `field` value is 50x+ smaller than the median,
    multiply them by the nearest power of 1000 to align.
    This catches LLM extracting "868,687" (millions) as 868687
    instead of 868,687,000,000.
    """
    values = [
        (i, getattr(r, field, None))
        for i, r in enumerate(rows)
        if getattr(r, field, None) is not None and getattr(r, field) > 0
    ]
    if len(values) < 2:
        return rows  # not enough data to detect anomalies

    nums = sorted(v for _, v in values)
    # Use the MAX as reference — if any row is 50x smaller, it's likely a unit error.
    # Max is more robust than median when majority of rows have the wrong unit.
    reference = nums[-1]

    if reference == 0:
        return rows

    fixed = list(rows)
    for idx, val in values:
        ratio = reference / val
        if ratio > _UNIT_SCALE_THRESHOLD:
            # Find the right multiplier: 1000, 1000000, etc.
            multiplier = 1
            while val * multiplier * 10 < reference:
                multiplier *= 10
            # Round to nearest power of 10
            if multiplier >= 10:
                new_val = val * multiplier
                # Apply to ALL numeric fields in this row, not just the anchor
                row = fixed[idx]
                updates: dict[str, Any] = {}
                for f in type(row).model_fields:
                    v = getattr(row, f, None)
                    if isinstance(v, (int, float)) and v != 0 and f != "fiscal_year":
                        updates[f] = v * multiplier
                fixed[idx] = row.model_copy(update=updates)
                logger.info(
                    "Unit fix: %s row %s scaled by %dx (%s: %s → %s)",
                    field, getattr(row, "fiscal_year", "?"),
                    multiplier, field, val, new_val,
                )

    return fixed


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
    # AkShare structured data override
    # ------------------------------------------------------------------

    async def _override_with_structured_data(
        self, output: FilingOutput, intake: CompanyIntake,
    ) -> FilingOutput:
        """Override LLM-extracted numbers with AkShare API data (zero hallucination).

        AkShare provides deterministic financial statement data for A-shares
        and HK stocks. When available, these values replace LLM extraction.
        """
        from investagent.datasources.akshare_source import fetch_structured_financials
        from investagent.schemas.filing import (
            BalanceSheetRow,
            CashFlowRow,
            IncomeStatementRow,
        )

        try:
            data = await fetch_structured_financials(intake.ticker, self._market)
        except Exception:
            logger.warning("AkShare fetch failed, keeping LLM data", exc_info=True)
            return output

        if not data or not any(data.get(k) for k in ("income_statement", "balance_sheet", "cash_flow")):
            return output

        logger.info(
            "AkShare override: IS=%d BS=%d CF=%d rows",
            len(data.get("income_statement", [])),
            len(data.get("balance_sheet", [])),
            len(data.get("cash_flow", [])),
        )

        ak_is = [IncomeStatementRow(**row) for row in data.get("income_statement", [])]
        ak_bs = [BalanceSheetRow(**row) for row in data.get("balance_sheet", [])]
        ak_cf = [CashFlowRow(**row) for row in data.get("cash_flow", [])]

        # AkShare overrides LLM for same fiscal_year
        def _merge_rows(ak_rows: list, llm_rows: list, key_fn) -> list:
            by_key: dict = {}
            for row in llm_rows:
                by_key[key_fn(row)] = row
            for row in ak_rows:
                by_key[key_fn(row)] = row  # override
            return list(by_key.values())

        new_is = _merge_rows(ak_is, list(output.income_statement),
                             lambda r: f"{r.fiscal_year}_{getattr(r, 'fiscal_period', 'FY')}")
        new_bs = _merge_rows(ak_bs, list(output.balance_sheet), lambda r: r.fiscal_year)
        new_cf = _merge_rows(ak_cf, list(output.cash_flow), lambda r: r.fiscal_year)

        all_years = sorted(set(r.fiscal_year for r in new_is) | set(r.fiscal_year for r in new_bs))

        return output.model_copy(update={
            "income_statement": new_is,
            "balance_sheet": new_bs,
            "cash_flow": new_cf,
            "filing_meta": output.filing_meta.model_copy(update={
                "fiscal_years_covered": all_years,
            }),
        })

    # ------------------------------------------------------------------
    # Post-processing: compute derived fields
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_fiscal_keys(output: FilingOutput) -> FilingOutput:
        """Normalize fiscal_year and fiscal_period across all rows.

        Fixes: "Y2023"→"2023", "FY2024"→"2024", "FY2023"→"FY" period.
        Then re-deduplicates rows that became identical after normalization.
        """
        import re as _re

        def _clean_year(fy: str) -> str:
            """Extract 4-digit year from messy fiscal_year strings."""
            m = _re.search(r"(\d{4})", fy)
            return m.group(1) if m else fy

        def _clean_period(fp: str | None) -> str:
            fp = fp or "FY"
            for prefix in ("FY", "H1", "H2", "Q1", "Q2", "Q3", "Q4"):
                if fp.upper().startswith(prefix):
                    return prefix
            return fp

        def _fix_rows(rows: list, has_period: bool = False) -> list:
            fixed = []
            for row in rows:
                updates: dict[str, Any] = {}
                clean_fy = _clean_year(row.fiscal_year)
                if clean_fy != row.fiscal_year:
                    updates["fiscal_year"] = clean_fy
                if has_period:
                    clean_fp = _clean_period(getattr(row, "fiscal_period", "FY"))
                    if clean_fp != getattr(row, "fiscal_period", None):
                        updates["fiscal_period"] = clean_fp
                fixed.append(row.model_copy(update=updates) if updates else row)
            return fixed

        # Normalize all row types
        new_is = _fix_rows(list(output.income_statement), has_period=True)
        new_bs = _fix_rows(list(output.balance_sheet))
        new_cf = _fix_rows(list(output.cash_flow))
        new_seg = _fix_rows(list(output.segments))

        # Re-deduplicate after normalization (keeps most-filled row)
        def _count_filled(row: Any) -> int:
            d = row.model_dump() if hasattr(row, "model_dump") else {}
            return sum(1 for v in d.values() if v is not None)

        def _dedup(rows: list, key_fn) -> list:
            seen: dict[str, Any] = {}
            for row in rows:
                k = key_fn(row)
                if k not in seen or _count_filled(row) > _count_filled(seen[k]):
                    seen[k] = row
            return list(seen.values())

        new_is = _dedup(new_is, lambda r: f"{r.fiscal_year}_{getattr(r, 'fiscal_period', 'FY')}")
        new_bs = _dedup(new_bs, lambda r: r.fiscal_year)
        new_cf = _dedup(new_cf, lambda r: r.fiscal_year)
        new_seg = _dedup(new_seg, lambda r: f"{r.fiscal_year}_{r.segment_name}")

        # Normalize fiscal_years_covered in meta
        all_years = sorted(set(r.fiscal_year for r in new_is) | set(r.fiscal_year for r in new_bs))

        return output.model_copy(update={
            "income_statement": new_is,
            "balance_sheet": new_bs,
            "cash_flow": new_cf,
            "segments": new_seg,
            "filing_meta": output.filing_meta.model_copy(update={
                "fiscal_years_covered": all_years,
            }),
        })

    @staticmethod
    def _compute_derived_fields(output: FilingOutput) -> FilingOutput:
        """Fill computable fields and fix unit errors. Pure arithmetic, no LLM.

        Derives: gross_profit, cost_of_revenue (from operating data),
                 eps, shares, free_cash_flow, depreciation estimate.
        Fixes: shares unit sanity check.
        """
        new_is: list[Any] = []
        for row in output.income_statement:
            updates: dict[str, Any] = {}

            rev = row.revenue
            cor = row.cost_of_revenue
            ni = row.net_income
            ni_parent = row.net_income_to_parent
            eps_b = row.eps_basic
            eps_d = row.eps_diluted
            sh_b = row.shares_basic
            sh_d = row.shares_diluted
            oi = row.operating_income
            gp = row.gross_profit

            # gross_profit: multiple fallback paths
            if gp is None:
                if rev is not None and cor is not None:
                    # Standard: gross_profit = revenue - cost_of_revenue
                    updates["gross_profit"] = rev - abs(cor)
                elif rev is not None and oi is not None and cor is None:
                    # IFRS "by nature" fallback: no COGS line exists.
                    # Approximate: gross_profit ≈ revenue - (revenue - operating_income)
                    # This equals operating_income + SGA + other OpEx, which overstates
                    # true gross profit. But it's better than null for margin analysis.
                    # We DON'T set cost_of_revenue (it genuinely doesn't exist).
                    pass  # Leave gross_profit null if no COGS — don't fake it

            # depreciation: if null in IS but we can estimate from balance sheet PPE changes
            if row.depreciation_amortization is None:
                # Will be handled in cross-row pass below
                pass

            # shares from eps: shares = net_income / eps
            if sh_b is None and eps_b is not None and eps_b != 0:
                src = ni_parent if ni_parent is not None else ni
                if src is not None:
                    updates["shares_basic"] = round(src / eps_b)
                    sh_b = updates["shares_basic"]

            if sh_d is None and eps_d is not None and eps_d != 0:
                src = ni_parent if ni_parent is not None else ni
                if src is not None:
                    updates["shares_diluted"] = round(src / eps_d)

            # Shares sanity check: if shares < 100M but net_income > 100M,
            # likely a unit error (thousands/万 vs actual shares)
            if sh_b is not None and ni is not None and sh_b < 1e8 and abs(ni) > 1e8:
                updates["shares_basic"] = sh_b * 100
                sh_b = updates["shares_basic"]
            if sh_d is not None and ni is not None and sh_d < 1e8 and abs(ni) > 1e8:
                updates["shares_diluted"] = sh_d * 100
                sh_d = updates["shares_diluted"]

            # eps from shares: eps = net_income / shares
            if eps_b is None and sh_b is not None and sh_b != 0:
                src = ni_parent if ni_parent is not None else ni
                if src is not None:
                    updates["eps_basic"] = round(src / sh_b, 4)

            if eps_d is None and sh_d is not None and sh_d != 0:
                src = ni_parent if ni_parent is not None else ni
                if src is not None:
                    updates["eps_diluted"] = round(src / sh_d, 4)

            new_is.append(row.model_copy(update=updates) if updates else row)

        new_cf: list[Any] = []
        for row in output.cash_flow:
            updates = {}
            if row.free_cash_flow is None and row.operating_cash_flow is not None and row.capex is not None:
                updates["free_cash_flow"] = row.operating_cash_flow - abs(row.capex)
            new_cf.append(row.model_copy(update=updates) if updates else row)

        # Estimate depreciation from PPE changes + capex
        # D&A ≈ capex - (PPE_end - PPE_start)
        bs_by_year = {r.fiscal_year: r for r in output.balance_sheet}
        cf_by_year = {r.fiscal_year: r for r in new_cf}
        is_by_year = {f"{r.fiscal_year}_{getattr(r, 'fiscal_period', 'FY')}": (i, r)
                      for i, r in enumerate(new_is)}

        for key, (idx, is_row) in is_by_year.items():
            if is_row.depreciation_amortization is not None:
                continue
            fy = is_row.fiscal_year
            prev_fy = str(int(fy) - 1) if fy.isdigit() else None
            if prev_fy and fy in bs_by_year and prev_fy in bs_by_year:
                ppe_end = getattr(bs_by_year[fy], "ppe_net", None)
                ppe_start = getattr(bs_by_year[prev_fy], "ppe_net", None)
                capex = getattr(cf_by_year.get(fy), "capex", None) if fy in cf_by_year else None
                if ppe_end is not None and ppe_start is not None and capex is not None:
                    # D&A ≈ capex - (PPE_end - PPE_start)
                    da_estimate = abs(capex) - (ppe_end - ppe_start)
                    if da_estimate > 0:
                        new_is[idx] = is_row.model_copy(update={
                            "depreciation_amortization": round(da_estimate),
                        })

        # Cross-year unit consistency check
        new_is = _fix_unit_scale(new_is, "revenue")
        new_bs = _fix_unit_scale(list(output.balance_sheet), "total_assets")
        new_cf = _fix_unit_scale(new_cf, "operating_cash_flow")

        changed = (
            new_is != list(output.income_statement)
            or new_cf != list(output.cash_flow)
            or new_bs != list(output.balance_sheet)
        )
        if changed:
            return output.model_copy(update={
                "income_statement": new_is,
                "balance_sheet": new_bs,
                "cash_flow": new_cf,
            })
        return output

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def _process_one_filing(
        self, doc: FilingDocument,
    ) -> tuple[FilingOutput | None, dict[str, str]]:
        """Download, extract, validate, and optionally retry one filing.

        Returns (FilingOutput, raw_sections) — raw_sections preserved verbatim
        for downstream agents (MD&A, remuneration, audit, etc. NOT processed
        through LLM structuring).
        """
        logger.info("Processing %s %s", doc.filing_type, doc.fiscal_year)

        sections = await self._download_one(doc)
        if not sections:
            logger.warning("No sections extracted from %s %s", doc.filing_type, doc.fiscal_year)
            return None, {}

        # Separate: sections that go to LLM for structuring vs raw preservation
        _LLM_SECTIONS = {
            "income_statement", "balance_sheet", "cash_flow", "changes_in_equity",
            "accounting_policies", "segments", "borrowings", "risk_factors",
            "related_party", "concentration", "revenue_recognition", "special_items",
        }
        llm_sections = {k: v for k, v in sections.items() if k in _LLM_SECTIONS}
        raw_sections = {k: v for k, v in sections.items() if k not in _LLM_SECTIONS}

        # Also keep a copy of LLM sections in raw for possible downstream use
        raw_sections.update(sections)

        output = await self._extract_from_single_filing(llm_sections, doc.fiscal_year)
        if output is None:
            logger.warning("LLM extraction failed for %s %s", doc.filing_type, doc.fiscal_year)
            return None, raw_sections

        # Validation + retry
        problems = self._validate_extraction(output)
        if problems:
            logger.info(
                "%s %s: %d critical nulls, retrying",
                doc.filing_type, doc.fiscal_year, len(problems),
            )
            retry_output = await self._retry_with_hints(llm_sections, doc.fiscal_year, problems)
            if retry_output is not None:
                retry_problems = self._validate_extraction(retry_output)
                if len(retry_problems) < len(problems):
                    output = retry_output

        return output, raw_sections

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
        all_mda: dict[str, str] = {}
        all_raw_sections: dict[str, dict[str, str]] = {}  # year_key → {section_key → text}
        for i, result in enumerate(results):
            if isinstance(result, tuple):
                output, raw_sections = result
                if isinstance(output, FilingOutput):
                    partial_outputs.append(output)
                fy = docs_to_process[i].fiscal_year
                ft = docs_to_process[i].filing_type
                year_key = f"{ft} {fy}"
                if raw_sections:
                    all_raw_sections[year_key] = raw_sections
                    if "mda" in raw_sections:
                        all_mda[year_key] = raw_sections["mda"]
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

        # Override with AkShare structured data (zero hallucination)
        result = await self._override_with_structured_data(result, input_data)

        # Post-processing: compute derived fields from available data
        result = self._normalize_fiscal_keys(result)
        result = self._compute_derived_fields(result)

        # Store raw sections in context for downstream agents
        if ctx is not None:
            if all_mda:
                ctx.set_data("mda_by_year", all_mda)
            if all_raw_sections:
                ctx.set_data("raw_sections_by_year", all_raw_sections)
            total = sum(sum(len(v) for v in secs.values()) for secs in all_raw_sections.values())
            logger.info("Stored raw sections: %d filings, %d chars total", len(all_raw_sections), total)

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
