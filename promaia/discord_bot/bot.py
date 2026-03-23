"""
Discord bot for Promaia - Interactive AI assistant in Discord servers.

This module implements a Discord bot that can:
- Listen for mentions and commands
- Process messages with Promaia's AI
- Respond in Discord channels
- Maintain conversation context
"""
import os
import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pathlib import Path

try:
    import discord
    from discord import app_commands
    from discord.ext import commands
except ImportError:
    print("Discord bot requires discord.py")
    print("Install with: pip install discord.py")
    raise

logger = logging.getLogger(__name__)


def parse_agent_request(text: str, available_agents: list) -> tuple:
    """
    Extract agent name from message.

    Supports formats like:
        "@promaia grace what's up?" -> ("grace", "what's up?")
        "grace, what's..." -> ("grace", "what's...")
        "@promaia what's 2+2?" -> (None, "what's 2+2?")

    Returns:
        (agent_name or None, cleaned_query)
    """
    cleaned = text.strip()
    cleaned_lower = cleaned.lower()

    # First pass: exact full name match at start
    for agent_name in available_agents:
        agent_lower = agent_name.lower()
        for pattern in [f"{agent_lower} ", f"{agent_lower}, ", f"{agent_lower}? ", f"{agent_lower}\n"]:
            if cleaned_lower.startswith(pattern):
                query = cleaned[len(pattern):].strip()
                return (agent_name, query)
        if cleaned_lower == agent_lower:
            return (agent_name, "")

    # Second pass: prefix match (e.g. "beacon" matches "beacon-2727")
    first_word = cleaned_lower.split()[0] if cleaned_lower.split() else ""
    if first_word:
        matches = [a for a in available_agents if a.lower().startswith(first_word)]
        if len(matches) == 1:
            agent_name = matches[0]
            rest = cleaned[len(first_word):].strip().lstrip("?,").strip()
            return (agent_name, rest if rest else "")

    return (None, cleaned)


def select_agent(requested_agent: str | None, available_agents: list, default_agent: str) -> str | None:
    """
    Determine which agent to use with smart fallback.

    Priority: requested > only-agent > default.
    Returns agent name to use, or None if requested agent is invalid.
    """
    if requested_agent:
        if requested_agent in available_agents:
            return requested_agent
        return None
    if len(available_agents) == 1:
        return available_agents[0]
    return default_agent


