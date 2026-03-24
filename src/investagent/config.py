"""Settings: model names, hurdle rates, thresholds."""


class Settings:
    """Placeholder for project-wide configuration."""

    model_name: str = "claude-sonnet-4-20250514"
    hurdle_rate: float = 0.10
    net_cash_watch_threshold: float = 0.5
    net_cash_priority_threshold: float = 1.0
    net_cash_high_priority_threshold: float = 1.5
