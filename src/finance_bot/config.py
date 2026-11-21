from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping, MutableMapping

REPO_ROOT = Path(__file__).resolve().parents[2]
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
DEFAULT_SHEET_PYTHON = "/usr/bin/python3"
DEFAULT_HISTORY_IDLE_MINUTES = 30
DEFAULT_HISTORY_MAX_CHARS = 400000
MAX_TOKENS = 16000
MAX_TOOL_ITERATIONS = 25
ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    discord_token: str
    allowed_user_ids: frozenset[int]
    channel_ids: frozenset[int]
    spreadsheet_path: Path
    model: str
    effort: str
    sheet_python: str
    history_idle_s: float
    history_max_chars: int
    sheet_cli: Path
    skills_dir: Path
    repo_root: Path


def parse_dotenv(text: str, source: str) -> dict[str, str]:
    values = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not ENV_KEY.fullmatch(key):
            raise ConfigError(f"{source}:{lineno}: expected KEY=value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def load_dotenv(env: MutableMapping[str, str], path: Path = REPO_ROOT / ".env") -> None:
    if not path.is_file():
        return
    for key, value in parse_dotenv(path.read_text(), str(path)).items():
        env.setdefault(key, value)


def parse_id_list(raw: str, var: str) -> frozenset[int]:
    try:
        return frozenset(int(part) for part in raw.split(",") if part.strip())
    except ValueError:
        raise ConfigError(f"{var} must be comma-separated integers, got {raw!r}")


def parse_positive(env: Mapping[str, str], var: str, default, cast):
    raw = env.get(var, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ConfigError(f"{var} must be a number, got {raw!r}")
    if value <= 0:
        raise ConfigError(f"{var} must be positive, got {raw!r}")
    return value


def load_config(env: Mapping[str, str], repo_root: Path = REPO_ROOT) -> Config:
    missing = [v for v in ("DISCORD_TOKEN", "ANTHROPIC_API_KEY", "ALLOWED_USER_IDS") if not env.get(v, "").strip()]
    if missing:
        raise ConfigError(f"missing required env vars: {', '.join(missing)} (see .env.example)")

    allowed = parse_id_list(env["ALLOWED_USER_IDS"], "ALLOWED_USER_IDS")
    if not allowed:
        raise ConfigError("ALLOWED_USER_IDS has no ids")

    effort = env.get("EFFORT", DEFAULT_EFFORT).strip()
    if effort not in EFFORTS:
        raise ConfigError(f"EFFORT must be one of {', '.join(EFFORTS)}, got {effort!r}")

    spreadsheet = (repo_root / env.get("SPREADSHEET_PATH", "spreadsheet.ods").strip()).resolve()
    if not spreadsheet.is_file():
        raise ConfigError(f"spreadsheet not found: {spreadsheet} (copy template.ods to spreadsheet.ods or set SPREADSHEET_PATH)")

    sheet_cli = repo_root / "claude-sheets" / "src" / "sheet.py"
    if not sheet_cli.is_file():
        raise ConfigError(f"{sheet_cli} missing; run: git submodule update --init")

    return Config(
        discord_token=env["DISCORD_TOKEN"].strip(),
        allowed_user_ids=allowed,
        channel_ids=parse_id_list(env.get("CHANNEL_IDS", ""), "CHANNEL_IDS"),
        spreadsheet_path=spreadsheet,
        model=env.get("MODEL", DEFAULT_MODEL).strip(),
        effort=effort,
        sheet_python=env.get("SHEET_PYTHON", DEFAULT_SHEET_PYTHON).strip(),
        history_idle_s=parse_positive(env, "HISTORY_IDLE_MINUTES", DEFAULT_HISTORY_IDLE_MINUTES, float) * 60,
        history_max_chars=parse_positive(env, "HISTORY_MAX_CHARS", DEFAULT_HISTORY_MAX_CHARS, int),
        sheet_cli=sheet_cli,
        skills_dir=repo_root / "skills",
        repo_root=repo_root,
    )
