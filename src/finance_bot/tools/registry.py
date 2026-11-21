import logging
from dataclasses import dataclass

from finance_bot.approval import Approver
from finance_bot.config import Config
from finance_bot.tools import skills as store
from finance_bot.tools.sheet import SheetError, sheet_run
from finance_bot.tools.skills import SkillError

log = logging.getLogger(__name__)

STR_LIST = {"type": "array", "items": {"type": "string"}}

# Constant across turns: changing tools mid-conversation invalidates thinking blocks.
TOOL_SCHEMAS: list[dict] = [
    {
        "name": "sheet",
        "description": "Run one sheet CLI action on the user's spreadsheet. The file argument is added automatically; do not pass it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Sheet action name, e.g. read, write, append, batch."},
                "args": {**STR_LIST, "description": "Remaining CLI arguments after the file, e.g. [\"--sheet\", \"Budget\", \"--range\", \"A1:C10\"]. Each flag and value is its own element."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "list_skills",
        "description": "List available skills (name and description), live from disk.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_skill",
        "description": "Read a skill's full SKILL.md and the source of its scripts.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Skill name."}},
            "required": ["name"],
        },
    },
    {
        "name": "propose_skill",
        "description": "Propose a new skill or an update to an existing one. The user is shown the content and must approve before anything is written. Files not included in an update are left untouched.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Lowercase letters, digits and hyphens only."},
                "skill_md": {"type": "string", "description": "Full SKILL.md text, starting with frontmatter containing name (equal to the skill name) and description."},
                "scripts": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Optional map of filename (like report.py, no paths) to Python source."},
            },
            "required": ["name", "skill_md"],
        },
    },
    {
        "name": "run_script",
        "description": "Run an existing skill script. The user must approve each run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill": {"type": "string"},
                "script": {"type": "string", "description": "Script filename inside the skill, e.g. report.py."},
                "args": {**STR_LIST, "description": "Command-line arguments for the script."},
            },
            "required": ["skill", "script"],
        },
    },
]


@dataclass(frozen=True)
class ToolOutput:
    text: str
    is_error: bool = False


def _str(value, field: str) -> str:
    if not isinstance(value, str):
        raise SkillError(f"{field} must be a string")
    return value


def _str_list(value, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise SkillError(f"{field} must be a list of strings")
    return value


def _str_map(value, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise SkillError(f"{field} must be an object of strings")
    return value


async def _sheet(cfg: Config, approver: Approver, inp: dict) -> ToolOutput:
    return ToolOutput(await sheet_run(cfg, _str(inp.get("action"), "action"), _str_list(inp.get("args"), "args")))


async def _list_skills(cfg: Config, approver: Approver, inp: dict) -> ToolOutput:
    return ToolOutput(store.format_skill_index(store.list_skills(cfg.skills_dir)))


async def _read_skill(cfg: Config, approver: Approver, inp: dict) -> ToolOutput:
    return ToolOutput(store.read_skill(cfg.skills_dir, _str(inp.get("name"), "name")))


async def _propose_skill(cfg: Config, approver: Approver, inp: dict) -> ToolOutput:
    name = _str(inp.get("name"), "name")
    skill = store.prepare_skill(name, _str(inp.get("skill_md"), "skill_md"), _str_map(inp.get("scripts"), "scripts"))
    is_update = store.skill_exists(cfg.skills_dir, name)
    files = dict(skill.files)
    if is_update:
        diff = store.skill_diff(cfg.skills_dir, skill)
        if not diff:
            return ToolOutput("proposal is identical to the existing skill", True)
        files = {"changes.diff": diff}
    kind = "UPDATE existing skill (overwrites listed files)" if is_update else "NEW skill"
    summary = f"**Proposed {kind}: `{name}`**\nFiles: {', '.join(skill.files)}"
    log.info("propose_skill name=%s update=%s files=%s", name, is_update, list(skill.files))
    if not await approver(summary, files):
        return ToolOutput("User rejected the proposal (or it timed out). Nothing was written.", True)
    store.write_skill(cfg.skills_dir, skill)
    return ToolOutput(f"Approved and written: skills/{name}/ ({', '.join(skill.files)})")


async def _run_script(cfg: Config, approver: Approver, inp: dict) -> ToolOutput:
    skill, script, args = _str(inp.get("skill"), "skill"), _str(inp.get("script"), "script"), _str_list(inp.get("args"), "args")
    store.script_path(cfg.skills_dir, skill, script)
    if not await approver(f"**Run script** `{skill}/{script}`\nargs: `{args}`", {}):
        return ToolOutput("User rejected the run (or it timed out). Script was not run.", True)
    return ToolOutput(await store.script_run(cfg, skill, script, args))


HANDLERS = {
    "sheet": _sheet,
    "list_skills": _list_skills,
    "read_skill": _read_skill,
    "propose_skill": _propose_skill,
    "run_script": _run_script,
}


async def dispatch(cfg: Config, approver: Approver, name: str, tool_input) -> ToolOutput:
    handler = HANDLERS.get(name)
    if handler is None:
        return ToolOutput(f"unknown tool {name!r}", True)
    if not isinstance(tool_input, dict):
        return ToolOutput("tool input must be an object", True)
    try:
        return await handler(cfg, approver, tool_input)
    except (SheetError, SkillError) as e:
        return ToolOutput(str(e), True)
    except Exception as e:
        log.exception("tool crashed name=%s", name)
        return ToolOutput(f"internal error: {type(e).__name__}: {e}", True)
