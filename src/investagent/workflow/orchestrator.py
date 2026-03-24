"""Main pipeline orchestrator: intake -> committee verdict."""

from investagent.schemas.company import CompanyIntake
from investagent.workflow.context import PipelineContext


async def run_pipeline(intake: CompanyIntake) -> PipelineContext:
    """Run the full 10-stage analysis pipeline.

    Stages:
    1. Triage -> gate check
    2. Info Capture
    3. Filing Structuring
    4. Accounting Risk -> gate check
    5. Financial Quality -> gate check
    6. Net Cash
    7. Valuation
    8. Mental Models (parallel)
    9. Critic
    10. Investment Committee
    """
    raise NotImplementedError
