import logging
import shutil
from pathlib import Path

from finance_bot.attachments import sanitize_filename, unique_name

log = logging.getLogger(__name__)

INBOX_DIR = "inbox"


def channel_dir(repo_root: Path, channel_id: int) -> Path:
    return repo_root / INBOX_DIR / str(channel_id)


def save(repo_root: Path, channel_id: int, filename: str, data: bytes) -> str:
    """Writes under inbox/<channel_id>/ and returns the repo-root-relative path."""
    directory = channel_dir(repo_root, channel_id)
    directory.mkdir(parents=True, exist_ok=True)
    name = unique_name(sanitize_filename(filename), {p.name for p in directory.iterdir()})
    (directory / name).write_bytes(data)
    return f"{INBOX_DIR}/{channel_id}/{name}"


def wipe_channel(repo_root: Path, channel_id: int) -> None:
    shutil.rmtree(channel_dir(repo_root, channel_id), ignore_errors=True)
    log.info("inbox wiped channel=%s", channel_id)


def wipe_all(repo_root: Path) -> None:
    shutil.rmtree(repo_root / INBOX_DIR, ignore_errors=True)
    log.info("inbox wiped all")
