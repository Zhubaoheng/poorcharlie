"""Filing Structuring Skill output schema."""

from __future__ import annotations

from investagent.schemas.common import BaseAgentOutput


class FilingOutput(BaseAgentOutput):
    income_statement_table: list[dict[str, float | str | None]]
    balance_sheet_table: list[dict[str, float | str | None]]
    cashflow_table: list[dict[str, float | str | None]]
    per_share_table: list[dict[str, float | str | None]]
    capital_allocation_table: list[dict[str, float | str | None]]
    accounting_policy_snippets: list[str]
    segment_table: list[dict[str, float | str | None]]
    footnote_flags: list[str]
