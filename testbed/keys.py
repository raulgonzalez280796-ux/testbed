"""API keys: environment variables first, then the project's .env file.

Keys are only ever written to .env (mode 600). They never go into scenarios,
manifests, logs or exports, and error messages are redacted before logging.
Every key is optional: a run needs keys only for the providers it uses.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import dotenv_values

from . import providers

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),
]


def env_file(home: Path) -> Path:
    return home / ".env"


def load_keys(home: Path) -> dict[str, str]:
    """Provider name -> key, for every provider that has one."""
    file_values = dotenv_values(env_file(home)) if env_file(home).exists() else {}
    keys = {}
    for name in providers.provider_names():
        variable = providers.env_key(name)
        value = os.environ.get(variable) or file_values.get(variable) or ""
        if value.strip():
            keys[name] = value.strip()
    return keys


def key_source(home: Path, provider: str) -> str | None:
    variable = providers.env_key(provider)
    if os.environ.get(variable):
        return "environment"
    if env_file(home).exists() and dotenv_values(env_file(home)).get(variable):
        return ".env"
    return None


def masked(key: str) -> str:
    return f"…{key[-4:]}" if len(key) >= 8 else "set"


def save_key(home: Path, provider: str, value: str) -> None:
    variable = providers.env_key(provider)
    value = value.strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("Paste a single-line key.")
    path = env_file(home)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [line for line in lines if not line.split("=", 1)[0].strip() == variable]
    lines.append(f"{variable}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    ensure_gitignored(home)


def remove_key(home: Path, provider: str) -> None:
    variable = providers.env_key(provider)
    path = env_file(home)
    if not path.exists():
        return
    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.split("=", 1)[0].strip() == variable
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def ensure_gitignored(home: Path) -> None:
    gitignore = home / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    if ".env" not in (line.strip() for line in lines):
        lines.append(".env")
        gitignore.write_text("\n".join(lines) + "\n", encoding="utf-8")


def missing_keys(used_providers: set[str], keys: dict[str, str]) -> list[str]:
    """Env var names that a run needs but that are not set."""
    return sorted(
        providers.env_key(name) for name in used_providers
        if providers.is_known(name) and name not in keys
        and providers.env_key(name)  # test-only providers have no key
    )


def redact(text: str, keys: dict[str, str] | None = None) -> str:
    if not text:
        return text
    for value in (keys or {}).values():
        if value and len(value) >= 8:
            text = text.replace(value, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
