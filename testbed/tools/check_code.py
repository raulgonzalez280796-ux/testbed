"""Static checks of a tool file: Python problems (pyflakes) and the testbed's rules.

Nothing here runs the code; importing it is left to inspect_file, in its own
process. Each problem is {"level": "error" | "warning", "line": int | None,
"message": str}. Only errors stop the Tools page from saving.

The testbed's rules for a tool file (tools/<name>.py):
- exactly one @tool function, named like the file;
- every argument the model fills in has a type hint;
- file= is a plain file name and op= is read, write, append or list;
- a docstring (the description the model sees) and a return type are advised.
"""

from __future__ import annotations

import ast

from pyflakes import checker, messages

from .decorator import OPS
from .workspace import NAME as FILE_NAME

ERROR = "error"
WARNING = "warning"


def problem(level: str, line: int | None, message: str) -> dict:
    return {"level": level, "line": line, "message": message}


def syntax_problem(source: str) -> dict | None:
    try:
        ast.parse(source)
    except SyntaxError as error:
        return problem(ERROR, error.lineno, f"SyntaxError: {error.msg}")
    return None


def python_problems(source: str, filename: str = "tool.py") -> list[dict]:
    """Syntax errors, then pyflakes: undefined names, unused imports, redefinitions."""
    broken = syntax_problem(source)
    if broken:
        return [broken]
    tree = ast.parse(source)
    found = []
    for message in checker.Checker(tree, filename=filename).messages:
        text = message.message % message.message_args
        if isinstance(message, messages.UndefinedName):
            text += " (this fails when that line runs)"
        found.append(problem(WARNING, message.lineno, text))
    return sorted(found, key=lambda item: item["line"] or 0)


def _is_tool_decorator(node: ast.expr) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    return (isinstance(target, ast.Name) and target.id == "tool") or (
        isinstance(target, ast.Attribute) and target.attr == "tool")


def _keyword(decorator: ast.expr, name: str) -> ast.expr | None:
    if isinstance(decorator, ast.Call):
        for keyword in decorator.keywords:
            if keyword.arg == name:
                return keyword.value
    return None


def tool_functions(source: str) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.expr]]:
    """Each @tool function in the source, with its decorator."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if _is_tool_decorator(decorator):
                    found.append((node, decorator))
                    break
    return sorted(found, key=lambda item: item[0].lineno)


def tool_name_in(source: str) -> str | None:
    """The name of the file's only @tool function, or None."""
    try:
        functions = tool_functions(source)
    except SyntaxError:
        return None
    return functions[0][0].name if len(functions) == 1 else None


def tool_rules(source: str, stem: str) -> list[dict]:
    """The testbed's rules for tools/<stem>.py. Assumes the source parses."""
    functions = tool_functions(source)
    if not functions:
        return [problem(ERROR, None, "No @tool function found. A tool file holds one function "
                                     "marked with @tool.")]
    if len(functions) > 1:
        names = ", ".join(function.name for function, _ in functions)
        return [problem(ERROR, functions[1][0].lineno,
                        f"This file has {len(functions)} @tool functions ({names}). A tool file "
                        "holds exactly one: put each tool in its own file.")]
    function, decorator = functions[0]
    line = function.lineno
    found = []
    if function.name != stem:
        found.append(problem(ERROR, line, f"The function is called {function.name} but the file is "
                                          f"{stem}.py. A tool's name is its file name: rename one so "
                                          "they match."))
    name = _keyword(decorator, "name")
    if isinstance(name, ast.Constant) and name.value != stem:
        found.append(problem(ERROR, decorator.lineno, f"name={name.value!r} differs from the file "
                                                      f"name {stem!r}. Remove name= or rename the file."))
    params = [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
    if params and (params[0].arg == "ctx" or _annotation_name(params[0].annotation) == "ToolContext"):
        params = params[1:]
    for param in params:
        if param.annotation is None:
            found.append(problem(ERROR, param.lineno, f"The argument {param.arg} needs a type hint, "
                                                      f"for example {param.arg}: str. The model is "
                                                      "told its type."))
    if function.args.vararg or function.args.kwarg:
        found.append(problem(ERROR, line, "*args and **kwargs are not supported: list each argument."))
    file = _keyword(decorator, "file")
    if file is not None and not (isinstance(file, ast.Constant) and isinstance(file.value, str)
                                 and FILE_NAME.match(file.value)):
        found.append(problem(ERROR, decorator.lineno, "file= must be a plain file name in quotes, "
                                                      'for example file="log.txt".'))
    op = _keyword(decorator, "op")
    if op is not None and not (isinstance(op, ast.Constant) and op.value in OPS):
        found.append(problem(ERROR, decorator.lineno, f"op= must be one of: {', '.join(OPS)}."))
    if ast.get_docstring(function) is None and _keyword(decorator, "description") is None:
        found.append(problem(WARNING, line, "No docstring: the model gets no description of this "
                                            "tool. Add one under the def line."))
    if function.returns is None:
        found.append(problem(WARNING, line, "No return type (for example -> str). Whatever the tool "
                                            "returns is sent to the model as text."))
    return found


def _annotation_name(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def static_problems(source: str, stem: str, kind: str = "tools") -> list[dict]:
    """Every problem found without running the code."""
    found = python_problems(source, f"{stem}.py")
    if found and found[0]["level"] == ERROR:  # a syntax error: nothing else can be checked
        return found
    if kind == "tools":
        found = tool_rules(source, stem) + found
    return found
