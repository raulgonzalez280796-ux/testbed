"""A project folder: scenarios/, tools/, checks/, runs/, .env, settings.yaml.

`python -m testbed init <folder>` creates one with the example templates.
"""

from __future__ import annotations

import re
import shutil
from importlib import resources
from pathlib import Path

from .scenario import Scenario, load_scenario, save_scenario
from .tools.library import NOTES_FILE, load_notes, merge_notes
from .tools.loader import referenced_files

TEMPLATES = Path(str(resources.files("testbed") / "templates"))
KINDS = ("scenarios", "tools", "checks")

GITIGNORE = """# Secrets
.env
# Run outputs
runs/
# Python
__pycache__/
.venv*/
"""

ENV_EXAMPLE = """# Copy to .env and fill in only the providers you use. Never commit .env.
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=
"""


def safe_stem(name: str) -> str:
    """A file stem made of letters, digits and _ (also a valid Python module name)."""
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip()).strip("_").lower()
    if not stem or not stem[0].isalpha():
        stem = f"x_{stem}" if stem else "untitled"
    return stem[:64]


def ensure_project(home: Path) -> None:
    for kind in (*KINDS, "runs"):
        (home / kind).mkdir(parents=True, exist_ok=True)
    gitignore = home / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE, encoding="utf-8")
    example = home / ".env.example"
    if not example.exists():
        example.write_text(ENV_EXAMPLE, encoding="utf-8")


def init_project(home: Path) -> list[str]:
    """Create the folders and copy every template. Existing files are never overwritten."""
    ensure_project(home)
    created = []
    for kind in KINDS:
        for source in sorted((TEMPLATES / kind).glob("*")):
            target = home / kind / source.name
            if source.is_file() and not target.exists():
                shutil.copy2(source, target)
                created.append(f"{kind}/{source.name}")
    if f"tools/{NOTES_FILE}" not in created:
        add_template_notes(home, created)
    return created


def add_template_notes(home: Path, copied: list[str]) -> None:
    """Give tools copied from the templates their notes, keeping the researcher's own."""
    stems = {Path(item).stem for item in copied if item.startswith("tools/") and item.endswith(".py")}
    template_notes = load_notes(TEMPLATES / "tools")
    merge_notes(home / "tools", {name: text for name, text in template_notes.items() if name in stems})


def template_names() -> list[str]:
    return sorted(path.stem for path in (TEMPLATES / "scenarios").glob("*.yaml"))


def template_scenario(name: str) -> Scenario:
    return load_scenario(TEMPLATES / "scenarios" / f"{name}.yaml")


def copy_template_code(home: Path, scenario: Scenario) -> list[str]:
    """Copy the template tools/checks a scenario needs, if the project lacks them."""
    copied = []
    tool_stems = referenced_files([ref for agent in scenario.agents for ref in agent.tools])
    check_stems = {ref.split(":", 1)[0] for ref in scenario.checks}
    for kind, stems in (("tools", tool_stems), ("checks", check_stems)):
        for stem in stems:
            source = TEMPLATES / kind / f"{stem}.py"
            target = home / kind / f"{stem}.py"
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied.append(f"{kind}/{stem}.py")
    add_template_notes(home, copied)
    return copied


def new_from_template(home: Path, template: str, stem: str) -> Path:
    ensure_project(home)
    scenario = template_scenario(template)
    path = home / "scenarios" / f"{safe_stem(stem)}.yaml"
    if path.exists():
        raise FileExistsError(f"scenarios/{path.name} already exists")
    shutil.copy2(TEMPLATES / "scenarios" / f"{template}.yaml", path)
    copy_template_code(home, scenario)
    return path


def scenario_files(home: Path) -> list[Path]:
    return sorted((home / "scenarios").glob("*.yaml"))


def code_files(home: Path, kind: str) -> list[Path]:
    return sorted(path for path in (home / kind).glob("*.py") if not path.name.startswith("_"))


def write_scenario(path: Path, scenario: Scenario) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    save_scenario(scenario, path)
