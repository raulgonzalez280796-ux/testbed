"""Project settings (settings.yaml in the project folder): per-provider rate limits.

Set these to your account's limits (each provider's console shows them).
Limits apply per organization, so more machines do not raise them.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field



class ProviderLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requests_per_minute: float | None = Field(None, gt=0)
    input_tokens_per_minute: float | None = Field(None, gt=0)
    max_concurrent: int = Field(100, ge=1)  # requests in flight at once


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate_limits: dict[str, ProviderLimits] = {}

    def limits(self, provider: str) -> ProviderLimits:
        return self.rate_limits.get(provider, ProviderLimits())


def settings_path(home: Path) -> Path:
    return home / "settings.yaml"


def load_settings(home: Path) -> Settings:
    path = settings_path(home)
    if not path.exists():
        return Settings()
    return Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def save_settings(home: Path, settings: Settings) -> None:
    data = settings.model_dump(mode="json")
    settings_path(home).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
