import logging

import discord

import config
from agents import monitor
from orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class IgorBot(discord.Client):
    """Discord interface for I.G.O.R. — Phase 1.

    Accepts DMs only. All messages pass through the orchestrator's security
    check before any processing occurs. Unauthorized user IDs are silently
    dropped — no response, no acknowledgment.

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
        # Fresh orchestrator on every connection — resets session context per spec
        self._orchestrator = Orchestrator(notify=self.send_to_user)
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
            response = await self._orchestrator.process(message.author.id, message.content)
        except Exception as e:
            logger.error("Unhandled error in message processing — %s: %s", type(e).__name__, e)
            return

        if response is not None:
            try:
                await self._send_chunked(message.channel, response)
            except Exception as e:
                logger.error("Failed to send response — %s: %s", type(e).__name__, e)

    async def _send_chunked(self, channel: discord.abc.Messageable, content: str) -> None:
        """Split and send content in ≤2000-character chunks (Discord's message limit)."""
        limit = 2000
        for i in range(0, len(content), limit):
            await channel.send(content[i:i + limit])

    async def send_to_user(self, content: str) -> None:
        """Send a message to the authorized user's DM channel.

        Used by orchestrator rate-limit notifications and monitor scheduled reports.
        Falls back to fetching a fresh DM channel if the cached one is stale.
        """
        if self._dm_channel is not None:
            try:
                await self._send_chunked(self._dm_channel, content)
                return
            except discord.HTTPException:
                self._dm_channel = None

        try:
            user = await self.fetch_user(config.AUTHORIZED_USER_ID)
            dm = await user.create_dm()
            await self._send_chunked(dm, content)
            self._dm_channel = dm
        except Exception as e:
            logger.error("Failed to send message to user — %s: %s", type(e).__name__, e)


async def run_bot() -> None:
    bot = IgorBot()
    await bot.start(config.DISCORD_BOT_TOKEN)
