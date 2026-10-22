"""Load researcher code files (tools/*.py, checks/*.py) and resolve references.

Scenarios name a tool by its file: "read_log" is tools/read_log.py. Checks, and
tools in a file that holds several, are "<file stem>:<function>". Files are imported
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
from .check_code import ERROR, problem, static_problems, tool_name_in
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
            function = marked.func.__name__
            # A one-tool file is referred to by its name alone: tools/read_log.py is read_log.
            marked.ref = function if function == path.stem else f"{path.stem}:{function}"
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
        raise CodeError(f"{ref!r} should look like 'file:function'")
    stem, function = ref.split(":", 1)
    return stem, function


def resolve_tool(ref: str, tools_dir: Path) -> Tool:
    """A tool by name: "read_log" is tools/read_log.py. "file:function" also works,
    for a file that holds several tools."""
    if ":" in ref:
        stem, function = split_ref(ref)
    else:
        stem = function = ref
    path = tools_dir / f"{stem}.py"
    if not path.is_file():
        raise CodeError(f"no tool called {ref!r}: there is no file tools/{stem}.py")
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


def resolve_agent_tools(refs: list[str], tools_dir: Path, agent_id: str) -> dict[str, Tool]:
    """Exposed tool name -> Tool for one agent. Two tools may not share a name."""
    tools: dict[str, Tool] = {}
    for ref in refs:
        resolved = resolve_tool(ref, tools_dir)
        if resolved.name in tools:
            raise CodeError(
                f"agent {agent_id}: two tools are both called {resolved.name!r} "
                f"({tools[resolved.name].ref} and {ref})"
            )
        tools[resolved.name] = resolved
    return tools


def referenced_files(refs: list[str]) -> set[str]:
    """The tools/ file stems a list of tool references needs."""
    return {ref.split(":", 1)[0] for ref in refs}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def syntax_error(source: str) -> dict | None:
    try:
        ast.parse(source)
    except SyntaxError as error:
        return {"ok": False, "error": f"SyntaxError: {error.msg}", "line": error.lineno}
    return None


def inspect_file(path: Path, timeout: float = 20.0, kind: str | None = None) -> dict:
    """Check a code file and list its tools and checks.

    The file is imported in a separate process, so a broken or slow file cannot
    take down the caller (the UI). `problems` lists everything found, each with
    a level ("error" or "warning") and a line; `kind="tools"` adds the testbed's
    rules for tool files (see check_code). `ok` means the file imports.
    """
    source = path.read_text(encoding="utf-8")
    problems = static_problems(source, path.stem, kind or "")
    error = syntax_error(source)
    if error:
        return {**error, "tools": [], "checks": [], "problems": problems}
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "testbed.tools.inspect_file", str(path)],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
    except subprocess.TimeoutExpired:
        report = {"ok": False, "error": f"Importing the file took longer than {timeout:.0f} s"}
    else:
        try:
            report = json.loads(completed.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            report = {"ok": False, "error": (completed.stderr or completed.stdout).strip()[-2000:]}
    report.setdefault("tools", [])
    report.setdefault("checks", [])
    if not report["ok"]:
        problems.insert(0, problem(ERROR, report.get("line"),
                                   f"Importing the file failed: {report['error']}"))
    elif kind == "tools" and not report["tools"] and tool_name_in(source):
        problems.insert(0, problem(ERROR, None, "The @tool here is not the testbed's: start the "
                                                "file with from testbed import ToolContext, tool"))
    # Errors first, then warnings; each in line order.
    report["problems"] = sorted(problems, key=lambda item: (
        item["level"] != ERROR, item["line"] is None, item["line"] or 0))
    return report


def has_errors(report: dict) -> bool:
    return any(item["level"] == ERROR for item in report.get("problems", []))
