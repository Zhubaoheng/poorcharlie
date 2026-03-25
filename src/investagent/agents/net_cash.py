"""Net Cash & Capital Return Agent — net cash / market cap analysis."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.net_cash import NetCashOutput


class NetCashAgent(BaseAgent):
    name: str = "net_cash"

    def _output_type(self) -> type[BaseAgentOutput]:
        return NetCashOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Net Cash & Capital Return Agent. Your role is to "
            "calculate a company's net cash position (cash + short-term "
            "investments minus interest-bearing debt), assess the net cash / "
            "market cap ratio, and evaluate capital return quality. "
            "You evaluate dividend sustainability, buyback effectiveness, "
            "and cash quality (restricted, trapped, or encumbered cash). "
            "Thresholds: net_cash / market_cap > 0.5x → WATCH, > 1.0x → "
            "PRIORITY, > 1.5x → HIGH_PRIORITY, otherwise NORMAL."
        )

    def _build_user_context(self, input_data: BaseModel, ctx: Any = None) -> dict[str, Any]:
        assert isinstance(input_data, CompanyIntake)
        result: dict[str, Any] = {
            "ticker": input_data.ticker,
            "name": input_data.name,
            "exchange": input_data.exchange,
        }
        if ctx is not None:
            try:
                filing = ctx.get_result("filing")  # noqa: F841
                result["has_filing_data"] = True
            except KeyError:
                result["has_filing_data"] = False
        else:
            result["has_filing_data"] = False
        return result
