import io
import logging
import re

import discord

import config
from agents import monitor
from orchestrator import Orchestrator

logger = logging.getLogger(__name__)


def _filename_from_response(content: str) -> str:
    first_line = content.split("\n")[0].strip()
    if first_line.startswith("#"):
        title = first_line.lstrip("#").strip()
        slug = re.sub(r"[^\w\s-]", "", title.lower())
        slug = re.sub(r"[\s_]+", "-", slug).strip("-")[:60]
        return f"{slug}.md" if slug else "response.md"
    return "response.md"


class IgorBot(discord.Client):
    """Discord interface for I.G.O.R. - Phase 1.

    Accepts DMs only. All messages pass through the orchestrator's security
    check before any processing occurs. Unauthorized user IDs are silently
    dropped - no response, no acknowledgment.

    Note: message_content is a privileged intent and must also be enabled in
    the Discord Developer Portal under Bot > Privileged Gateway Intents.
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self._orchestrator: Orchestrator | None = None
        self._dm_channel: discord.DMChannel | None = None

    async def on_ready(self) -> None:
        logger.info("I.G.O.R. online: %s (ID: %d)", self.user, self.user.id)
        # Fresh orchestrator on every connection - resets session context per spec
        self._orchestrator = Orchestrator(notify=self.send_to_user, notify_file=self.send_file_to_user)
        monitor.setup(send_fn=self.send_to_user)

    async def on_message(self, message: discord.Message) -> None:
        if self.user is None or message.author.id == self.user.id:
            return
        if not isinstance(message.channel, discord.DMChannel):
            return
        if self._orchestrator is None:
            return

        # Cache the DM channel for proactive messages (monitor digests, rate limit notices)
        self._dm_channel = message.channel

        try:
            result = await self._orchestrator.process(message.author.id, message.content)
        except Exception as e:
            logger.error("Unhandled error in message processing - %s: %s", type(e).__name__, e)
            return

        if result is not None:
            response, as_file = result
            try:
                if as_file:
                    await message.channel.send(
                        file=discord.File(io.BytesIO(response.encode()), filename=_filename_from_response(response))
                    )
                else:
                    await self._send_chunked(message.channel, response)
            except Exception as e:
                logger.error("Failed to send response - %s: %s", type(e).__name__, e)

    async def _send_chunked(self, channel: discord.abc.Messageable, content: str) -> None:
        limit = 2000
        if len(content) <= limit:
            await channel.send(content, suppress_embeds=True)
            return
        chunk = ""
        for line in content.split("\n"):
            candidate = chunk + ("\n" if chunk else "") + line
            if len(candidate) > limit:
                if chunk:
                    await channel.send(chunk, suppress_embeds=True)
                chunk = line
            else:
                chunk = candidate
        if chunk:
            await channel.send(chunk, suppress_embeds=True)

    async def _get_dm_channel(self) -> discord.DMChannel | None:
        if self._dm_channel is not None:
            return self._dm_channel
        try:
            user = await self.fetch_user(config.AUTHORIZED_USER_ID)
            self._dm_channel = await user.create_dm()
            return self._dm_channel
        except Exception as e:
            logger.error("Failed to get DM channel - %s: %s", type(e).__name__, e)
            return None

    async def send_to_user(self, content: str) -> None:
        channel = await self._get_dm_channel()
        if channel is None:
            return
        try:
            await self._send_chunked(channel, content)
        except discord.HTTPException:
            self._dm_channel = None
            logger.error("Failed to send message to user")

    async def send_file_to_user(self, content: str) -> None:
        channel = await self._get_dm_channel()
        if channel is None:
            return
        try:
            filename = _filename_from_response(content)
            await channel.send(file=discord.File(io.BytesIO(content.encode()), filename=filename))
        except Exception as e:
            logger.error("Failed to send file to user - %s: %s", type(e).__name__, e)


async def run_bot() -> None:
    bot = IgorBot()
    await bot.start(config.DISCORD_BOT_TOKEN)
