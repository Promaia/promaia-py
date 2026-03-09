"""
Discord platform implementation.

Implements the BaseMessagingPlatform interface for Discord.
"""

from typing import Dict, Any, Optional, List
import asyncio
import logging

from .base import BaseMessagingPlatform, MessageMetadata

logger = logging.getLogger(__name__)

# Lazy import for discord - only loaded when DiscordPlatform is instantiated
discord = None


def _ensure_discord_imported():
    """Ensure discord.py is imported. Raises ImportError if not available."""
    global discord
    if discord is None:
        try:
            import discord as discord_module
            discord = discord_module
        except ImportError:
            raise ImportError(
                "Discord integration requires discord.py\n"
                "Install with: pip install discord.py"
            )


class DiscordPlatform(BaseMessagingPlatform):
    """Discord messaging platform implementation."""
    
    def __init__(self, bot_token: str, bot_instance=None):
        """
        Initialize Discord platform.

        Args:
            bot_token: Discord bot token
            bot_instance: Optional existing discord.Client/Bot to reuse (avoids double login)
        """
        super().__init__()

        # Ensure discord.py is available
        _ensure_discord_imported()

        self.platform_name = 'discord'
        self.bot_token = bot_token
        self.client = bot_instance  # Reuse the bot's client if provided

        self.logger.info("Discord platform initialized")

    async def _ensure_client(self):
        """Lazy initialize Discord client (only if no bot_instance was provided)."""
        if self.client is None:
            intents = discord.Intents.default()
            intents.message_content = True
            intents.guilds = True
            intents.guild_messages = True

            self.client = discord.Client(intents=intents)

            try:
                await self.client.login(self.bot_token)
                self.logger.info("Discord client connected")
            except Exception as e:
                self.logger.error(f"Failed to connect Discord client: {e}")
                raise
    
    async def send_message(
        self,
        channel_id: str,
        content: str,
        thread_id: Optional[str] = None,
        blocks: Optional[List[Dict]] = None
    ) -> MessageMetadata:
        """
        Send message to Discord channel.
        
        Args:
            channel_id: Discord channel ID
            content: Message text
            thread_id: Optional message ID to reply to (creates thread)
            blocks: Ignored (Discord doesn't use blocks like Slack)
        
        Returns:
            MessageMetadata with message info
        """
        await self._ensure_client()
        
        try:
            # For Discord threads, thread_id IS a channel ID — send there directly.
            # When channel_id == thread_id (set by bot.py), this fetches the thread channel.
            target_channel_id = thread_id or channel_id
            channel = await self.client.fetch_channel(int(target_channel_id))
            msg = await channel.send(content)
            
            return MessageMetadata(
                message_id=str(msg.id),
                channel_id=str(msg.channel.id),
                user_id=str(msg.author.id),
                username=msg.author.name,
                timestamp=msg.created_at.isoformat(),
                thread_id=thread_id or str(msg.id),
                platform='discord'
            )
        
        except Exception as e:
            self.logger.error(f"Error sending Discord message: {e}", exc_info=True)
            raise
    
    async def send_typing_indicator(self, channel_id: str) -> None:
        """
        Show Discord typing indicator.
        
        Discord has a built-in typing indicator that shows for ~10 seconds.
        """
        await self._ensure_client()
        
        try:
            channel = await self.client.fetch_channel(int(channel_id))
            async with channel.typing():
                # Show typing for 1 second
                await asyncio.sleep(1)
        except Exception as e:
            self.logger.warning(f"Failed to show typing indicator: {e}")
    
    async def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        """
        Get Discord channel information.
        
        Args:
            channel_id: Discord channel ID
        
        Returns:
            Channel metadata
        """
        await self._ensure_client()
        
        try:
            channel = await self.client.fetch_channel(int(channel_id))
            
            return {
                'id': str(channel.id),
                'name': channel.name,
                'type': str(channel.type),
                'guild_id': str(channel.guild.id) if hasattr(channel, 'guild') else None,
                'guild_name': channel.guild.name if hasattr(channel, 'guild') else None
            }
        
        except Exception as e:
            self.logger.error(f"Error getting Discord channel info: {e}")
            return {'id': channel_id, 'name': 'Unknown', 'error': str(e)}
    
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """
        Get Discord user information.
        
        Args:
            user_id: Discord user ID
        
        Returns:
            User metadata
        """
        await self._ensure_client()
        
        try:
            user = await self.client.fetch_user(int(user_id))
            
            return {
                'id': str(user.id),
                'name': user.name,
                'discriminator': user.discriminator,
                'display_name': user.display_name,
                'bot': user.bot
            }
        
        except Exception as e:
            self.logger.error(f"Error getting Discord user info: {e}")
            return {'id': user_id, 'name': 'Unknown', 'error': str(e)}
    
    async def edit_message(
        self,
        channel_id: str,
        message_id: str,
        content: str,
        thread_id: Optional[str] = None
    ) -> None:
        """Edit an existing Discord message."""
        await self._ensure_client()
        try:
            channel = await self.client.fetch_channel(int(thread_id or channel_id))
            message = await channel.fetch_message(int(message_id))
            await message.edit(content=content)
        except Exception as e:
            self.logger.error(f"Error editing Discord message: {e}", exc_info=True)
            raise

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        """Delete a Discord message."""
        await self._ensure_client()
        try:
            channel = await self.client.fetch_channel(int(channel_id))
            message = await channel.fetch_message(int(message_id))
            await message.delete()
        except Exception as e:
            self.logger.error(f"Error deleting Discord message: {e}", exc_info=True)
            raise

    async def create_thread(
        self,
        channel_id: str,
        message_id: str,
        name: Optional[str] = None
    ) -> str:
        """Create a Discord thread from a message. Returns thread channel ID."""
        await self._ensure_client()
        try:
            channel = await self.client.fetch_channel(int(channel_id))
            message = await channel.fetch_message(int(message_id))
            thread = await message.create_thread(name=name or "Promaia")
            return str(thread.id)
        except Exception as e:
            self.logger.error(f"Error creating Discord thread: {e}", exc_info=True)
            raise

    async def get_thread_messages(
        self,
        channel_id: str,
        thread_id: str
    ) -> List[Dict[str, Any]]:
        """Get all messages in a Discord thread."""
        await self._ensure_client()
        try:
            thread_channel = await self.client.fetch_channel(int(thread_id))
            messages = []
            async for msg in thread_channel.history(limit=100):
                messages.append({
                    'user_id': str(msg.author.id),
                    'text': msg.content,
                    'timestamp': msg.created_at.isoformat(),
                })
            messages.reverse()  # Oldest first
            return messages
        except Exception as e:
            self.logger.error(f"Error getting Discord thread messages: {e}", exc_info=True)
            raise

    async def add_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str
    ) -> None:
        """Add an emoji reaction to a Discord message."""
        await self._ensure_client()
        try:
            channel = await self.client.fetch_channel(int(channel_id))
            message = await channel.fetch_message(int(message_id))
            await message.add_reaction(emoji)
        except Exception as e:
            self.logger.error(f"Error adding Discord reaction: {e}", exc_info=True)
            raise

    async def remove_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str
    ) -> None:
        """Remove the bot's emoji reaction from a Discord message."""
        await self._ensure_client()
        try:
            channel = await self.client.fetch_channel(int(channel_id))
            message = await channel.fetch_message(int(message_id))
            await message.remove_reaction(emoji, self.client.user)
        except Exception as e:
            self.logger.debug(f"Error removing Discord reaction: {e}")

    def format_message(
        self,
        content: str,
        agent_name: Optional[str] = None
    ) -> str:
        """
        Format message for Discord using Discord markdown.
        
        Discord uses ** for bold, * for italic, ` for code.
        
        Args:
            content: Message content
            agent_name: Optional agent name for header
        
        Returns:
            Formatted message
        """
        if agent_name:
            # Discord uses ** for bold
            return f"**{agent_name}**\n\n{content}"
        return content
    
    async def cleanup(self):
        """Clean up Discord client connection."""
        if self.client and not self.client.is_closed():
            await self.client.close()
            self.logger.info("Discord client closed")
