import json
import time
from typing import Callable

SIZE_NOTICE = "History exceeded the size limit and was cleared; starting a fresh conversation."


def measure(content) -> int:
    if isinstance(content, str):
        return len(content)
    return sum(
        len(json.dumps(block)) if isinstance(block, dict) else len(block.model_dump_json())
        for block in content
    )


class Conversation:
    """Append-only message list plus the system prompt frozen when it started.

    Editing earlier turns invalidates later thinking blocks, so the only mutation besides append
    is rollback_to(mark), which truncates the tail to a state that was already sent.
    """

    def __init__(self, system: list[dict]):
        self.system = system
        self.messages: list[dict] = []
        self._sizes: list[int] = []
        self.last_active = 0.0

    def append(self, message: dict, size: int | None = None) -> None:
        self.messages.append(message)
        self._sizes.append(measure(message["content"]) if size is None else size)

    def mark(self) -> int:
        return len(self.messages)

    def rollback_to(self, mark: int) -> None:
        del self.messages[mark:]
        del self._sizes[mark:]

    def size_chars(self) -> int:
        return sum(self._sizes)


class ConversationStore:
    def __init__(self, idle_s: float, max_chars: int, clock: Callable[[], float] = time.monotonic, on_wipe: Callable[[int], None] = lambda channel_id: None):
        self.idle_s = idle_s
        self.max_chars = max_chars
        self.clock = clock
        self.on_wipe = on_wipe
        self._by_channel: dict[int, Conversation] = {}

    def acquire(self, channel_id: int, make_system: Callable[[], list[dict]]) -> tuple[Conversation, str | None]:
        now = self.clock()
        conv = self._by_channel.get(channel_id)
        notice = None
        if conv and now - conv.last_active > self.idle_s:
            conv = None
        elif conv and conv.size_chars() > self.max_chars:
            conv = None
            notice = SIZE_NOTICE
        if conv is None:
            if channel_id in self._by_channel:
                self.on_wipe(channel_id)
            conv = Conversation(make_system())
            self._by_channel[channel_id] = conv
        conv.last_active = now
        return conv, notice

    def touch(self, conv: Conversation) -> None:
        conv.last_active = self.clock()

    def reset(self, channel_id: int) -> None:
        self._by_channel.pop(channel_id, None)
        self.on_wipe(channel_id)
