"""Investment Committee Agent — final verdict with deterministic post-processing.

LLM produces thesis/anti-thesis/unknowns/reasoning. Python post-processing
enforces hard label and confidence rules that LLM consistently fails to follow.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.common import BaseAgentOutput
from investagent.schemas.committee import CommitteeOutput, FinalLabel
from investagent.schemas.company import CompanyIntake

logger = logging.getLogger(__name__)

# Label priority for "at least X" upgrades
_LABEL_RANK = {
    FinalLabel.REJECT: 0,
    FinalLabel.TOO_HARD: 1,
    FinalLabel.WATCHLIST: 2,
    FinalLabel.DEEP_DIVE: 3,
    FinalLabel.SPECIAL_SITUATION: 4,
    FinalLabel.INVESTABLE: 5,
}


def _post_process_committee(
    output: CommitteeOutput,
    ctx: Any,
) -> CommitteeOutput:
    """Deterministic label and confidence correction.

    LLM consistently defaults to WATCHLIST regardless of upstream signals.
    These rules enforce what the prompt asks but LLM doesn't deliver.
    """
    # Extract upstream signals
    quality = ""
    mos = None
    hurdle = None
    pvv = ""
    kill_shots: list[str] = []

    try:
        fq = ctx.get_result("financial_quality")
        quality = getattr(fq, "enterprise_quality", "")
    except (KeyError, AttributeError):
        pass

    try:
        val = ctx.get_result("valuation")
        mos = getattr(val, "margin_of_safety_pct", None)
        hurdle = getattr(val, "meets_hurdle_rate", None)
        pvv = getattr(val, "price_vs_value", "")
    except (KeyError, AttributeError):
        pass

    try:
        critic = ctx.get_result("critic")
        kill_shots = getattr(critic, "kill_shots", []) or []
    except (KeyError, AttributeError):
        pass

    label = output.final_label
    confidence = output.confidence
    original_label = label

    # --- Hard REJECT only (防呆) ---
    # These are objective facts that don't require judgment.
    # Upgrade decisions (WATCHLIST→DEEP_DIVE→INVESTABLE) stay with LLM
    # because they require weighing critic analysis, unknowns, and context.
    if quality == "POOR":
        label = FinalLabel.REJECT
    elif mos is not None and mos < -50 and quality in ("AVERAGE", "POOR", ""):
        label = FinalLabel.REJECT
    elif quality == "AVERAGE" and hurdle is False:
        label = FinalLabel.REJECT

    if label != original_label:
        logger.info(
            "Committee post-process: %s → %s (quality=%s pvv=%s mos=%s hurdle=%s)",
            original_label.value, label.value, quality, pvv, mos, hurdle,
        )

    if label != output.final_label or confidence != output.confidence:
        return output.model_copy(update={
            "final_label": label,
            "confidence": confidence,
        })
    return output


class CommitteeAgent(BaseAgent):
    name: str = "committee"

    def _output_type(self) -> type[BaseAgentOutput]:
        return CommitteeOutput

    def _agent_role_description(self) -> str:
        return (
            "You are the Investment Committee Agent — the final synthesis layer "
            "that renders a verdict on the investment case. You do NOT re-analyze "
            "raw data. You consume only the structured outputs from all prior "
            "agents in the pipeline and synthesize them into a single actionable "
            "conclusion. You weigh the bull case against the bear case, identify "
            "the largest remaining unknowns, and assign one of six labels: "
            "REJECT, TOO_HARD, WATCHLIST, DEEP_DIVE, SPECIAL_SITUATION, or "
            "INVESTABLE. Your output must include a clear thesis, anti-thesis, "
            "expected return summary, timing rationale, and next action."
        )

    def _build_user_context(self, input_data: BaseModel, ctx: Any = None) -> dict[str, Any]:
        assert isinstance(input_data, CompanyIntake)
        self._pipeline_ctx = ctx  # Store for post-processing
        result: dict[str, Any] = {
            "ticker": input_data.ticker,
            "name": input_data.name,
            "exchange": input_data.exchange,
        }
        if ctx is not None:
            from investagent.agents.context_helpers import data_for_committee, format_json
            upstream = data_for_committee(ctx)
            result["has_filing_data"] = bool(upstream)
            result["upstream_json"] = format_json(upstream)
        else:
            result["has_filing_data"] = False
            result["upstream_json"] = ""
        return result

    async def run(
        self, input_data: BaseModel, ctx: Any = None, *, max_retries: int = 2,
    ) -> CommitteeOutput:
        """Run LLM committee, then deterministic post-processing."""
        output: CommitteeOutput = await super().run(input_data, ctx, max_retries=max_retries)  # type: ignore[assignment]
        pipeline_ctx = getattr(self, "_pipeline_ctx", None)
        if pipeline_ctx is not None:
            return _post_process_committee(output, pipeline_ctx)
        return output
