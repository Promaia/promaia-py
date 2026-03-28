"""
Slack bot for conversational AI.

Listens to Slack events using Socket Mode and routes messages to the
platform-agnostic conversation manager. Supports tag-to-chat: @mention
creates a thread with a structured response loop (batching, countdown,
typing detection, pause/stop controls).
"""

import os
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Lazy imports
slack_bolt = None
slack_sdk = None


def _ensure_slack_imported():
    """Ensure Slack libraries are imported."""
    global slack_bolt, slack_sdk
    if slack_bolt is None:
        try:
            import slack_bolt
            import slack_sdk
        except ImportError:
            raise ImportError(
                "Slack bot requires slack-bolt and slack-sdk\n"
                "Install with: pip install slack-bolt slack-sdk"
            )


def parse_agent_request(text: str, available_agents: list, bot_user_id: str) -> tuple:
    """
    Extract agent name from message.

    Supports formats like:
        "@promaia grace what's up?" -> ("grace", "what's up?")
        "hey promaia thomas help" -> ("thomas", "help")
        "@promaia what's 2+2?" -> (None, "what's 2+2?")

    Args:
        text: Message text
        available_agents: List of valid agent names
        bot_user_id: Slack bot user ID for @mention removal

    Returns:
        (agent_name or None, cleaned_query)
    """
    # Remove bot mentions
    cleaned = text.replace(f'<@{bot_user_id}>', '').strip()

    # Lowercase for matching
    cleaned_lower = cleaned.lower()

    # Try to find agent name at start of message
    # First pass: exact full name match
    for agent_name in available_agents:
        agent_lower = agent_name.lower()

        # Patterns: "grace what's...", "grace, what's...", "grace? what's..."
        patterns = [
            f"{agent_lower} ",      # "grace what's..."
            f"{agent_lower}, ",     # "grace, what's..."
            f"{agent_lower}? ",     # "grace? what's..."
            f"{agent_lower}\n",     # "grace\nwhat's..."
        ]

        for pattern in patterns:
            if cleaned_lower.startswith(pattern):
                # Extract query after agent name
                query = cleaned[len(pattern):].strip()
                return (agent_name, query)

        # Exact match (just agent name)
        if cleaned_lower == agent_lower:
            return (agent_name, "")

    # Second pass: prefix match (e.g. "beacon" matches "beacon-2727")
    first_word = cleaned_lower.split()[0] if cleaned_lower.split() else ""
    if first_word:
        matches = [a for a in available_agents if a.lower().startswith(first_word)]
        if len(matches) == 1:
            agent_name = matches[0]
            # Remove the first word and return the rest as query
            rest = cleaned[len(first_word):].strip().lstrip("?,").strip()
            return (agent_name, rest if rest else "")

    # No agent specified - return original cleaned text
    return (None, cleaned)


def select_agent(requested_agent: str | None, available_agents: list, default_agent: str) -> str | None:
    """
    Determine which agent to use with smart fallback.

    Priority:
    1. Requested agent (if valid)
    2. Only agent (if only one exists)
    3. Default agent (from config)

    Args:
        requested_agent: Agent name from user message
        available_agents: List of valid agent names
        default_agent: Default agent name

    Returns:
        Agent name to use, or None if requested agent invalid
    """
    # If agent requested, validate it exists
    if requested_agent:
        if requested_agent in available_agents:
            return requested_agent
        else:
            # Invalid agent - caller should return error
            return None

    # If only one agent exists, use it
    if len(available_agents) == 1:
        return available_agents[0]

    # Use default
    return default_agent


