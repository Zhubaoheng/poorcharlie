"""Valuation & Look-through Return Agent — bear/base/bull expected returns."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.company import CompanyIntake
from investagent.schemas.valuation import ValuationOutput


class ValuationAgent(BaseAgent):
    name: str = "valuation"

    def _output_type(self) -> type[BaseAgentOutput]:
        return ValuationOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Valuation & Look-through Return Agent. Your role is to "
            "estimate the expected look-through return of a company under three "
            "scenarios: bear, base, and bull. You calculate normalized earnings "
            "yield and owner earnings / FCF yield, project per-share intrinsic "
            "value growth based on ROIC reinvestment, and subtract friction "
            "(tax, transaction costs) to produce friction-adjusted returns. "
            "You then compare the base-case return against a hurdle rate "
            "(2× risk-free rate for the reporting currency)."
        )

    def _build_user_context(self, input_data: BaseModel, ctx: Any = None) -> dict[str, Any]:
        assert isinstance(input_data, CompanyIntake)
        from investagent.config import Settings

        # Determine currency and hurdle rate
        currency = "USD"
        if ctx is not None:
            try:
                filing = ctx.get_result("filing")
                if hasattr(filing, "filing_meta"):
                    currency = filing.filing_meta.currency or "USD"
            except KeyError:
                pass

        settings = Settings()
        hurdle = settings.get_hurdle_rate(currency)
        rfr = settings.risk_free_rates.get(currency, 0.04)

        result: dict[str, Any] = {
            "ticker": input_data.ticker,
            "name": input_data.name,
            "exchange": input_data.exchange,
            "hurdle_rate": hurdle,
            "hurdle_rate_pct": f"{hurdle * 100:.1f}%",
            "risk_free_rate": rfr,
            "risk_free_rate_pct": f"{rfr * 100:.1f}%",
            "currency": currency,
        }
        if ctx is not None:
            from investagent.agents.context_helpers import (
                _safe_get,
                format_filing_json,
                serialize_filing_for_prompt,
            )
            filing_data = serialize_filing_for_prompt(ctx)
            result["has_filing_data"] = filing_data.get("has_filing", False)
            result["filing_json"] = format_filing_json(filing_data)

            # Inject market snapshot (price, market_cap, PE, PB)
            info = _safe_get(ctx, "info_capture")
            if info is not None and hasattr(info, "market_snapshot"):
                ms = info.market_snapshot
                result["market_snapshot"] = {
                    "price": ms.price,
                    "market_cap": ms.market_cap,
                    "enterprise_value": ms.enterprise_value,
                    "pe_ratio": ms.pe_ratio,
                    "pb_ratio": ms.pb_ratio,
                    "dividend_yield": ms.dividend_yield,
                    "currency": ms.currency,
                }
        else:
            result["has_filing_data"] = False
            result["filing_json"] = ""
        return result
