"""python -m testbed.tools.inspect_file <path>: print the file's tools and checks as JSON."""

import json
import sys
import traceback
from pathlib import Path

from .loader import checks_in_file, tools_in_file


def main(path: str) -> int:
    file = Path(path)
    try:
        tools = tools_in_file(file)
        checks = checks_in_file(file)
    except Exception as error:
        cause = error.__cause__ or error
        line = None  # the last line of the researcher's file in the traceback
        for frame in traceback.extract_tb(cause.__traceback__):
            if Path(frame.filename).resolve() == file.resolve():
                line = frame.lineno
        if line is None and isinstance(cause, SyntaxError):
            line = cause.lineno
        print(json.dumps({"ok": False, "error": f"{type(cause).__name__}: {cause}", "line": line}))
        return 1
    print(json.dumps({
        "ok": True,
        "tools": [
            {"ref": item.ref, "name": item.name, "description": item.description,
             "parameters": item.parameters, "takes_ctx": item.takes_ctx,
             "file": item.target_file, "op": item.op}
            for item in tools.values()
        ],
        "checks": [
            {"ref": f"{file.stem}:{name}", "name": name, "description": (func.__doc__ or "").strip()}
            for name, func in checks.items()
        ],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