async def _save_dm_to_history(conv_manager, state):
    """Save a DM conversation to the unified content database for recall.

    Converts the ConversationState messages into markdown and stores
    in conversation_content table (same as terminal chat history).
    Skips if incognito or empty.
    """
    if not state or not state.messages:
        return
    if state.context and state.context.get("incognito"):
        logger.info(f"Skipping save for incognito conversation {state.conversation_id}")
        return

    try:
        from promaia.storage.hybrid_storage import get_hybrid_registry

        # Build markdown from messages
        user_name = (state.context or {}).get("user_name", "user")
        lines = [
            f"# DM: {state.platform} with {user_name}",
            f"Date: {(state.created_at or '')[:10]}",
            f"Platform: {state.platform}",
            "",
        ]
        for msg in state.messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Extract text from structured content blocks
                text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                content = "\n".join(text_parts)
            if isinstance(content, str) and content.strip():
                speaker = user_name if role == "user" else "Maia"
                lines.append(f"**{speaker}**: {content}")

        markdown = "\n\n".join(lines)

        # Generate title from first user message
        first_user_msg = next(
            (m["content"] for m in state.messages
             if m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"].strip()),
            "DM conversation",
        )
        title = f"DM with {user_name}: {first_user_msg[:60]}"

        # Get workspace from agent config
        agent = conv_manager._get_cached_agent(state.agent_id)
        workspace = agent.workspace if agent else "default"

        # Save to unified storage
        registry = get_hybrid_registry()

        now = datetime.now(timezone.utc).isoformat()
        # Write markdown file
        from promaia.utils.env_writer import get_data_dir
        md_dir = get_data_dir() / "data" / "md" / "conversation" / workspace / "convos"
        md_dir.mkdir(parents=True, exist_ok=True)
        md_path = md_dir / f"{state.conversation_id}.md"
        md_path.write_text(markdown, encoding="utf-8")

        content_data = {
            "page_id": state.conversation_id,
            "title": title,
            "content": markdown,
            "workspace": workspace,
            "database_id": "convos",
            "database_name": "convos",
            "file_path": str(md_path),
            "synced_time": now,
            "created_time": state.created_at or now,
            "last_edited_time": state.last_message_at or now,
        }
        metadata = {
            "thread_id": state.conversation_id,
            "message_count": len(state.messages),
            "context_type": "dm",
            "data_source": "conversation",
            "content_type": "conversation",
        }

        registry.add_conversation_content(content_data, metadata)
        logger.info(f"Saved DM conversation {state.conversation_id} ({len(state.messages)} messages)")

    except Exception as e:
        logger.error(f"Failed to save DM conversation: {e}", exc_info=True)


def create_slack_bot():
    """
    Create and configure Slack bot with conversation routing.

    Returns:
        Configured Slack Bolt app
    """
    _ensure_slack_imported()

    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from promaia.agents.conversation_manager import ConversationManager, ConversationState
    from promaia.agents.messaging.slack_platform import SlackPlatform
    from promaia.agents.tag_to_chat import TagToChatLoop

    # Get tokens: try auth module first, fall back to env vars
    bot_token = None
    app_token = None
    try:
        from promaia.auth.registry import get_integration
        from promaia.config.workspaces import get_workspace_manager
        slack_int = get_integration("slack")
        ws = get_workspace_manager().get_default_workspace()
        creds = slack_int.get_slack_credentials(ws)
        if creds:
            bot_token = creds.get("bot_token")
            app_token = creds.get("app_token")
    except Exception:
        pass

    # Fall back to environment variables
    if not bot_token:
        bot_token = os.environ.get("SLACK_BOT_TOKEN")
    if not app_token:
        app_token = os.environ.get("SLACK_APP_TOKEN")

    if not bot_token or not app_token:
        raise ValueError(
            "Slack tokens not configured. Run 'maia setup' or set\n"
            "SLACK_BOT_TOKEN and SLACK_APP_TOKEN in your .env file."
        )

    # Create Slack app (async version)
    app = AsyncApp(token=bot_token)

    # Load available agents
    from promaia.agents.agent_config import load_agents
    agents = load_agents()
    available_agent_names = [a.name or a.agent_id for a in agents if a.name or a.agent_id]

    if not available_agent_names:
        logger.warning("No agents configured! Bot will have limited functionality.")
        available_agent_names = []

    # Set default agent (first agent or 'grace' if exists)
    default_agent = 'grace' if 'grace' in available_agent_names else (
        available_agent_names[0] if available_agent_names else None
    )

    logger.info(f"Available agents: {', '.join(available_agent_names)}")
    logger.info(f"Default agent: {default_agent}")

    # Create conversation manager
    conv_manager = ConversationManager()

    # Register Slack platform
    slack_platform = SlackPlatform(bot_token=bot_token)
    conv_manager.register_platform('slack', slack_platform)

    logger.info("Slack bot initialized with conversation manager")

    # ── Tag-to-chat loop registry ───────────────────────────────────────
    # In-memory dict of active loops, keyed by thread_id (Slack ts)
    active_loops: Dict[str, TagToChatLoop] = {}
    _bot_user_id: Optional[str] = None  # cached bot user ID

    def _cleanup_loop(thread_id: str):
        """Remove loop from registry when it goes dormant/stopped."""
        active_loops.pop(thread_id, None)

    async def _get_username(client, user_id: str) -> str:
        """Resolve Slack user ID to display name."""
        try:
            info = await client.users_info(user=user_id)
            user = info['user']
            return (
                user.get('profile', {}).get('display_name')
                or user.get('real_name')
                or user.get('name')
                or user_id
            )
        except Exception:
            return user_id

    def _start_loop(
        conversation_id: str,
        channel_id: str,
        thread_id: str,
        agent_id: str,
        is_wake: bool = False,
        dm_key: str = None,
    ) -> TagToChatLoop:
        """Create and register a TagToChatLoop, return it (caller starts it)."""
        loop = TagToChatLoop(
            conversation_id=conversation_id,
            channel_id=channel_id,
            thread_id=thread_id,
            platform='slack',
            agent_id=agent_id,
            platform_impl=slack_platform,
            conv_manager=conv_manager,
            is_wake=is_wake,
        )
        loop_key = dm_key or thread_id
        active_loops[loop_key] = loop
        loop.on_done(lambda: active_loops.pop(loop_key, None))
        return loop

    async def _wake_dormant_thread(
        thread_id: str,
        channel_id: str,
    ) -> Optional[TagToChatLoop]:
        """Load dormant conversation from DB, create new loop, return it."""
        conv = await conv_manager.get_tag_to_chat_conversation(
            platform='slack',
            thread_id=thread_id,
        )
        if not conv:
            return None

        loop = _start_loop(
            conversation_id=conv.conversation_id,
            channel_id=channel_id,
            thread_id=thread_id,
            agent_id=conv.agent_id,
            is_wake=True,
        )
        # Restore thread parent message ID for title updates
        if conv.context and conv.context.get('thread_parent_message_id'):
            loop.thread_parent_message_id = conv.context['thread_parent_message_id']
        logger.info(f"Woke dormant thread {thread_id[:12]} -> {conv.conversation_id[:20]}")
        return loop

    # ── Event handlers ──────────────────────────────────────────────────

    @app.message()
    async def handle_message(message, say, client):
        """
        Handle incoming Slack messages.

        Routes to:
        1. Active tag-to-chat loop (if message is in a tracked thread)
        2. Dormant tag-to-chat thread (wake it up)
        3. Existing direct conversation (legacy behavior)
        """
        try:
            # Skip bot messages
            if message.get('bot_id'):
                return

            # Skip messages without text
            if not message.get('text'):
                return

            # Skip @mentions in channels — handled by handle_app_mention
            # But in DMs, process everything (no app_mention event fires for DMs)
            nonlocal _bot_user_id
            if _bot_user_id is None:
                auth_result = await client.auth_test()
                _bot_user_id = auth_result['user_id']

            channel_id = message['channel']
            is_1on1_dm = channel_id.startswith('D')  # D = 1-on-1 DM, G = group DM (mpim)

            if not is_1on1_dm and f'<@{_bot_user_id}>' in message.get('text', ''):
                return  # Channel/group @mention — let handle_app_mention deal with it
            user_id = message['user']
            text = message['text']
            # Strip bot @mention from 1-on-1 DMs
            if is_1on1_dm and _bot_user_id:
                text = text.replace(f'<@{_bot_user_id}>', '').strip()
            thread_ts = message.get('thread_ts')

            logger.info(f"Message from {user_id} in {channel_id}: {text[:50]}...")

            # 0. Handle /new command in DMs — save + reset conversation
            if is_1on1_dm and text.strip().lower() in ('/new', '/reset', '/clear'):
                # Save the current conversation before resetting
                try:
                    conv = await conv_manager.get_active_conversation(
                        platform='slack', channel_id=channel_id, user_id=user_id
                    )
                    if conv and conv.messages:
                        was_incognito = (conv.context or {}).get("incognito", False)
                        await _save_dm_to_history(conv_manager, conv)
                        if was_incognito:
                            await say(text="🕶️ Incognito conversation ended — nothing was saved.\n💬 Starting fresh!")
                        else:
                            await say(text="💬 Conversation saved. Starting fresh!")
                    else:
                        await say(text="Starting fresh! What can I help with?")
                except Exception:
                    await say(text="Starting fresh! What can I help with?")
                active_loops.pop(channel_id, None)
                return

            # 1. Active tag-to-chat loop? Feed message directly.
            # Check thread_ts for threaded conversations, channel_id for DMs
            loop_key = thread_ts if thread_ts else (channel_id if is_1on1_dm else None)
            if loop_key and loop_key in active_loops:
                loop = active_loops[loop_key]
                if loop.state.status != "stopped":
                    username = await _get_username(client, user_id)
                    loop.add_message(
                        user_id=user_id,
                        username=username,
                        text=text,
                        timestamp=message['ts'],
                    )
                    logger.info(f"Fed message to active loop for {loop_key[:12]}")
                    return

            # 2. Dormant tag-to-chat thread? Wake it up.
            if thread_ts:
                loop = await _wake_dormant_thread(thread_ts, channel_id)
                if loop:
                    username = await _get_username(client, user_id)
                    loop.add_message(
                        user_id=user_id,
                        username=username,
                        text=text,
                        timestamp=message['ts'],
                    )
                    asyncio.create_task(loop.run())
                    logger.info(f"Woke dormant thread {thread_ts[:12]} with new message")
                    return

            # 3. DMs: tag-to-chat with conversation persistence
            if is_1on1_dm and default_agent:
                username = await _get_username(client, user_id)

                # Check for existing DM conversation to resume
                conversation = await conv_manager.get_active_conversation(
                    platform='slack',
                    channel_id=channel_id,
                    user_id=user_id
                )

                # 30-minute timeout: if last message was >30min ago, start fresh
                DM_TIMEOUT_SECONDS = 1800
                if conversation and conversation.last_message_at:
                    try:
                        from datetime import datetime as _dt
                        last = _dt.fromisoformat(conversation.last_message_at.replace('Z', '+00:00'))
                        gap = (datetime.now(timezone.utc) - last).total_seconds()
                        if gap > DM_TIMEOUT_SECONDS:
                            logger.info(f"DM conversation timed out ({int(gap)}s), starting fresh")
                            # Save the timed-out conversation before dropping it
                            await _save_dm_to_history(conv_manager, conversation)
                            conversation = None  # will create new below
                    except Exception:
                        pass

                conv_id = conversation.conversation_id if conversation else f"slack_dm_{channel_id}_{int(datetime.now(timezone.utc).timestamp())}"
                agent_id = conversation.agent_id if conversation else default_agent

                if not conversation:
                    # New DM — create conversation state
                    logger.info(f"New DM from {user_id} ({username}), starting conversation with {agent_id}")
                    now = datetime.now(timezone.utc).isoformat()
                    dm_conversation = ConversationState(
                        conversation_id=conv_id,
                        agent_id=agent_id,
                        platform='slack',
                        channel_id=channel_id,
                        user_id=user_id,
                        thread_id=None,
                        status='active',
                        last_message_at=now,
                        messages=[],
                        context={"is_dm": True, "user_name": username},
                        timeout_seconds=DM_TIMEOUT_SECONDS,
                        max_turns=None,
                        created_at=now,
                        conversation_type='tag_to_chat',
                    )
                    await conv_manager._save_state(dm_conversation)

                loop = _start_loop(
                    conversation_id=conv_id,
                    channel_id=channel_id,
                    thread_id=None,
                    agent_id=agent_id,
                    dm_key=channel_id,
                )

                username = await _get_username(client, user_id)
                loop.add_message(
                    user_id=user_id,
                    username=username,
                    text=text,
                    timestamp=message['ts'],
                )

                asyncio.create_task(loop.run())
                logger.info(f"DM conversation via tag-to-chat: {conv_id}")

            # 4. Non-DM without active loop — channel message without @mention
            else:
                logger.debug(f"No active conversation for user {user_id} in {channel_id}")

        except Exception as e:
            logger.error(f"Error handling Slack message: {e}", exc_info=True)

    @app.event("app_mention")
    async def handle_app_mention(event, say, client):
        """
        Handle direct @mentions of the bot.

        Creates a tag-to-chat thread and starts the response loop.
        If already in a thread with an active loop, feeds the message.
        """
        try:
            channel_id = event['channel']
            user_id = event['user']
            text = event['text']
            event_ts = event['ts']
            thread_ts = event.get('thread_ts')

            # Get bot user ID for parsing
            auth_result = await client.auth_test()
            bot_user_id = auth_result['user_id']

            logger.info(f"Bot mentioned in {channel_id} by {user_id}: {text[:50]}...")

            # Parse agent name from message
            requested_agent, query = parse_agent_request(text, available_agent_names, bot_user_id)

            # Handle special commands (respond inline, not in thread)
            if query.lower() in ['list agents', 'who are you', 'who are you?', 'list agents?']:
                if available_agent_names:
                    agent_list = ", ".join(available_agent_names)
                    await say(f"Available agents: {agent_list}\n\nDefault agent: {default_agent}")
                else:
                    await say("No agents are currently configured.")
                return

            if not query:
                if thread_ts:
                    # In a thread with empty @mention — treat as re-engagement
                    query = "hey"
                else:
                    # Top-level empty @mention — nudge user
                    await say(
                        "Tag me with a message and I'll reply in a thread! "
                        "Try `@promaia what's on my calendar?`\n\n"
                        "Or use `/agent` to pick a specific agent."
                    )
                    return

            # Select agent to use
            agent_to_use = select_agent(requested_agent, available_agent_names, default_agent)

            if requested_agent and not agent_to_use:
                agent_list = ", ".join(available_agent_names)
                await say(f"I don't know the agent '{requested_agent}'. Available agents: {agent_list}")
                return

            if not agent_to_use:
                await say("No agents are configured. Please set up an agent first.")
                return

            logger.info(f"Routing to agent: {agent_to_use}")

            # If @tagged in an existing thread with an active loop, feed message
            effective_thread = thread_ts or event_ts
            if effective_thread in active_loops:
                loop = active_loops[effective_thread]
                username = await _get_username(client, user_id)
                loop.add_message(
                    user_id=user_id,
                    username=username,
                    text=query,
                    timestamp=event_ts,
                )
                # If stopped, restart the loop
                if loop.state.status == "stopped":
                    loop.state.status = "active"
                    loop._stop_requested = False
                    asyncio.create_task(loop.run())
                    logger.info(f"Re-engaged stopped thread {effective_thread[:12]} via @tag")
                return

            # If @tagged in a stopped thread, wake it back up
            if thread_ts:
                conv = await conv_manager.get_tag_to_chat_conversation(
                    platform='slack', thread_id=thread_ts
                )
                if not conv:
                    # Check for stopped threads too
                    import sqlite3
                    with sqlite3.connect(conv_manager.db_path) as conn:
                        conn.row_factory = sqlite3.Row
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT * FROM conversations
                            WHERE platform = 'slack' AND thread_id = ?
                            AND conversation_type = 'tag_to_chat'
                            AND status = 'stopped'
                            ORDER BY created_at DESC LIMIT 1
                        """, (thread_ts,))
                        row = cursor.fetchone()
                        if row:
                            conv = conv_manager._row_to_state(dict(row))

                if conv:
                    loop = _start_loop(
                        conversation_id=conv.conversation_id,
                        channel_id=channel_id,
                        thread_id=thread_ts,
                        agent_id=agent_to_use,
                    )
                    if conv.context and conv.context.get('thread_parent_message_id'):
                        loop.thread_parent_message_id = conv.context['thread_parent_message_id']
                    username = await _get_username(client, user_id)
                    loop.add_message(
                        user_id=user_id,
                        username=username,
                        text=query,
                        timestamp=event_ts,
                    )
                    asyncio.create_task(loop.run())
                    logger.info(f"Re-engaged thread {thread_ts[:12]} via @tag")
                    return

            # New @mention — create tag-to-chat conversation with thread
            # The mention message itself becomes the thread parent
            thread_id = event_ts

            now = datetime.now(timezone.utc).isoformat()
            conversation_id = f"slack_t2c_{channel_id}_{int(datetime.now(timezone.utc).timestamp())}"

            # Fetch channel context: name + recent messages
            mention_context = {}
            try:
                channel_info = await client.conversations_info(channel=channel_id)
                channel_name = channel_info.get("channel", {}).get("name", "")
                if channel_name:
                    mention_context["channel_name"] = channel_name

                # Fetch recent channel messages for context
                history = await client.conversations_history(channel=channel_id, limit=50)
                if history.get("messages"):
                    lines = []
                    for msg in reversed(history["messages"][:50]):
                        msg_user = msg.get("user", "")
                        msg_text = msg.get("text", "").strip()
                        if msg_text:
                            lines.append(f"<@{msg_user}>: {msg_text}")
                    if lines:
                        mention_context["recent_messages"] = "\n".join(lines)
            except Exception as e:
                logger.debug(f"Could not fetch channel context: {e}")

            username = await _get_username(client, user_id)
            mention_context["user_name"] = username

            conversation = ConversationState(
                conversation_id=conversation_id,
                agent_id=agent_to_use,
                platform='slack',
                channel_id=channel_id,
                user_id=user_id,
                thread_id=thread_id,
                status='active',
                last_message_at=now,
                messages=[],
                context=mention_context,
                timeout_seconds=30 * 60,
                max_turns=None,
                created_at=now,
                conversation_type='tag_to_chat',
            )
            await conv_manager._save_state(conversation)
            logger.info(f"Created tag-to-chat conversation {conversation_id} with agent {agent_to_use}")

            # Start the response loop
            loop = _start_loop(
                conversation_id=conversation_id,
                channel_id=channel_id,
                thread_id=thread_id,
                agent_id=agent_to_use,
            )

            username = await _get_username(client, user_id)
            loop.add_message(
                user_id=user_id,
                username=username,
                text=query,
                timestamp=event_ts,
            )

            asyncio.create_task(loop.run())
            logger.info(f"Tag-to-chat loop started for thread {thread_id[:12]}")

        except Exception as e:
            logger.error(f"Error handling app mention: {e}", exc_info=True)
            try:
                await say("Sorry, I encountered an error processing your request.")
            except:
                pass

    @app.event({"type": "message", "subtype": "message_changed"})
    async def handle_message_changed(event, client):
        """Acknowledge message_changed events (from our edits) to suppress warnings."""
        pass

    # NOTE: reaction_added handler is defined below, after /maia command,
    # as handle_reaction_added_v2 (handles both agent picks and t2c controls)

    @app.command("/promaia-reset")
    async def handle_reset_command(ack, command, respond):
        """
        Handle conversation reset slash command.

        Usage: /promaia-reset
        """
        await ack()

        try:
            channel_id = command['channel_id']
            user_id = command['user_id']

            # Check for active conversation
            conversation = await conv_manager.get_active_conversation(
                platform='slack',
                channel_id=channel_id,
                user_id=user_id
            )

            if conversation:
                await conv_manager.end_conversation(
                    conversation.conversation_id,
                    reason="user_reset"
                )
                await respond("Conversation reset! Start fresh anytime.")
            else:
                await respond("No active conversation to reset.")

        except Exception as e:
            logger.error(f"Error handling reset command: {e}", exc_info=True)
            await respond("Sorry, I encountered an error resetting the conversation.")

    @app.command("/promaia-status")
    async def handle_status_command(ack, command, respond):
        """
        Handle conversation status slash command.

        Usage: /promaia-status
        """
        await ack()

        try:
            channel_id = command['channel_id']
            user_id = command['user_id']

            # Check for active conversation
            conversation = await conv_manager.get_active_conversation(
                platform='slack',
                channel_id=channel_id,
                user_id=user_id
            )

            if conversation:
                response = (
                    f"*Active Conversation*\n"
                    f"- Agent: {conversation.agent_id}\n"
                    f"- Turn: {conversation.turn_count}\n"
                    f"- Status: {conversation.status}\n"
                    f"- Type: {conversation.conversation_type}\n"
                    f"- Started: {conversation.created_at}\n"
                )

                if conversation.max_turns:
                    response += f"- Max turns: {conversation.max_turns}\n"

                await respond(response)
            else:
                await respond("No active conversation.")

        except Exception as e:
            logger.error(f"Error handling status command: {e}", exc_info=True)
            await respond("Sorry, I encountered an error getting conversation status.")

    # ── /maia command ────────────────────────────────────────────────────

    AGENT_EMOJIS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    AGENT_EMOJI_UNICODE = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
    @app.command("/incognito")
    async def handle_incognito_command(ack, command, respond):
        """Toggle incognito mode for the current DM conversation."""
        await ack()
        try:
            channel_id = command["channel_id"]
            user_id = command["user_id"]

            # Only works in DMs
            if not channel_id.startswith("D"):
                await respond("🕶️ Incognito only works in DMs — channel messages are synced separately.")
                return

            conv = await conv_manager.get_active_conversation(
                platform="slack", channel_id=channel_id, user_id=user_id
            )
            if conv:
                is_incognito = (conv.context or {}).get("incognito", False)
                if is_incognito:
                    conv.context["incognito"] = False
                    await conv_manager._save_state(conv)
                    await respond("💬 Incognito off — this conversation will be saved.")
                else:
                    conv.context["incognito"] = True
                    await conv_manager._save_state(conv)
                    await respond("🕶️ Incognito — this conversation won't be saved. Resets next conversation.")
            else:
                await respond("No active conversation. Start chatting first, then use /incognito.")
        except Exception as e:
            logger.error(f"Error handling /incognito: {e}", exc_info=True)
            await respond("Sorry, something went wrong.")

    # Track pending agent-pick messages: {message_ts: {emoji_name: agent_name}}
    _agent_pick_messages: Dict[str, Dict[str, str]] = {}

    @app.command("/agent")
    async def handle_agent_command(ack, command, client):
        """
        Handle /agent slash command.

        Usage:
            /agent  — list agents and pick one to start a conversation
        """
        await ack()

        try:
            subcommand = command.get('text', '').strip().lower()
            channel_id = command['channel_id']
            user_id = command['user_id']

            if subcommand in ('', 'list'):
                if not available_agent_names:
                    await client.chat_postEphemeral(
                        channel=channel_id, user=user_id,
                        text="No agents configured."
                    )
                    return

                # Build agent list message
                lines = ["*Choose an agent to chat with:*\n"]
                emoji_to_agent = {}
                for i, agent_name in enumerate(available_agent_names):
                    if i >= len(AGENT_EMOJIS):
                        break
                    lines.append(f"{i + 1}. {agent_name}")
                    emoji_to_agent[AGENT_EMOJIS[i]] = agent_name

                # Post as a visible message (not ephemeral) so reactions work
                response = await client.chat_postMessage(
                    channel=channel_id,
                    text="\n".join(lines),
                )
                msg_ts = response['ts']

                # Add number reactions
                for i in range(min(len(available_agent_names), len(AGENT_EMOJIS))):
                    try:
                        await client.reactions_add(
                            channel=channel_id,
                            timestamp=msg_ts,
                            name=AGENT_EMOJIS[i],
                        )
                    except Exception:
                        pass

                # Track this message for reaction handling
                _agent_pick_messages[msg_ts] = emoji_to_agent
                logger.info(f"/maia agents posted in {channel_id}, tracking {msg_ts}")

            else:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id,
                    text="Usage: `/agent` — pick an agent to chat with"
                )

        except Exception as e:
            logger.error(f"Error handling /maia command: {e}", exc_info=True)

    @app.event("reaction_added")
    async def handle_reaction_added(event, client):
        """Handle emoji reactions — agent picks and tag-to-chat controls."""
        try:
            emoji = event['reaction']
            item = event['item']
            message_ts = item['ts']
            channel_id = item['channel']
            user_id = event['user']

            # Check if this is an agent pick reaction
            if message_ts in _agent_pick_messages:
                agent_map = _agent_pick_messages[message_ts]
                if emoji in agent_map:
                    agent_name = agent_map[emoji]

                    # Delete the pick message
                    try:
                        await client.chat_delete(channel=channel_id, ts=message_ts)
                    except Exception:
                        pass
                    del _agent_pick_messages[message_ts]

                    # Post a starter message that becomes the thread parent
                    username = await _get_username(client, user_id)
                    starter = await client.chat_postMessage(
                        channel=channel_id,
                        text=f"_{username} started a conversation with *{agent_name}*_",
                    )
                    thread_id = starter['ts']

                    # Create the conversation and loop
                    now = datetime.now(timezone.utc).isoformat()
                    conversation_id = f"slack_t2c_{channel_id}_{int(datetime.now(timezone.utc).timestamp())}"

                    conversation = ConversationState(
                        conversation_id=conversation_id,
                        agent_id=agent_name,
                        platform='slack',
                        channel_id=channel_id,
                        user_id=user_id,
                        thread_id=thread_id,
                        status='active',
                        last_message_at=now,
                        messages=[],
                        context={'thread_parent_message_id': thread_id},
                        timeout_seconds=30 * 60,
                        max_turns=None,
                        created_at=now,
                        conversation_type='tag_to_chat',
                    )
                    await conv_manager._save_state(conversation)

                    loop = _start_loop(
                        conversation_id=conversation_id,
                        channel_id=channel_id,
                        thread_id=thread_id,
                        agent_id=agent_name,
                    )
                    loop.thread_parent_message_id = thread_id

                    # Add a greeting message to kick things off
                    loop.add_message(
                        user_id=user_id,
                        username=username,
                        text=f"hey {agent_name}",
                        timestamp=thread_id,
                    )
                    asyncio.create_task(loop.run())
                    logger.info(f"Agent pick: {agent_name} by {user_id} in {channel_id}")
                return

            # Otherwise handle tag-to-chat control reactions (🛑 only)
            if emoji != 'octagonal_sign':
                return

            for loop in active_loops.values():
                if loop.state.temp_message_id == message_ts:
                    await loop.handle_cancel(user_id)
                    logger.info(f"Cancel triggered by {user_id} on thread {loop.state.thread_id[:12]}")
                    break

        except Exception as e:
            logger.error(f"Error handling reaction: {e}", exc_info=True)

    return app


async def start_slack_bot_async():
    """
    Start the Slack bot asynchronously.

    This function:
    1. Creates the bot app
    2. Sets up Socket Mode handler
    3. Starts listening for events
    """
    _ensure_slack_imported()

    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    handler = None
    try:
        app = create_slack_bot()

        # Get app token from auth module or env
        app_token = None
        try:
            from promaia.auth.registry import get_integration
            from promaia.config.workspaces import get_workspace_manager
            slack_int = get_integration("slack")
            ws = get_workspace_manager().get_default_workspace()
            creds = slack_int.get_slack_credentials(ws)
            if creds:
                app_token = creds.get("app_token")
        except Exception:
            pass
        if not app_token:
            app_token = os.environ.get("SLACK_APP_TOKEN")

        # Create async Socket Mode handler
        handler = AsyncSocketModeHandler(app, app_token)

        logger.info("Starting Slack bot in Socket Mode...")
        logger.info("Bot is ready to receive messages!")

        # Start bot (this blocks until interrupted)
        await handler.start_async()

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Slack bot stopped")
    except Exception as e:
        logger.error(f"Error starting Slack bot: {e}", exc_info=True)
        raise
    finally:
        # Gracefully close the Socket Mode handler so its internal
        # aiohttp WebSocket session is properly shut down.
        if handler:
            try:
                await handler.close_async()
            except Exception:
                pass


def start_slack_bot():
    """Synchronous wrapper to start the async bot."""
    asyncio.run(start_slack_bot_async())


if __name__ == "__main__":
    # Load environment variables from .env file
    from dotenv import load_dotenv
    load_dotenv()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    start_slack_bot()
