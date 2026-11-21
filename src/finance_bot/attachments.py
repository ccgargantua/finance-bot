import base64
import re
from dataclasses import dataclass
from pathlib import PurePath

MB = 1024 * 1024
MAX_ATTACHMENT_BYTES = 25 * MB
MAX_PDF_INLINE_BYTES = 20 * MB
MAX_IMAGE_INLINE_BYTES = 5 * MB
MAX_MESSAGE_INLINE_BYTES = 20 * MB
MAX_TEXT_INLINE_CHARS = 20000
IMAGE_EST_CHARS = 6400
PDF_PAGE_EST_CHARS = 12000
NO_TEXT_PLACEHOLDER = "(no text, attachments only)"

TEXT_EXTS = frozenset({".csv", ".tsv", ".txt", ".ofx", ".qfx", ".qif", ".json"})
SAVE_ONLY_EXTS = frozenset({".xlsx", ".xls", ".ods"})
IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
PDF_PAGE_RE = re.compile(rb"/Type\s*/Page[^s]")
UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class UserTurn:
    blocks: list[dict]
    size_chars: int


def ext_of(filename: str) -> str:
    return PurePath(filename).suffix.lower()


def classify(filename: str) -> str | None:
    ext = ext_of(filename)
    if ext in TEXT_EXTS:
        return "text"
    if ext == ".pdf":
        return "pdf"
    if ext in IMAGE_MEDIA_TYPES:
        return "image"
    if ext in SAVE_ONLY_EXTS:
        return "spreadsheet"
    return None


def rejection(filename: str, size: int) -> str | None:
    """Reason the attachment must not be saved, or None if acceptable."""
    if classify(filename) is None:
        return f"unsupported file type {ext_of(filename) or '(none)'!r}"
    if size > MAX_ATTACHMENT_BYTES:
        return f"over the {MAX_ATTACHMENT_BYTES // MB} MB limit ({format_size(size)})"
    return None


def sanitize_filename(name: str) -> str:
    base = re.split(r"[\\/]", name)[-1]
    return UNSAFE_RE.sub("_", base).lstrip(".") or "file"


def unique_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    path = PurePath(name)
    stem, suffix = path.stem, path.suffix
    n = 1
    while f"{stem}-{n}{suffix}" in taken:
        n += 1
    return f"{stem}-{n}{suffix}"


def format_size(n: int) -> str:
    if n >= MB:
        return f"{n / MB:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def pdf_pages(data: bytes) -> int:
    return max(1, len(PDF_PAGE_RE.findall(data)))


def b64_source(media_type: str, data: bytes) -> dict:
    return {"type": "base64", "media_type": media_type, "data": base64.b64encode(data).decode("ascii")}


def text_part(text: str) -> tuple[dict, int]:
    return {"type": "text", "text": text}, len(text)


def inline_part(filename: str, path: str, kind: str, data: bytes, budget: int) -> tuple[tuple[dict, int] | None, str]:
    """(inline block with its size estimate or None, note appended to the kind label)."""
    if kind == "text":
        text = data.decode("utf-8", errors="replace")
        if not text:
            return text_part("(empty file)"), ", empty"
        if len(text) <= MAX_TEXT_INLINE_CHARS:
            return text_part(f"Contents of {path}:\n{text}"), ", inlined"
        head = text[:MAX_TEXT_INLINE_CHARS]
        return text_part(f"Preview of {path} (first {MAX_TEXT_INLINE_CHARS} of {len(text)} chars, truncated):\n{head}"), ", truncated preview"
    if kind in ("pdf", "image"):
        limit = MAX_PDF_INLINE_BYTES if kind == "pdf" else MAX_IMAGE_INLINE_BYTES
        if len(data) > limit:
            return None, ", saved only (too large to inline)"
        if len(data) > budget:
            return None, ", saved only (message inline limit reached)"
        if kind == "pdf":
            block = {"type": "document", "source": b64_source("application/pdf", data)}
            return (block, pdf_pages(data) * PDF_PAGE_EST_CHARS), ", inlined"
        block = {"type": "image", "source": b64_source(IMAGE_MEDIA_TYPES[ext_of(filename)], data)}
        return (block, IMAGE_EST_CHARS), ", inlined"
    return None, ", saved only"


def saved_parts(filename: str, path: str, kind: str, data: bytes, budget: int = MAX_MESSAGE_INLINE_BYTES) -> list[tuple[dict, int]]:
    inline, note = inline_part(filename, path, kind, data, budget)
    header = text_part(f"Attachment: {filename} saved at {path} ({format_size(len(data))}, {kind}{note})")
    return [header, inline] if inline else [header]


def binary_inlined(parts: list[tuple[dict, int]]) -> bool:
    return any(block["type"] in ("image", "document") for block, _ in parts)


def rejected_parts(filename: str, reason: str) -> list[tuple[dict, int]]:
    return [text_part(f"Attachment: {filename} not saved: {reason}")]


def build_turn(text: str, parts: list[tuple[dict, int]]) -> UserTurn:
    parts = [text_part(text or NO_TEXT_PLACEHOLDER), *parts]
    return UserTurn([block for block, _ in parts], sum(size for _, size in parts))
