import difflib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from finance_bot.config import Config
from finance_bot.procs import child_env, run_capture

log = logging.getLogger(__name__)

SKILL_FILE = "SKILL.md"
NAME_RE = re.compile(r"[a-z0-9-]{1,64}")
SCRIPT_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\.py")
MAX_SKILL_MD_CHARS = 20000
MAX_SCRIPT_CHARS = 50000
MAX_SCRIPTS = 10
SCRIPT_TIMEOUT_S = 60


class SkillError(Exception):
    pass


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str


@dataclass(frozen=True)
class SkillFiles:
    name: str
    files: dict[str, str]


def skill_dir(root: Path, name: str) -> Path:
    if not NAME_RE.fullmatch(name):
        raise SkillError(f"invalid skill name {name!r}: use [a-z0-9-], 1-64 chars")
    root = root.resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise SkillError(f"skill {name!r} escapes skills dir")
    return path


def script_path(root: Path, skill: str, script: str) -> Path:
    if not SCRIPT_RE.fullmatch(script):
        raise SkillError(f"invalid script name {script!r}: use [A-Za-z0-9_-]+.py, no paths")
    base = skill_dir(root, skill)
    path = (base / script).resolve()
    if path.parent != base or not path.is_file():
        raise SkillError(f"script {skill}/{script} not found")
    return path


def parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip().strip("\"'")
    return {}


def list_skills(root: Path) -> list[SkillInfo]:
    if not root.is_dir():
        return []
    skills = []
    for entry in sorted(root.iterdir()):
        md = entry / SKILL_FILE
        if not NAME_RE.fullmatch(entry.name) or not md.is_file():
            continue
        meta = parse_frontmatter(md.read_text())
        skills.append(SkillInfo(entry.name, meta.get("description", "")))
    return skills


def format_skill_index(skills: list[SkillInfo]) -> str:
    if not skills:
        return "(no skills yet)"
    return "\n".join(f"- {s.name}: {s.description}" for s in skills)


def script_names(root: Path, name: str) -> list[str]:
    return sorted(p.name for p in skill_dir(root, name).glob("*.py") if SCRIPT_RE.fullmatch(p.name))


def read_skill(root: Path, name: str) -> str:
    md = skill_dir(root, name) / SKILL_FILE
    if not md.is_file():
        raise SkillError(f"skill {name!r} not found")
    parts = [md.read_text()]
    for script in script_names(root, name):
        parts.append(f"--- {script} ---\n{script_path(root, name, script).read_text()}")
    return "\n\n".join(parts)


def prepare_skill(name: str, skill_md: str, scripts: dict[str, str]) -> SkillFiles:
    if not NAME_RE.fullmatch(name):
        raise SkillError(f"invalid skill name {name!r}: use [a-z0-9-], 1-64 chars")
    if len(skill_md) > MAX_SKILL_MD_CHARS:
        raise SkillError(f"skill_md over {MAX_SKILL_MD_CHARS} chars")
    meta = parse_frontmatter(skill_md)
    if meta.get("name") != name or not meta.get("description"):
        raise SkillError("skill_md needs frontmatter with name equal to the skill name and a non-empty description")
    if len(scripts) > MAX_SCRIPTS:
        raise SkillError(f"more than {MAX_SCRIPTS} scripts")
    for filename, content in scripts.items():
        if not SCRIPT_RE.fullmatch(filename):
            raise SkillError(f"invalid script filename {filename!r}: use [A-Za-z0-9_-]+.py, no paths")
        if len(content) > MAX_SCRIPT_CHARS:
            raise SkillError(f"script {filename} over {MAX_SCRIPT_CHARS} chars")
    return SkillFiles(name, {SKILL_FILE: skill_md, **scripts})


def skill_exists(root: Path, name: str) -> bool:
    return (skill_dir(root, name) / SKILL_FILE).is_file()


def skill_diff(root: Path, skill: SkillFiles) -> str:
    base = skill_dir(root, skill.name)
    chunks = []
    for filename, new in skill.files.items():
        target = base / filename
        old = target.read_text() if target.is_file() else ""
        if old == new:
            continue
        chunks.extend(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"a/{filename}", tofile=f"b/{filename}",
        ))
    return "".join(chunks)


def write_skill(root: Path, skill: SkillFiles) -> None:
    base = skill_dir(root, skill.name)
    base.mkdir(parents=True, exist_ok=True)
    for filename, content in skill.files.items():
        (base / filename).write_text(content)


def script_argv(cfg: Config, skill: str, script: str, args: list[str]) -> list[str]:
    if not all(isinstance(a, str) for a in args):
        raise SkillError("args must all be strings")
    return [cfg.sheet_python, str(script_path(cfg.skills_dir, skill, script)), *args]


async def script_run(cfg: Config, skill: str, script: str, args: list[str]) -> str:
    argv = script_argv(cfg, skill, script, args)
    log.info("run_script skill=%s script=%s args=%s", skill, script, args)
    result = await run_capture(argv, cfg.repo_root, child_env(cfg), SCRIPT_TIMEOUT_S)
    if result.timed_out:
        log.error("run_script timeout skill=%s script=%s", skill, script)
        return f"timed out after {SCRIPT_TIMEOUT_S}s"
    log.info("run_script done skill=%s script=%s rc=%s", skill, script, result.returncode)
    return f"exit code: {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
