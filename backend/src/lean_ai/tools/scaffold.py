"""Scaffold system — reads scaffold.yaml recipes and sets up new projects.

Each scaffold.yaml describes either:
  type: command  — run a framework CLI (e.g. composer, cargo, rails new)
  type: files    — create a minimal directory/file structure from inline content

Variable substitution in commands, paths, and file content:
  {project_name}  — user-supplied project name (as-given)
  {package_name}  — snake_case version safe for identifiers
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from lean_ai.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ScaffoldTemplate:
    name: str
    display_name: str
    description: str
    language: str
    framework: str | None
    aliases: list[str]
    setup_type: str  # "command" or "files"
    setup: dict
    source_dir: Path


@dataclass
class ScaffoldResult:
    scaffold_name: str
    project_dir: str
    files_created: list[str]
    command_output: str
    success: bool
    error: str | None = None


class ScaffoldRegistry:
    """Loads all scaffold.yaml files and provides name/alias lookup."""

    def __init__(self, scaffolds_dir: Path) -> None:
        self._templates: dict[str, ScaffoldTemplate] = self._load(scaffolds_dir)

    def _load(self, scaffolds_dir: Path) -> dict[str, ScaffoldTemplate]:
        lookup: dict[str, ScaffoldTemplate] = {}
        if not scaffolds_dir.is_dir():
            logger.warning("Scaffolds directory not found: %s", scaffolds_dir)
            return lookup
        for subdir in sorted(scaffolds_dir.iterdir()):
            yaml_path = subdir / "scaffold.yaml"
            if not subdir.is_dir() or not yaml_path.exists():
                continue
            try:
                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                tmpl = ScaffoldTemplate(
                    name=data["name"],
                    display_name=data["display_name"],
                    description=data["description"],
                    language=data["language"],
                    framework=data.get("framework"),
                    aliases=data.get("aliases") or [],
                    setup_type=data["setup"]["type"],
                    setup=data["setup"],
                    source_dir=subdir,
                )
                lookup[tmpl.name.lower()] = tmpl
                for alias in tmpl.aliases:
                    lookup[alias.lower()] = tmpl
            except Exception as exc:
                logger.warning("Failed to load scaffold from %s: %s", subdir, exc)
        return lookup

    def get(self, name: str) -> ScaffoldTemplate | None:
        return self._templates.get(name.lower())

    def list_all(self) -> list[ScaffoldTemplate]:
        seen: set[str] = set()
        result: list[ScaffoldTemplate] = []
        for tmpl in self._templates.values():
            if tmpl.name not in seen:
                seen.add(tmpl.name)
                result.append(tmpl)
        return sorted(result, key=lambda t: t.name)


def _package_name(project_name: str) -> str:
    """Convert a project name to a safe snake_case identifier."""
    result = []
    for ch in project_name.lower():
        if ch.isalnum():
            result.append(ch)
        else:
            result.append("_")
    return "".join(result).strip("_")


def _substitute(text: str, project_name: str, package_name: str) -> str:
    return text.replace("{project_name}", project_name).replace("{package_name}", package_name)


class ScaffoldRunner:
    """Executes scaffold recipes."""

    async def run(
        self, template: ScaffoldTemplate, project_name: str, parent_dir: str,
    ) -> ScaffoldResult:
        pkg = _package_name(project_name)
        if template.setup_type == "command":
            result = await self._run_command(template, project_name, pkg, parent_dir)
        else:
            result = self._run_files(template, project_name, pkg, parent_dir)

        if result.success:
            await self._git_init(Path(result.project_dir))
        return result

    async def _git_init(self, project_dir: Path) -> None:
        if (project_dir / ".git").exists():
            return
        steps = [
            ["git", "init"],
            ["git", "add", "."],
            ["git", "commit", "-m", "Initial commit"],
        ]
        try:
            for cmd in steps:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, cwd=str(project_dir),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )
                await proc.communicate()
                if proc.returncode != 0:
                    return
        except Exception as exc:
            logger.warning("git init skipped for %s: %s", project_dir, exc)

    async def _run_command(
        self, template: ScaffoldTemplate, project_name: str, pkg: str, parent_dir: str,
    ) -> ScaffoldResult:
        setup = template.setup
        cmd = _substitute(setup["command"], project_name, pkg)
        cwd = Path(parent_dir)

        pre_create = setup.get("pre_create_project_dir", False)
        if pre_create:
            project_dir = cwd / project_name
            project_dir.mkdir(parents=True, exist_ok=True)
            cwd = project_dir
        else:
            project_dir = cwd / project_name
            cwd.mkdir(parents=True, exist_ok=True)

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd, cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            stdout_bytes, _ = await proc.communicate()
            output = stdout_bytes.decode(errors="replace") if stdout_bytes else ""

            if proc.returncode != 0:
                if project_dir.exists() and any(project_dir.iterdir()):
                    return ScaffoldResult(
                        scaffold_name=template.name, project_dir=str(project_dir),
                        files_created=[], command_output=output, success=True,
                    )
                return ScaffoldResult(
                    scaffold_name=template.name, project_dir=str(project_dir),
                    files_created=[], command_output=output, success=False,
                    error=f"Command exited with code {proc.returncode}",
                )
            return ScaffoldResult(
                scaffold_name=template.name, project_dir=str(project_dir),
                files_created=[], command_output=output, success=True,
            )
        except Exception as exc:
            return ScaffoldResult(
                scaffold_name=template.name, project_dir=str(Path(parent_dir) / project_name),
                files_created=[], command_output="", success=False, error=str(exc),
            )

    def _run_files(
        self, template: ScaffoldTemplate, project_name: str, pkg: str, parent_dir: str,
    ) -> ScaffoldResult:
        setup = template.setup
        project_dir = Path(parent_dir) / project_name
        created: list[str] = []
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
            for raw_dir in setup.get("directories") or []:
                rel = _substitute(raw_dir, project_name, pkg)
                (project_dir / rel).mkdir(parents=True, exist_ok=True)
            for file_spec in setup.get("files") or []:
                rel_path = _substitute(file_spec["path"], project_name, pkg)
                content = _substitute(file_spec.get("content") or "", project_name, pkg)
                dest = project_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
                created.append(rel_path)
            return ScaffoldResult(
                scaffold_name=template.name, project_dir=str(project_dir),
                files_created=sorted(created), command_output="", success=True,
            )
        except Exception as exc:
            return ScaffoldResult(
                scaffold_name=template.name, project_dir=str(project_dir),
                files_created=created, command_output="", success=False, error=str(exc),
            )


_registry: ScaffoldRegistry | None = None
_runner: ScaffoldRunner | None = None


def get_scaffold_registry() -> ScaffoldRegistry:
    global _registry
    if _registry is None:
        _registry = ScaffoldRegistry(settings.scaffolds_dir)
    return _registry


def get_scaffold_runner() -> ScaffoldRunner:
    global _runner
    if _runner is None:
        _runner = ScaffoldRunner()
    return _runner
