import asyncio
import logging
from collections import defaultdict
from functools import partial

import discord
from anthropic import AsyncAnthropic

from finance_bot import inbox
from finance_bot.agent import TurnResult, run_turn
from finance_bot.approval import request_approval
from finance_bot.attachments import MAX_MESSAGE_INLINE_BYTES, UserTurn, binary_inlined, build_turn, classify, rejected_parts, rejection, saved_parts
from finance_bot.config import Config
from finance_bot.conversation import ConversationStore
from finance_bot.messaging import should_respond, split_message, strip_mention
from finance_bot.prompt import build_static_prompt, build_system

log = logging.getLogger(__name__)

RESET_COMMAND = "!reset"


class FinanceBot(discord.Client):
    def __init__(self, cfg: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.cfg = cfg
        self.anthropic = AsyncAnthropic()
        self.static_prompt = build_static_prompt(cfg)
        inbox.wipe_all(cfg.repo_root)
        self.conversations = ConversationStore(
            cfg.history_idle_s, cfg.history_max_chars,
            on_wipe=partial(inbox.wipe_channel, cfg.repo_root),
        )
        self.locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def on_ready(self) -> None:
        log.info("ready user=%s id=%s", self.user, self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        respond = should_respond(
            message.author.id, self.cfg.allowed_user_ids,
            message.guild is None, self.user in message.mentions,
            message.channel.id, self.cfg.channel_ids,
        )
        if not respond:
            return
        text = strip_mention(message.content, self.user.id)
        if text == RESET_COMMAND:
            self.conversations.reset(message.channel.id)
            await message.channel.send("History cleared.")
            return
        if text or message.attachments:
            await self.handle_turn(message, text)

    async def handle_turn(self, message: discord.Message, text: str) -> None:
        channel = message.channel
        approver = partial(self.approve, channel)
        async with self.locks[channel.id]:
            conv, notice = self.conversations.acquire(channel.id, lambda: build_system(self.cfg, self.static_prompt))
            try:
                async with channel.typing():
                    turn = await self.collect_turn(message, text)
                    result = await run_turn(self.anthropic, self.cfg, approver, conv, turn.blocks, turn.size_chars)
            except Exception:
                log.exception("turn failed channel=%s", channel.id)
                result = TurnResult("Internal error; see bot logs.", False)
            self.conversations.touch(conv)
        reply = f"{notice}\n\n{result.text}" if notice else result.text
        for chunk in split_message(reply):
            await channel.send(chunk)

    async def collect_turn(self, message: discord.Message, text: str) -> UserTurn:
        parts = []
        budget = MAX_MESSAGE_INLINE_BYTES
        for attachment in message.attachments:
            attachment_parts, inlined_bytes = await self.collect_attachment(message.channel.id, attachment, budget)
            parts.extend(attachment_parts)
            budget -= inlined_bytes
        return build_turn(text, parts)

    async def collect_attachment(self, channel_id: int, attachment: discord.Attachment, budget: int) -> tuple[list, int]:
        name = attachment.filename
        reason = rejection(name, attachment.size)
        if reason:
            log.info("attachment rejected channel=%s name=%s reason=%s", channel_id, name, reason)
            return rejected_parts(name, reason), 0
        try:
            data = await attachment.read()
        except discord.HTTPException:
            log.exception("attachment download failed channel=%s name=%s", channel_id, name)
            return rejected_parts(name, "download failed"), 0
        path = inbox.save(self.cfg.repo_root, channel_id, name, data)
        log.info("attachment saved channel=%s path=%s bytes=%s", channel_id, path, len(data))
        parts = saved_parts(name, path, classify(name), data, budget)
        return parts, len(data) if binary_inlined(parts) else 0

    async def approve(self, channel, summary: str, files: dict[str, str]) -> bool:
        return await request_approval(channel, self.cfg.allowed_user_ids, summary, files)
