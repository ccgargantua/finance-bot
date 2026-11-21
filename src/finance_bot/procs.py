import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from finance_bot.config import Config

PASSTHROUGH_ENV = (
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR",
    "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "SHEETCLI_STATE_DIR",
)
OUTPUT_LIMIT = 20000


@dataclass(frozen=True)
class ProcResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool


def child_env(cfg: Config) -> dict[str, str]:
    env = {k: os.environ[k] for k in PASSTHROUGH_ENV if k in os.environ}
    env["SPREADSHEET_PATH"] = str(cfg.spreadsheet_path)
    env["SHEET_PYTHON"] = cfg.sheet_python
    env["SHEET_CLI"] = str(cfg.sheet_cli)
    return env


def truncate(text: str, limit: int = OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


async def run_capture(argv: list[str], cwd: Path, env: dict[str, str], timeout: float) -> ProcResult:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ProcResult(None, "", "", True)
    return ProcResult(
        proc.returncode,
        truncate(out.decode(errors="replace")),
        truncate(err.decode(errors="replace")),
        False,
    )
