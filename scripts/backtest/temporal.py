"""TemporalValidator — ensures no data from after the decision date enters the pipeline."""

from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)


class TemporalViolation:
    """Record of a temporal constraint violation."""

    def __init__(self, field: str, data_date: str, decision_date: str, detail: str = ""):
        self.field = field
        self.data_date = data_date
        self.decision_date = decision_date
        self.detail = detail

    def __repr__(self) -> str:
        return (
            f"TemporalViolation({self.field!r}, data={self.data_date}, "
            f"cutoff={self.decision_date}, {self.detail!r})"
        )


class TemporalValidator:
    """Validate that all data respects the decision-date cutoff.

    Any data with publish_date > decision_date is flagged and blocked.
    """

    def __init__(self, decision_date: date):
        self.decision_date = decision_date
        self.violations: list[TemporalViolation] = []

    def check_date(self, field: str, data_date: date | str, detail: str = "") -> bool:
        """Return True if the data date is on or before the decision date."""
        if isinstance(data_date, str):
            try:
                data_date = date.fromisoformat(data_date)
            except ValueError:
                # Can't parse — flag as violation to be safe
                v = TemporalViolation(field, str(data_date), str(self.decision_date), "unparseable date")
                self.violations.append(v)
                logger.warning("Temporal violation: %s", v)
                return False

        if data_date > self.decision_date:
            v = TemporalViolation(field, str(data_date), str(self.decision_date), detail)
            self.violations.append(v)
            logger.warning("Temporal violation: %s", v)
            return False
        return True

    def check_price_data(self, prices: dict[str, float], field: str = "price") -> dict[str, float]:
        """Filter a date-keyed price dict, removing entries after decision_date."""
        clean = {}
        for dt_str, val in prices.items():
            if self.check_date(field, dt_str):
                clean[dt_str] = val
        return clean

    @property
    def is_clean(self) -> bool:
        return len(self.violations) == 0

    def summary(self) -> str:
        if self.is_clean:
            return f"No violations (cutoff: {self.decision_date})"
        lines = [f"Found {len(self.violations)} violations (cutoff: {self.decision_date}):"]
        for v in self.violations:
            lines.append(f"  - {v.field}: {v.data_date} > {v.decision_date} ({v.detail})")
        return "\n".join(lines)
