"""Info Capture Agent — gather filings, market data, official sources."""

from pydantic import BaseModel

from investagent.agents.base import BaseAgent
from investagent.schemas.info_capture import InfoCaptureOutput


class InfoCaptureAgent(BaseAgent):
    name: str = "info_capture"

    async def run(self, input_data: BaseModel) -> InfoCaptureOutput:
        raise NotImplementedError
