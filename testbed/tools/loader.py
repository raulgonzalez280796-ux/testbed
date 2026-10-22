"""Load researcher code files (tools/*.py, checks/*.py) and resolve references.

Scenarios name researcher code as "<file stem>:<function>". Files are imported
under a private module name and their folder is never put on sys.path, so a
file called json.py cannot shadow the standard library.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from ..checks import CHECK_ATTR
from .builtin import BUILTIN_TOOLS
from .decorator import TOOL_ATTR, Tool

_modules: dict[tuple[str, float, int], ModuleType] = {}


class CodeError(Exception):
    pass


def import_code_file(path: Path) -> ModuleType:
    path = path.resolve()
    stat = path.stat()
    key = (str(path), stat.st_mtime, stat.st_size)
    if key in _modules:
        return _modules[key]
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:12]
    name = f"testbed_user_{path.stem}_{digest}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CodeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        sys.modules.pop(name, None)
        raise CodeError(f"{path.name}: {type(error).__name__}: {error}") from error
    _modules[key] = module
    return module


def tools_in_file(path: Path) -> dict[str, Tool]:
    module = import_code_file(path)
    found = {}
    for value in vars(module).values():
        marked = getattr(value, TOOL_ATTR, None)
        if isinstance(marked, Tool):
            marked.ref = f"{path.stem}:{marked.func.__name__}"
            marked.source = path
            found[marked.func.__name__] = marked
    return found


def checks_in_file(path: Path) -> dict:
    module = import_code_file(path)
    return {
        value.__name__: value for value in vars(module).values()
        if callable(value) and getattr(value, CHECK_ATTR, False)
    }


def split_ref(ref: str) -> tuple[str, str]:
    if ":" not in ref:
        raise CodeError(f"{ref!r} is not a built-in tool; researcher code is named 'file:function'")
    stem, function = ref.split(":", 1)
    return stem, function


def resolve_tool(ref: str, tools_dir: Path, scenario_tools: dict[str, Tool] | None = None) -> Tool:
    """A scenario's own tools first, then built-ins, then researcher code ("file:function")."""
    if scenario_tools and ref in scenario_tools:
        return scenario_tools[ref]
    if ref in BUILTIN_TOOLS:
        return BUILTIN_TOOLS[ref]
    if ":" not in ref:
        raise CodeError(
            f"no tool called {ref!r}: not a tool in the scenario's tools section, not a "
            f"built-in tool, and not researcher code (file:function)"
        )
    stem, function = split_ref(ref)
    path = tools_dir / f"{stem}.py"
    if not path.is_file():
        raise CodeError(f"tool {ref!r}: no file tools/{stem}.py")
    found = tools_in_file(path)
    if function not in found:
        raise CodeError(f"tool {ref!r}: tools/{stem}.py has no @tool function {function!r}")
    return found[function]


def resolve_check(ref: str, checks_dir: Path):
    stem, function = split_ref(ref)
    path = checks_dir / f"{stem}.py"
    if not path.is_file():
        raise CodeError(f"check {ref!r}: no file checks/{stem}.py")
    found = checks_in_file(path)
    if function not in found:
        raise CodeError(f"check {ref!r}: checks/{stem}.py has no @check function {function!r}")
    return found[function]


def resolve_agent_tools(
    refs: list[str], tools_dir: Path, agent_id: str,
    scenario_tools: dict[str, Tool] | None = None,
) -> dict[str, Tool]:
    """Exposed tool name -> Tool for one agent. Two tools may not share a name."""
    tools: dict[str, Tool] = {}
    for ref in refs:
        resolved = resolve_tool(ref, tools_dir, scenario_tools)
        if resolved.name in tools:
            raise CodeError(
                f"agent {agent_id}: two tools are both called {resolved.name!r} "
                f"({tools[resolved.name].ref} and {ref})"
            )
        tools[resolved.name] = resolved
    return tools


def referenced_files(refs: list[str]) -> set[str]:
    """Researcher code files ("file:function") among tool refs; other names are not files."""
    return {split_ref(ref)[0] for ref in refs if ":" in ref}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def syntax_error(source: str) -> dict | None:
    try:
        ast.parse(source)
    except SyntaxError as error:
        return {"ok": False, "error": f"SyntaxError: {error.msg}", "line": error.lineno}
    return None


def inspect_file(path: Path, timeout: float = 20.0) -> dict:
    """Import a code file in a separate process and list its tools and checks.

    A broken or slow file cannot take down the caller (the UI).
    """
    error = syntax_error(path.read_text(encoding="utf-8"))
    if error:
        return error
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "testbed.tools.inspect_file", str(path)],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Importing the file took longer than {timeout:.0f} s"}
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"ok": False, "error": (completed.stderr or completed.stdout).strip()[-2000:]}
