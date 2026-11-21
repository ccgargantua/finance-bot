import re

DISCORD_LIMIT = 2000


def split_message(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n ")
    if text.strip():
        chunks.append(text)
    return chunks


def strip_mention(content: str, bot_id: int) -> str:
    return re.sub(rf"<@!?{bot_id}>", "", content).strip()


def should_respond(
    author_id: int,
    allowed_user_ids: frozenset[int],
    is_dm: bool,
    mentioned: bool,
    channel_id: int,
    channel_ids: frozenset[int],
) -> bool:
    if author_id not in allowed_user_ids:
        return False
    return is_dm or mentioned or channel_id in channel_ids
