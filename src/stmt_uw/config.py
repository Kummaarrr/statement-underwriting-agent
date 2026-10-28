"""Paths and settings: secrets from the environment / .env, nothing else machine-specific."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


def project_path(relative: str) -> Path:
    return PROJECT_ROOT / relative


class Env(BaseSettings):
    """Values read from environment variables / .env (the shell wins)."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    anthropic_api_key: str = ""
    max_run_cost_usd: float = 5.0
