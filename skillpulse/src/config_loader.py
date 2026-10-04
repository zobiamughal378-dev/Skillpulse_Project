"""Load and validate configuration (config/config.yaml) and secrets (.env)."""

import os
from pathlib import Path
from typing import Any, Optional, Union

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from src.utils import PROJECT_ROOT

DEFAULT_CONFIG_PATH: Path = PROJECT_ROOT / "config" / "config.yaml"


class AppSection(BaseModel):
    """General application metadata."""

    name: str = "SkillPulse"
    version: str = "1.0.0"


class PathsConfig(BaseModel):
    """Locations of output files (relative to the project root)."""

    csv: str = "data/output.csv"
    database: str = "data/database.db"
    log_file: str = "logs/scraper.log"


class LoggingConfig(BaseModel):
    """Logging options."""

    level: str = "INFO"


class HttpConfig(BaseModel):
    """HTTP behaviour: timeouts, retries, politeness and anti-bot settings."""

    timeout_seconds: float = 20.0
    max_retries: int = 4
    backoff_min_seconds: float = 2.0
    backoff_max_seconds: float = 30.0
    delay_seconds: float = 1.5
    jitter_seconds: float = 0.5
    respect_robots_txt: bool = True
    fallback_user_agents: list[str] = Field(
        default_factory=lambda: [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ]
    )


class SourcesConfig(BaseModel):
    """Which sources run, and the per-source settings."""

    active: list[str] = Field(default_factory=lambda: ["fake_jobs"])
    fake_jobs: dict[str, Any] = Field(default_factory=dict)
    hn_hiring: dict[str, Any] = Field(default_factory=dict)


class ScheduleConfig(BaseModel):
    """Scheduler settings."""

    interval_hours: float = 6
    run_on_start: bool = True
    timezone: str = "UTC"


class NLPConfig(BaseModel):
    """NLP extraction settings."""

    use_spacy: bool = True
    spacy_model: str = "en_core_web_sm"
    fuzzy_enabled: bool = True
    fuzzy_threshold: int = 90
    max_description_chars: int = 1500


class AlertsConfig(BaseModel):
    """Alert (email / Telegram) settings."""

    enabled: bool = True
    channels: list[str] = Field(default_factory=lambda: ["email"])
    keywords: list[str] = Field(default_factory=list)
    max_items_in_digest: int = 25
    min_new_records: int = 1


class DashboardConfig(BaseModel):
    """Dashboard settings."""

    title: str = "SkillPulse"


class AppConfig(BaseModel):
    """Top-level validated configuration object."""

    app: AppSection = Field(default_factory=AppSection)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    nlp: NLPConfig = Field(default_factory=NLPConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    skills: dict[str, list[str]] = Field(default_factory=dict)


def load_env(env_path: Optional[Union[str, Path]] = None) -> None:
    """Load variables from a .env file (if present) into the process environment."""
    load_dotenv(env_path or PROJECT_ROOT / ".env", override=False)


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read an environment variable, treating empty strings as missing."""
    value = os.getenv(name)
    return value if value else default


def load_config(path: Optional[Union[str, Path]] = None) -> AppConfig:
    """Read the YAML configuration, validate it and return an :class:`AppConfig`.

    Raises:
        FileNotFoundError: if the configuration file does not exist.
        ValueError: if the YAML is not a mapping.
    """
    load_env()
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{config_path} must contain a YAML mapping at the top level")
    return AppConfig.model_validate(data)
