"""Provider registry.

To add a provider: write an adapter module next to this file (subclass
ProviderAdapter), add one line to PROVIDERS, and list its models in models.yaml.
Modules are imported lazily, so a missing SDK only breaks its own provider.
"""

from __future__ import annotations

import importlib
from functools import lru_cache
from pathlib import Path

import yaml

from .base import ProviderAdapter, ProviderInfo

PROVIDERS: dict[str, str] = {
    "anthropic": "testbed.providers.anthropic:AnthropicAdapter",
    "openai": "testbed.providers.openai:OpenAIAdapter",
    "gemini": "testbed.providers.gemini:GeminiAdapter",
}

# Registered at runtime only (tests); never listed in the UI.
_EXTRA: dict[str, type[ProviderAdapter]] = {}


def register(name: str, adapter: type[ProviderAdapter]) -> None:
    _EXTRA[name] = adapter


def unregister(name: str) -> None:
    _EXTRA.pop(name, None)


def provider_names() -> list[str]:
    return list(PROVIDERS)


def adapter_class(name: str) -> type[ProviderAdapter]:
    if name in _EXTRA:
        return _EXTRA[name]
    if name not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise KeyError(f"unknown provider {name!r} (known: {known})")
    module_name, class_name = PROVIDERS[name].split(":")
    return getattr(importlib.import_module(module_name), class_name)


def is_known(name: str) -> bool:
    return name in PROVIDERS or name in _EXTRA


@lru_cache(maxsize=1)
def _model_lists() -> dict[str, dict]:
    path = Path(__file__).with_name("models.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def provider_info(name: str) -> ProviderInfo:
    cls = adapter_class(name)
    return ProviderInfo(
        name=cls.name, label=cls.label, env_key=cls.env_key, key_url=cls.key_url,
        sdk=cls.sdk, models=list(_model_lists().get(name, {}).get("models", [])),
        default_model=_model_lists().get(name, {}).get("default"),
    )


def env_key(name: str) -> str:
    return adapter_class(name).env_key