class AgentSelectView(discord.ui.View):
    """Dropdown menu for picking an agent via /agent slash command."""

    def __init__(self, agents: List[str], bot: "PromaiaBot"):
        super().__init__(timeout=60)
        self.bot = bot
        options = [
            discord.SelectOption(label=name, value=name)
            for name in agents[:25]  # Discord Select limit
        ]
        self.select = discord.ui.Select(
            placeholder="Choose an agent...",
            options=options,
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        agent_name = self.select.values[0]
        channel = interaction.channel

        try:
            # Post starter message in the channel
            username = interaction.user.display_name
            starter_msg = await channel.send(
                f"*{username} started a conversation with {agent_name}*"
            )

            # Create a thread from the starter message
            thread = await starter_msg.create_thread(
                name=f"Promaia - {username}"
            )
            thread_id = str(thread.id)
            parent_channel_id = str(channel.id)
            starter_msg_id = str(starter_msg.id)

            from promaia.agents.tag_to_chat import TagToChatLoop
            from promaia.agents.conversation_manager import ConversationState

            now = datetime.now(timezone.utc).isoformat()
            conversation_id = f"discord_t2c_{parent_channel_id}_{int(datetime.now(timezone.utc).timestamp())}"

            conv_state = ConversationState(
                conversation_id=conversation_id,
                agent_id=agent_name,
                platform='discord',
                channel_id=parent_channel_id,
                user_id=str(interaction.user.id),
                thread_id=thread_id,
                status='active',
                last_message_at=now,
                messages=[],
                context={'thread_parent_message_id': starter_msg_id},
                timeout_seconds=30 * 60,
                max_turns=None,
                created_at=now,
                conversation_type='tag_to_chat',
            )
            await self.bot.conv_manager._save_state(conv_state)

            loop = TagToChatLoop(
                conversation_id=conversation_id,
                channel_id=thread_id,
                thread_id=thread_id,
                platform='discord',
                agent_id=agent_name,
                platform_impl=self.bot.discord_platform,
                conv_manager=self.bot.conv_manager,
            )
            self.bot.active_loops[thread_id] = loop
            loop.on_done(lambda tid=thread_id: self.bot.active_loops.pop(tid, None))

            # Set parent message tracking for thread title updates
            loop.thread_parent_message_id = starter_msg_id
            loop.thread_parent_channel_id = parent_channel_id

            # Kick off the conversation with a greeting
            loop.add_message(
                user_id=str(interaction.user.id),
                username=username,
                text=f"hey {agent_name}",
                timestamp=starter_msg_id,
            )
            asyncio.create_task(loop.run())

            # Confirm to the user (edit the ephemeral picker)
            await interaction.response.edit_message(
                content=f"Started conversation with **{agent_name}**!",
                view=None,
            )
            logger.info(f"/agent: {agent_name} by {interaction.user.id} in {parent_channel_id}")

        except Exception as e:
            logger.error(f"/agent select failed: {e}", exc_info=True)
            try:
                await interaction.response.edit_message(
                    content="Something went wrong starting the conversation.",
                    view=None,
                )
            except discord.errors.InteractionResponded:
                await interaction.followup.send(
                    "Something went wrong starting the conversation.",
                    ephemeral=True,
                )

    async def on_timeout(self):
        """Disable the select menu when it expires."""
        self.select.disabled = True
        self.stop()


@app_commands.command(name="agent", description="Start a conversation with a Promaia agent")
async def agent_command(interaction: discord.Interaction):
    bot = interaction.client
    if not hasattr(bot, 'available_agent_names') or not bot.available_agent_names:
        await interaction.response.send_message(
            "No agents are configured.", ephemeral=True
        )
        return

    if not bot.discord_platform:
        await interaction.response.send_message(
            "Discord platform not initialized yet. Try again in a moment.",
            ephemeral=True,
        )
        return

    view = AgentSelectView(bot.available_agent_names, bot)
    await interaction.response.send_message(
        "Pick an agent to chat with:", view=view, ephemeral=True
    )


class PromaiaBot(commands.Bot):
    """Discord bot for Promaia AI assistant."""

    def __init__(self, workspace: str = "koii", **kwargs):
        """
        Initialize Promaia Discord bot.

        Args:
            workspace: Workspace to use for credentials and context
            **kwargs: Additional arguments passed to commands.Bot
        """
        # Set up intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.guild_messages = True
        intents.members = True
        intents.typing = True  # Needed for tag-to-chat typing detection

        # Initialize bot with command prefix
        super().__init__(
            command_prefix=self._get_prefix,
            intents=intents,
            **kwargs
        )

        self.workspace = workspace
        self.config = self._load_config()

        # Track conversation context per channel (legacy)
        self.conversation_context: Dict[int, List[Dict]] = {}

        # Initialize unified conversation manager
        from promaia.agents.conversation_manager import ConversationManager
        from promaia.agents.messaging.discord_platform import DiscordPlatform
        
        self.conv_manager = ConversationManager()

        # Register Discord platform (will be initialized with actual token when bot starts)
        self.discord_platform = None  # Lazy init after bot login

        # Tag-to-chat loop registry: thread_id -> TagToChatLoop
        self.active_loops: Dict[str, Any] = {}

        # Load available agents for tag-to-chat routing
        from promaia.agents.agent_config import load_agents
        agents_list = load_agents()
        self.available_agent_names = [a.agent_id or a.name for a in agents_list if a.agent_id or a.name]
        self.default_agent = 'grace' if 'grace' in self.available_agent_names else (
            self.available_agent_names[0] if self.available_agent_names else None
        )

    async def setup_hook(self):
        """Register slash commands before connecting to Discord."""
        self.tree.add_command(agent_command)

    def _load_config(self) -> Dict[str, Any]:
        """Load bot configuration from credentials file."""
        from promaia.utils.env_writer import get_data_dir
        config_path = get_data_dir() / "credentials" / self.workspace / "discord_credentials.json"

        if not config_path.exists():
            raise FileNotFoundError(
                f"Discord credentials not found at {config_path}\n"
                f"Run: maia workspace discord-setup {self.workspace}"
            )

        with open(config_path) as f:
            return json.load(f)

    async def _get_prefix(self, bot, message):
        """
        Determine command prefix dynamically.
        Supports: !maia, @mention, or 'maia' keyword
        """
        prefixes = ['!maia ', '!promaia ', 'maia ', 'promaia ']
        return prefixes

    async def on_ready(self):
        """Called when bot successfully connects to Discord."""
        logger.info(f"Promaia bot logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} servers")

        # Sync slash commands with Discord
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} slash commands")
        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}", exc_info=True)

        # Initialize Discord platform for conversation manager
        from promaia.agents.messaging.discord_platform import DiscordPlatform
        
        if not self.discord_platform:
            bot_token = os.environ.get('DISCORD_BOT_TOKEN') or self.config.get('bot_token')
            if bot_token:
                # Pass self (the bot) as bot_instance so the platform reuses
                # our already-connected client instead of creating a second one
                self.discord_platform = DiscordPlatform(bot_token=bot_token, bot_instance=self)
                self.conv_manager.register_platform('discord', self.discord_platform)
                logger.info("Discord platform registered with conversation manager")

        # Set bot status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="@mentions and !maia commands"
            )
        )

    async def on_message(self, message: discord.Message):
        """
        Handle incoming messages.

        Routes to:
        1. Active tag-to-chat loop (if message is in a tracked thread)
        2. Dormant tag-to-chat thread (wake it up)
        3. @mention or command -> tag-to-chat thread creation
        4. Legacy behavior for direct conversations
        """
        # Ignore messages from the bot itself
        if message.author == self.user:
            return

        # Ignore messages from other bots
        if message.author.bot:
            return

        thread_id = None
        # Check if message is in a thread
        if isinstance(message.channel, discord.Thread):
            thread_id = str(message.channel.id)

        # 1. Active tag-to-chat loop? Feed message directly.
        if thread_id and thread_id in self.active_loops:
            loop = self.active_loops[thread_id]
            if loop.state.status != "stopped":
                loop.add_message(
                    user_id=str(message.author.id),
                    username=message.author.display_name,
                    text=message.content,
                    timestamp=str(message.id),
                )
                return

        # 2. Dormant tag-to-chat thread? Wake it up.
        if thread_id:
            conv = await self.conv_manager.get_tag_to_chat_conversation(
                platform='discord',
                thread_id=thread_id,
            )
            if conv:
                from promaia.agents.tag_to_chat import TagToChatLoop
                # For Discord, thread IS a channel — use thread_id as channel_id
                loop = TagToChatLoop(
                    conversation_id=conv.conversation_id,
                    channel_id=thread_id,
                    thread_id=thread_id,
                    platform='discord',
                    agent_id=conv.agent_id,
                    platform_impl=self.discord_platform,
                    conv_manager=self.conv_manager,
                    is_wake=True,
                )
                self.active_loops[thread_id] = loop
                loop.on_done(lambda tid=thread_id: self.active_loops.pop(tid, None))
                loop.add_message(
                    user_id=str(message.author.id),
                    username=message.author.display_name,
                    text=message.content,
                    timestamp=str(message.id),
                )
                asyncio.create_task(loop.run())
                logger.info(f"Woke dormant Discord thread {thread_id}")
                return

        # 3. Check if bot was mentioned or command was used
        is_mention = self.user.mentioned_in(message)
        is_command = message.content.lower().startswith(('!maia', '!promaia', 'maia ', 'promaia '))

        if is_mention or is_command:
            await self._handle_ai_request(message)
            return

        # Process commands
        await self.process_commands(message)

    async def on_typing(self, channel: discord.abc.Messageable, user: discord.User, when: datetime):
        """Handle typing events for tag-to-chat threads."""
        if user == self.user:
            return

        thread_id = None
        if isinstance(channel, discord.Thread):
            thread_id = str(channel.id)

        if thread_id and thread_id in self.active_loops:
            self.active_loops[thread_id].update_typing(str(user.id))

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handle emoji reactions on Promaia's temp messages (pause/stop)."""
        # Ignore own reactions
        if payload.user_id == self.user.id:
            return

        emoji_name = str(payload.emoji)
        # Match base codepoints (ignore variation selectors \ufe0f)
        # ⏭️ = fast forward, 🛑 = pause, 👋 = leave thread
        base = emoji_name.replace('\ufe0f', '')
        if base != '\U0001f6d1':  # 🛑 only
            return

        message_id = str(payload.message_id)
        user_id = str(payload.user_id)

        for loop in self.active_loops.values():
            if loop.state.temp_message_id == message_id:
                await loop.handle_cancel(user_id)
                break

    async def _handle_ai_request(self, message: discord.Message):
        """
        Process a message that requests AI assistance.

        For @mentions: creates a Discord thread and starts a tag-to-chat loop.
        For messages in existing threads: feeds to the active loop.
        Falls back to legacy behavior for direct conversations.

        Args:
            message: Discord message to process
        """
        try:
            # Extract the actual query (remove mention/command prefix)
            query = message.content
            query = query.replace(f'<@{self.user.id}>', '').strip()
            query = query.replace(f'<@!{self.user.id}>', '').strip()

            for prefix in ['!maia', '!promaia', 'maia', 'promaia']:
                if query.lower().startswith(prefix):
                    query = query[len(prefix):].strip()
                    break

            # Parse agent name from query (e.g. "grace what's up?" -> agent=grace)
            requested_agent, query = parse_agent_request(query, self.available_agent_names)

            if not query:
                if isinstance(message.channel, discord.Thread):
                    # In a thread with empty @mention — treat as re-engagement
                    query = "hey"
                else:
                    # Top-level empty @mention — nudge user
                    await message.reply(
                        "Tag me with a message and I'll reply in a thread! "
                        "Try `@promaia what's on my calendar?`"
                    )
                    return

            # Select agent to use
            agent_to_use = select_agent(requested_agent, self.available_agent_names, self.default_agent)

            if requested_agent and not agent_to_use:
                agent_list = ", ".join(self.available_agent_names)
                await message.reply(
                    f"I don't have an agent called **{requested_agent}**. "
                    f"Available agents: {agent_list}"
                )
                return

            # If in a thread with an active loop, just feed the message
            if isinstance(message.channel, discord.Thread):
                thread_id = str(message.channel.id)
                if thread_id in self.active_loops:
                    loop = self.active_loops[thread_id]
                    loop.add_message(
                        user_id=str(message.author.id),
                        username=message.author.display_name,
                        text=query,
                        timestamp=str(message.id),
                    )
                    # If stopped, restart via @tag
                    if loop.state.status == "stopped":
                        loop.state.status = "active"
                        loop._stop_requested = False
                        asyncio.create_task(loop.run())
                        logger.info(f"Re-engaged stopped thread {thread_id} via @tag")
                    return

            # If @tagged in a stopped/dormant thread, wake it back up
            if isinstance(message.channel, discord.Thread):
                thread_id = str(message.channel.id)
                conv = await self.conv_manager.get_tag_to_chat_conversation(
                    platform='discord', thread_id=thread_id
                )
                if not conv:
                    # Check for stopped threads too
                    import sqlite3
                    with sqlite3.connect(self.conv_manager.db_path) as conn:
                        conn.row_factory = sqlite3.Row
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT * FROM conversations
                            WHERE platform = 'discord' AND thread_id = ?
                            AND conversation_type = 'tag_to_chat'
                            AND status = 'stopped'
                            ORDER BY created_at DESC LIMIT 1
                        """, (thread_id,))
                        row = cursor.fetchone()
                        if row:
                            conv = self.conv_manager._row_to_state(dict(row))
                if conv:
                    from promaia.agents.tag_to_chat import TagToChatLoop
                    loop = TagToChatLoop(
                        conversation_id=conv.conversation_id,
                        channel_id=thread_id,
                        thread_id=thread_id,
                        platform='discord',
                        agent_id=agent_to_use,
                        platform_impl=self.discord_platform,
                        conv_manager=self.conv_manager,
                    )
                    self.active_loops[thread_id] = loop
                    loop.on_done(lambda tid=thread_id: self.active_loops.pop(tid, None))
                    loop.add_message(
                        user_id=str(message.author.id),
                        username=message.author.display_name,
                        text=query,
                        timestamp=str(message.id),
                    )
                    asyncio.create_task(loop.run())
                    logger.info(f"Re-engaged thread {thread_id} via @tag")
                    return

            # Check for existing direct conversation (unified manager)
            conversation = await self.conv_manager.get_active_conversation(
                platform='discord',
                channel_id=str(message.channel.id),
                user_id=str(message.author.id)
            )

            if conversation and conversation.conversation_type == 'direct':
                # Process through unified conversation manager (legacy direct path)
                logger.info(f"Processing message in direct conversation {conversation.conversation_id}")
                async with message.channel.typing():
                    response = await self.conv_manager.handle_user_message(
                        conversation_id=conversation.conversation_id,
                        user_message=query,
                        user_id=str(message.author.id)
                    )
                if response:
                    if len(response) > 2000:
                        for chunk in self._split_message(response):
                            await message.reply(chunk)
                    else:
                        await message.reply(response)
                return

            # New @mention — create thread and start tag-to-chat loop
            agent_id = agent_to_use
            if not agent_id:
                await message.reply("No agents are configured.")
                return

            if not self.discord_platform:
                await message.reply("Discord platform not initialized yet.")
                return

            from promaia.agents.tag_to_chat import TagToChatLoop
            from promaia.agents.conversation_manager import ConversationState

            # Create a Discord thread from the mention message
            thread = await message.create_thread(name=f"Promaia - {message.author.display_name}")
            thread_id = str(thread.id)
            parent_channel_id = str(message.channel.id)

            now = datetime.now(timezone.utc).isoformat()
            conversation_id = f"discord_t2c_{parent_channel_id}_{int(datetime.now(timezone.utc).timestamp())}"

            conv_state = ConversationState(
                conversation_id=conversation_id,
                agent_id=agent_id,
                platform='discord',
                channel_id=parent_channel_id,
                user_id=str(message.author.id),
                thread_id=thread_id,
                status='active',
                last_message_at=now,
                messages=[],
                context={},
                timeout_seconds=30 * 60,
                max_turns=None,
                created_at=now,
                conversation_type='tag_to_chat',
            )
            await self.conv_manager._save_state(conv_state)

            # For Discord, thread IS a channel — use thread_id as channel_id
            # so all platform operations (send, edit, delete, react) target the thread
            loop = TagToChatLoop(
                conversation_id=conversation_id,
                channel_id=thread_id,
                thread_id=thread_id,
                platform='discord',
                agent_id=agent_id,
                platform_impl=self.discord_platform,
                conv_manager=self.conv_manager,
            )
            self.active_loops[thread_id] = loop
            loop.on_done(lambda tid=thread_id: self.active_loops.pop(tid, None))

            loop.add_message(
                user_id=str(message.author.id),
                username=message.author.display_name,
                text=query,
                timestamp=str(message.id),
            )

            asyncio.create_task(loop.run())
            logger.info(f"Tag-to-chat thread created: {thread_id} for agent {agent_id}")

        except Exception as e:
            logger.error(f"Error handling AI request: {e}", exc_info=True)
            await message.reply("Sorry, I encountered an error processing your request.")

    async def _get_ai_response(self, query: str, channel_id: int, message: discord.Message) -> str:
        """
        Get AI response for a query with channel context.

        Args:
            query: User's question/request
            channel_id: Discord channel ID for context
            message: Original Discord message

        Returns:
            AI generated response
        """
        try:
            # Build context from conversation history
            context_messages = self.conversation_context.get(channel_id, [])

            # Add channel/server context
            context_info = f"Discord Server: {message.guild.name if message.guild else 'DM'}\n"
            context_info += f"Channel: {message.channel.name if hasattr(message.channel, 'name') else 'DM'}\n"
            context_info += f"User: {message.author.name}\n\n"

            # Format conversation history
            history_context = ""
            for msg in context_messages[-5:]:  # Last 5 messages
                role = "User" if msg["role"] == "user" else "Assistant"
                history_context += f"{role}: {msg['content']}\n"

            # Combine contexts
            full_prompt = f"{context_info}\nRecent conversation:\n{history_context}\n\nCurrent query: {query}"

            # Get response from AI interface
            # TODO: Integrate with actual ChatInterface
            # For now, use a simple response
            response = await self._simple_ai_response(query, full_prompt)

            return response

        except Exception as e:
            logger.error(f"Error getting AI response: {e}", exc_info=True)
            return f"I encountered an error processing your request: {str(e)}"

    async def _simple_ai_response(self, query: str, context: str) -> str:
        """
        Generate a simple AI response.
        TODO: Replace with actual ChatInterface integration.

        Args:
            query: User query
            context: Full context including conversation history

        Returns:
            Response string
        """
        # Placeholder implementation
        # In production, this should use promaia.chat.interface.ChatInterface

        if "hello" in query.lower() or "hi" in query.lower():
            return "Hello! I'm Promaia, your AI assistant. How can I help you today?"
        elif "help" in query.lower():
            return (
                "I can help you with:\n"
                "• Answering questions about your data\n"
                "• Summarizing Discord conversations\n"
                "• Searching through your knowledge base\n"
                "• General assistance\n\n"
                "Just mention me or use `!maia <your question>`"
            )
        else:
            return (
                f"I received your query: '{query}'\n\n"
                "Note: Full AI integration is in progress. "
                "I'll soon be able to provide detailed responses!"
            )

    def _split_message(self, text: str, max_length: int = 2000) -> List[str]:
        """
        Split a long message into chunks that fit Discord's character limit.

        Args:
            text: Text to split
            max_length: Maximum length per chunk (default: 2000)

        Returns:
            List of message chunks
        """
        if len(text) <= max_length:
            return [text]

        chunks = []
        current_chunk = ""

        # Split by paragraphs first
        paragraphs = text.split('\n\n')

        for para in paragraphs:
            if len(current_chunk) + len(para) + 2 <= max_length:
                current_chunk += para + '\n\n'
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())

                # If paragraph itself is too long, split by sentences
                if len(para) > max_length:
                    sentences = para.split('. ')
                    for sentence in sentences:
                        if len(current_chunk) + len(sentence) + 2 <= max_length:
                            current_chunk += sentence + '. '
                        else:
                            if current_chunk:
                                chunks.append(current_chunk.strip())
                            current_chunk = sentence + '. '
                else:
                    current_chunk = para + '\n\n'

        if current_chunk:
            chunks.append(current_chunk.strip())

        return chunks

    @commands.command(name='ping')
    async def ping(self, ctx):
        """Check if bot is responsive."""
        latency = round(self.latency * 1000)
        await ctx.reply(f"Pong! Latency: {latency}ms")

    @commands.command(name='status')
    async def status(self, ctx):
        """Get bot status and information."""
        embed = discord.Embed(
            title="Promaia Bot Status",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )

        embed.add_field(name="Servers", value=len(self.guilds), inline=True)
        embed.add_field(name="Latency", value=f"{round(self.latency * 1000)}ms", inline=True)
        embed.add_field(name="Workspace", value=self.workspace, inline=True)

        await ctx.reply(embed=embed)

    @commands.command(name='clear')
    async def clear_context(self, ctx):
        """Clear conversation context for this channel."""
        channel_id = ctx.channel.id
        if channel_id in self.conversation_context:
            del self.conversation_context[channel_id]
            await ctx.reply("Conversation context cleared for this channel.")
        else:
            await ctx.reply("No conversation context to clear.")


async def run_bot(workspace: str = "koii", token: Optional[str] = None):
    """
    Start the Promaia Discord bot.

    Args:
        workspace: Workspace to use for configuration
        token: Optional bot token (if not in credentials file)
    """
    bot = PromaiaBot(workspace=workspace)

    # Get token from parameter or config
    if not token:
        token = bot.config.get("bot_token")

    if not token:
        raise ValueError("No Discord bot token provided")

    try:
        await bot.start(token)
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested")
        await bot.close()
    except Exception as e:
        logger.error(f"Bot error: {e}", exc_info=True)
        await bot.close()
        raise


if __name__ == "__main__":
    # For testing: python -m promaia.discord.bot
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    workspace = sys.argv[1] if len(sys.argv) > 1 else "koii"

    print(f"Starting Promaia Discord bot for workspace: {workspace}")
    asyncio.run(run_bot(workspace=workspace))
