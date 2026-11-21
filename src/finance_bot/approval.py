import io
import logging
from typing import Awaitable, Callable

import discord

log = logging.getLogger(__name__)

APPROVAL_TIMEOUT_S = 600
SUMMARY_LIMIT = 1700

Approver = Callable[[str, dict[str, str]], Awaitable[bool]]


class ApprovalView(discord.ui.View):
    def __init__(self, allowed_user_ids: frozenset[int], timeout: float):
        super().__init__(timeout=timeout)
        self.allowed_user_ids = allowed_user_ids
        self.approved: bool | None = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.allowed_user_ids:
            return True
        log.warning("approval click by non-allowed user id=%s", interaction.user.id)
        await interaction.response.send_message("Not authorised.", ephemeral=True)
        return False

    async def _finish(self, interaction: discord.Interaction, approved: bool) -> None:
        self.approved = approved
        self.stop()
        verdict = "Approved" if approved else "Rejected"
        await interaction.response.edit_message(content=f"{interaction.message.content}\n**{verdict}** by <@{interaction.user.id}>", view=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._finish(interaction, True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._finish(interaction, False)

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(content=f"{self.message.content}\n**Timed out (rejected)**", view=None)
        except discord.HTTPException:
            log.warning("could not mark approval message as timed out")


async def request_approval(
    channel: discord.abc.Messageable,
    allowed_user_ids: frozenset[int],
    summary: str,
    files: dict[str, str],
) -> bool:
    view = ApprovalView(allowed_user_ids, APPROVAL_TIMEOUT_S)
    attachments = [discord.File(io.BytesIO(text.encode()), filename=name) for name, text in files.items()]
    view.message = await channel.send(content=summary[:SUMMARY_LIMIT], files=attachments, view=view)
    await view.wait()
    approved = view.approved is True
    log.info("approval result=%s summary=%r", approved, summary[:80])
    return approved
