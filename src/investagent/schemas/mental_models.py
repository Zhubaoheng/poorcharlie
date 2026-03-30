"""Mental Model council output schemas — 5 parallel agents + container."""

from __future__ import annotations

from pydantic import BaseModel

from investagent.schemas.common import BaseAgentOutput


class MoatOutput(BaseAgentOutput):
    moat_rating: str = ""  # WIDE / NARROW / NONE / ERODING
    industry_structure: str
    moat_type: list[str]
    pricing_power_position: str
    moat_trend: str


class CompoundingOutput(BaseAgentOutput):
    compounding_quality: str = ""  # STRONG / MODERATE / WEAK / NEGATIVE
    compounding_engine: str
    incremental_return_on_capital: str
    sustainability_period: str
    per_share_value_growth_logic: str


class PsychologyOutput(BaseAgentOutput):
    management_alignment: str = ""  # ALIGNED / NEUTRAL / MISALIGNED
    management_incentive_distortion: str
    market_sentiment_bias: str
    narrative_vs_fact_divergence: str
    self_bias_check: str = ""  # Check biases in OUR analysis: familiarity, confirmation, overconfidence, availability


class SystemsOutput(BaseAgentOutput):
    fragility_level: str = ""  # ROBUST / MODERATE / FRAGILE
    single_points_of_failure: list[str]
    fragility_sources: list[str]
    fault_tolerance: str
    system_resilience: str


class EcologyOutput(BaseAgentOutput):
    survival_rating: str = ""  # DOMINANT / COMPETITIVE / VULNERABLE / ENDANGERED
    ecological_niche: str
    adaptability_trend: str
    cyclical_vs_structural: str
    long_term_survival_probability: str


class MentalModelCouncilOutput(BaseModel, frozen=True):
    moat: MoatOutput
    compounding: CompoundingOutput
    psychology: PsychologyOutput
    systems: SystemsOutput
    ecology: EcologyOutput
