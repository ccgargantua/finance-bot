import logging

from finance_bot.config import Config
from finance_bot.procs import child_env, run_capture

log = logging.getLogger(__name__)

SHEET_TIMEOUT_S = 120
ALLOWED_SHEET_ACTIONS = frozenset({
    "info", "read", "find", "filter", "stats", "get-format", "names", "charts",
    "write", "write-range", "append", "clear", "copy-range", "move-range", "fill",
    "sort", "dedupe", "recalc", "merge", "unmerge", "hyperlink", "comment", "delete-comment",
    "insert-rows", "delete-rows", "insert-cols", "delete-cols",
    "hide-rows", "show-rows", "hide-cols", "show-cols",
    "col-width", "row-height", "autofit", "freeze", "unfreeze",
    "add-sheet", "delete-sheet", "rename-sheet", "copy-sheet", "move-sheet",
    "hide-sheet", "show-sheet", "protect", "unprotect", "define-name", "delete-name",
    "format", "cond-format", "cond-format-clear", "validation", "validation-clear", "autofilter",
    "chart", "delete-chart", "image", "batch",
})
FORBIDDEN_FLAGS = ("--ops-file", "--path")


class SheetError(Exception):
    pass


def build_sheet_argv(cfg: Config, action: str, args: list[str]) -> list[str]:
    if action not in ALLOWED_SHEET_ACTIONS:
        raise SheetError(f"action {action!r} not allowed; allowed: {', '.join(sorted(ALLOWED_SHEET_ACTIONS))}")
    if not all(isinstance(a, str) for a in args):
        raise SheetError("args must all be strings")
    for a in args:
        if a.split("=", 1)[0] in FORBIDDEN_FLAGS:
            raise SheetError(f"flag {a.split('=', 1)[0]} not allowed (reads arbitrary files); pass inline --ops / --data instead")
    return [cfg.sheet_python, str(cfg.sheet_cli), action, str(cfg.spreadsheet_path), *args]


async def sheet_run(cfg: Config, action: str, args: list[str]) -> str:
    argv = build_sheet_argv(cfg, action, args)
    log.info("sheet action=%s args=%s", action, args)
    result = await run_capture(argv, cfg.repo_root, child_env(cfg), SHEET_TIMEOUT_S)
    if result.timed_out:
        log.error("sheet timeout action=%s", action)
        raise SheetError(f"sheet {action} timed out after {SHEET_TIMEOUT_S}s")
    if result.returncode != 0:
        log.warning("sheet failed action=%s rc=%s", action, result.returncode)
    return result.stdout or result.stderr or f"(no output, exit {result.returncode})"
