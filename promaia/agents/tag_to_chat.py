"""
Tag-to-Chat response loop.

When someone @mentions Promaia in Slack or Discord, this module manages a
structured response loop that:
- Waits for humans to finish their thought (message batching)
- Detects typing and defers accordingly
- Shows a countdown before responding (with pause/stop controls)
- Uses a lightweight LLM call to decide when to respond
- Manages thread lifecycle: active -> dormant -> woken
"""

import asyncio
import random
import time
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from promaia.agents.messaging.base import BaseMessagingPlatform
    from promaia.agents.conversation_manager import ConversationManager

logger = logging.getLogger(__name__)

# Hidden countdown duration — thinking animation plays during this time
COUNTDOWN_SECONDS = 2

# Emoji pool for thinking animation — randomly sampled each frame
THINKING_EMOJIS = [
    "🧠", "💭", "✨", "🔮", "💡", "⚡", "🌀", "🎯", "🔍", "🌊",
    "🪐", "🌙", "☁️", "🍃", "🦋", "🐚", "🎲", "🧩", "🎪", "🫧",
    "🪄", "🧿", "🌸", "🍄", "🪷", "🕯️", "🫀", "👁️", "🌈", "💫",
    "🔥", "❄️", "🌿", "🪸", "🎭", "🫠", "🧬", "🪩", "💎", "🌻",
    "🐙", "🦊", "🐝", "🦉", "🐋", "🪺", "🌵", "🎵", "🎶", "🔔",
    "🧲", "🪬", "🏮", "🎐", "🎑", "🪻", "🌺", "🍀", "🌾", "🐌",
    "🦎", "🐈‍⬛", "🦜", "🪼", "🫎", "🦔", "🐉", "🪶", "🍂", "🌪️",
    "⭐", "🌟", "💥", "🎆", "🎇", "🧊", "🫗", "🪅", "🎨", "🖌️",
    "📡", "🔭", "🧪", "⚗️", "🧫", "🔬", "💠", "🔷", "♾️", "🌐",
]

# Control emoji names: Slack uses shortcodes, Discord uses Unicode characters
# 🛑 cancels the response and leaves the thread
CONTROL_EMOJIS = {
    'slack': {
        'stop': 'octagonal_sign',
    },
    'discord': {
        'stop': '\U0001f6d1',              # 🛑
    },
}

# Common Slack shortcode -> Unicode mapping for Discord reactions
SHORTCODE_TO_UNICODE = {
    'thumbsup': '\U0001f44d', '+1': '\U0001f44d',
    'thumbsdown': '\U0001f44e', '-1': '\U0001f44e',
    'eyes': '\U0001f440',
    'wave': '\U0001f44b',
    'heart': '\u2764\ufe0f', 'red_heart': '\u2764\ufe0f',
    'fire': '\U0001f525',
    'sparkles': '\u2728',
    'star': '\u2b50',
    'check': '\u2705', 'white_check_mark': '\u2705',
    'x': '\u274c',
    'thinking_face': '\U0001f914', 'thinking': '\U0001f914',
    'ok_hand': '\U0001f44c',
    'clap': '\U0001f44f',
    'raised_hands': '\U0001f64c',
    'pray': '\U0001f64f',
    'muscle': '\U0001f4aa',
    'brain': '\U0001f9e0',
    'saluting_face': '\U0001fae1',
    'rocket': '\U0001f680',
    'tada': '\U0001f389',
    'bulb': '\U0001f4a1',
    'memo': '\U0001f4dd',
    'speech_balloon': '\U0001f4ac',
    '100': '\U0001f4af',
    'handshake': '\U0001f91d',
    'sunglasses': '\U0001f60e',
    'sob': '\U0001f62d',
    'joy': '\U0001f602',
    'smile': '\U0001f604',
    'sweat_smile': '\U0001f605',
    'slightly_smiling_face': '\U0001f642',
    'wink': '\U0001f609',
    'hugging_face': '\U0001f917', 'hugs': '\U0001f917',
    'see_no_evil': '\U0001f648',
    'point_up': '\u261d\ufe0f',
    'point_right': '\U0001f449',
    'point_down': '\U0001f447',
    'raising_hand': '\U0001f64b',
    'skull': '\U0001f480',
    'ghost': '\U0001f47b',
    'purple_heart': '\U0001f49c',
    'blue_heart': '\U0001f499',
    'green_heart': '\U0001f49a',
    'yellow_heart': '\U0001f49b',
    'orange_heart': '\U0001f9e1',
}

# How often the main loop ticks (seconds)
LOOP_TICK_INTERVAL = 1

# Seconds of recent typing activity that counts as "someone is typing"
TYPING_RECENCY = 8

# Ultimate timeout — stop loop if no activity for this long
ULTIMATE_TIMEOUT = 600  # 10 minutes

# Decision prompt for Haiku
DECISION_PROMPT = """You are deciding whether an AI assistant should respond in a thread.

New messages since last response:
{pending_messages}

Someone is currently typing: {typing_status}
Seconds since last message: {seconds_since_last}

Rules:
- If someone asked a question, made a request, or said something: answer_now
- If someone just sent a single word like "wait" or "hold on" and it's been less than 5 seconds: wait
- If it's been more than 5 seconds since the last message: answer_now
- When in doubt: answer_now

Reply with ONLY one of:
- answer_now
- wait"""


@dataclass
class TagToChatState:
    """Per-conversation state for the tag-to-chat response loop."""
    conversation_id: str
    channel_id: str
    thread_id: str
    platform: str
    agent_id: str

    # Timestamps (monotonic time.time())
    last_message_at: float = 0.0
    last_typing_at: Optional[float] = None
    next_check_in: float = 0.0
    ultimate_timeout: float = 0.0

    # Temporary message tracking
    temp_message_id: Optional[str] = None
    animation_frame: int = 0

    # Countdown
    countdown_remaining: Optional[int] = None

    # Message collection
    pending_messages: List[Dict[str, Any]] = field(default_factory=list)

    # Lifecycle: "active", "dormant", "paused", "stopped"
    status: str = "active"

    # DM flag — suppresses leave announcements, etc.
    is_dm: bool = False


class TagToChatLoop:
    """
    The core response loop for tag-to-chat conversations.

    One instance per active thread. Manages the cycle of:
    collecting messages -> deciding to respond -> countdown -> generate -> post.
    """

    def __init__(
        self,
        conversation_id: str,
        channel_id: str,
        thread_id: str,
        platform: str,
        agent_id: str,
        platform_impl: "BaseMessagingPlatform",
        conv_manager: "ConversationManager",
        is_wake: bool = False,
        is_dm: bool = False,
    ):
        self.state = TagToChatState(
            conversation_id=conversation_id,
            channel_id=channel_id,
            thread_id=thread_id,
            platform=platform,
            agent_id=agent_id,
            last_message_at=time.time(),
            ultimate_timeout=time.time() + ULTIMATE_TIMEOUT,
            status="dormant" if is_wake else "active",
            is_dm=is_dm,
        )
        self.platform = platform_impl
        self.conv_manager = conv_manager
        self._loop_task: Optional[asyncio.Task] = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Not paused initially
        self._stop_requested = False
        self._cancelled = False
        self._on_done_callback = None  # Called when loop exits (for cleanup)
        self.thread_parent_message_id: Optional[str] = None  # Editable parent (from /agent)
        self.thread_parent_channel_id: Optional[str] = None  # Parent channel (Discord: starter msg lives here, not in thread)
        self._participants: set = set()  # Display names of thread participants

    def on_done(self, callback):
        """Register a callback to run when the loop exits (dormant/stopped)."""
        self._on_done_callback = callback

    # ── Public API (called from bot event handlers) ─────────────────────

    def add_message(self, user_id: str, username: str, text: str, timestamp: str):
        """Feed a new human message into the loop."""
        now = time.time()
        self.state.pending_messages.append({
            'user_id': user_id,
            'username': username,
            'text': text,
            'timestamp': timestamp,
        })
        self.state.last_message_at = now
        self.state.ultimate_timeout = now + ULTIMATE_TIMEOUT
        self._participants.add(username)

        # If paused, wake up on new message
        if self.state.status == "paused":
            self.state.status = "active"
            self._pause_event.set()
            logger.info(f"[tag2chat:{(self.state.thread_id or self.state.channel_id or "dm")[:12]}] Unpaused by new message")

    def update_typing(self, user_id: str):
        """Record a typing event from a human."""
        self.state.last_typing_at = time.time()

    async def handle_cancel(self, user_id: str):
        """Handle 🛑 reaction — cancel current response, stay in thread."""
        logger.info(f"[tag2chat:{(self.state.thread_id or self.state.channel_id or "dm")[:12]}] Cancelled by user {user_id}")
        self._cancelled = True
        self._pause_event.set()  # Unblock if waiting
        await self._cleanup_temp_message()
        await self._go_dormant()

    # Keep old names as aliases for backward compatibility
    async def handle_pause(self, user_id: str):
        await self.handle_cancel(user_id)

    async def handle_stop(self, user_id: str):
        await self.handle_cancel(user_id)

    # ── Main loop ───────────────────────────────────────────────────────

    async def run(self):
        """Run the response loop. Call via asyncio.create_task()."""
        logger.info(
            f"[tag2chat:{(self.state.thread_id or self.state.channel_id or "dm")[:12]}] Loop started for "
            f"agent={self.state.agent_id} platform={self.state.platform}"
        )
        try:
            while True:
                # Check stop conditions
                if self._stop_requested or self.state.status == "stopped":
                    logger.info(f"[tag2chat:{(self.state.thread_id or self.state.channel_id or "dm")[:12]}] Loop stopped")
                    break

                if time.time() > self.state.ultimate_timeout:
                    logger.info(f"[tag2chat:{(self.state.thread_id or self.state.channel_id or 'dm')[:12]}] Ultimate timeout reached (dm={self.state.is_dm})")
                    # DMs: go dormant silently, stay is_active for resume on next message
                    # Channels: announce leave so users know to re-tag
                    await self._go_dormant(announce=not self.state.is_dm)
                    break

                # If paused, wait for wake-up signal
                if self.state.status == "paused":
                    await self._pause_event.wait()
                    if self._stop_requested:
                        break
                    continue

                # Main tick logic
                await self._tick()

                await asyncio.sleep(LOOP_TICK_INTERVAL)

        except asyncio.CancelledError:
            logger.info(f"[tag2chat:{(self.state.thread_id or self.state.channel_id or "dm")[:12]}] Loop cancelled")
            await self._cleanup_temp_message()
        except Exception as e:
            logger.error(f"[tag2chat:{(self.state.thread_id or self.state.channel_id or "dm")[:12]}] Loop error: {e}", exc_info=True)
            await self._cleanup_temp_message()
            await self._go_dormant()
        finally:
            if self._on_done_callback:
                try:
                    self._on_done_callback()
                except Exception:
                    pass

    async def _tick(self):
        """One iteration of the response loop."""
        now = time.time()

        # Nothing to respond to
        if not self.state.pending_messages:
            return

        # Someone is actively typing — defer
        if self._is_typing_active(now):
            logger.debug(f"[tag2chat:{(self.state.thread_id or self.state.channel_id or "dm")[:12]}] Typing detected, deferring")
            return

        # Scheduled wait hasn't elapsed
        if now < self.state.next_check_in:
            return

        # Skip the Haiku decision call on first response — if someone just
        # @mentioned us, they obviously want a response. Only use the decision
        # call for follow-up messages after the first response.
        if self.state.status == "dormant":
            decision, _ = await self._make_decision()
            thread_key = self.state.thread_id or self.state.channel_id or "dm"
            logger.info(f"[tag2chat:{thread_key[:12]}] Decision: {decision}")

            if decision == "wait":
                self.state.next_check_in = now + 5
                return

        # answer_now (or first response)
        await self._respond()

    # ── Decision call (Haiku) ───────────────────────────────────────────

    async def _make_decision(self) -> tuple:
        """Ask Haiku whether to respond now or wait for more messages.

        Returns (decision, None) where decision is "answer_now" or "wait".
        """
        try:
            from anthropic import Anthropic

            pending_text = "\n".join(
                f"[{m['username']}] {m['text']}"
                for m in self.state.pending_messages
            )
            typing_active = self._is_typing_active(time.time())
            seconds_since_last = int(time.time() - self.state.last_message_at)

            prompt = DECISION_PROMPT.format(
                pending_messages=pending_text,
                typing_status="yes" if typing_active else "no",
                seconds_since_last=seconds_since_last,
            )

            from promaia.utils.ai import get_anthropic_client
            client, prefix = get_anthropic_client()
            if not client:
                return ("answer_now", None)

            response = await asyncio.to_thread(
                client.messages.create,
                model=f"{prefix}claude-haiku-4-5-20251001",
                max_tokens=10,
                messages=[{"role": "user", "content": prompt}],
            )

            text = (response.content[0].text.strip().lower() if response.content else "")
            if "wait" in text:
                return ("wait", None)
            return ("answer_now", None)

        except Exception as e:
            logger.error(f"Decision call failed: {e}", exc_info=True)
            return ("answer_now", None)

    async def _get_thread_context(self) -> str:
        """Get the last few thread messages for decision context."""
        try:
            messages = await self.platform.get_thread_messages(
                channel_id=self.state.channel_id,
                thread_id=self.state.thread_id,
            )
            recent = messages[-5:] if len(messages) > 5 else messages
            return "\n".join(
                f"[{m.get('user_id', '?')}] {m.get('text', '')}"
                for m in recent
            )
        except Exception as e:
            logger.warning(f"Failed to get thread context: {e}")
            return "(thread context unavailable)"

    # ── Response flow: countdown -> thinking -> post ────────────────────

    async def _respond(self):
        """Post thinking message, generate response, post it."""
        self._cancelled = False

        # Post thinking message immediately
        thinking_msg = await self._post_temp_message(
            random.choice(THINKING_EMOJIS)
        )
        if thinking_msg:
            self.state.temp_message_id = thinking_msg
            await asyncio.sleep(0.3)
            # Add control reaction: 🛑 cancel
            emojis = CONTROL_EMOJIS.get(self.state.platform, CONTROL_EMOJIS['slack'])
            try:
                await self.platform.add_reaction(
                    self.state.channel_id, thinking_msg, emojis['stop']
                )
            except Exception:
                pass

        # Generate and respond
        await self._do_thinking_and_respond()

    async def _do_thinking_and_respond(self):
        """Generate response with thinking animation, post it, go dormant.

        The thinking message is already posted by _respond() — this method
        upgrades it with tool activity and generates the response.
        """
        # Shared state: callback updates these, animation task renders them
        plan_steps = []           # List of step text strings
        plan_step_status = []     # "pending" | "in_progress" | "completed"
        plan_active_step = 0      # Currently active step (1-indexed, 0 = none)
        tool_steps = []           # Completed tool summaries
        current_tool = None       # Tool name while executing (None = thinking)
        current_tool_input = {}   # Tool input for current call (for display)

        def _strikethrough(text: str) -> str:
            if self.state.platform == "slack":
                return f"~{text}~"
            return f"~~{text}~~"

        def _render_plan_lines():
            """Build plan display with status indicators."""
            if not plan_steps:
                return []
            lines = ["\U0001f4cb Plan:"]
            for i, step in enumerate(plan_steps):
                status = plan_step_status[i] if i < len(plan_step_status) else "pending"
                if status == "completed":
                    lines.append(f"\u2705 {_strikethrough(step)}")
                elif status == "in_progress":
                    lines.append(f"\u23f3 {step}")
                else:
                    lines.append(f"\u00b7 {step}")
            return lines

        async def on_tool_activity(tool_name, tool_input, completed, summary=None):
            nonlocal current_tool, current_tool_input, plan_active_step

            # Special event: plan was generated
            if tool_name == "__plan__":
                steps = tool_input.get("steps", [])
                plan_steps.clear()
                plan_step_status.clear()
                plan_steps.extend(steps)
                plan_step_status.extend(["pending"] * len(steps))
                plan_active_step = 0
                return

            if tool_name == "__plan_step__":
                step = tool_input.get("step", 1)
                # Mark previous steps as completed
                for i in range(step - 1):
                    if i < len(plan_step_status):
                        plan_step_status[i] = "completed"
                # Mark current step as in_progress
                if 0 < step <= len(plan_step_status):
                    plan_step_status[step - 1] = "in_progress"
                    plan_active_step = step
                return

            if tool_name == "__plan_done__":
                # Mark all steps as completed
                for i in range(len(plan_step_status)):
                    plan_step_status[i] = "completed"
                plan_active_step = 0
                return

            if not completed:
                current_tool = tool_name
                current_tool_input = tool_input or {}
            else:
                # Format like Claude Code: `tool_name`(params) ⎿ result
                from promaia.agents.run_goal import _summarize_tool_input
                params = _summarize_tool_input(tool_name, current_tool_input)
                call_str = f"`{tool_name}` ({params})" if params else f"`{tool_name}`"
                if summary:
                    result = summary[:120] + "..." if len(summary) > 120 else summary
                    tool_steps.append(f"{call_str}\n     ⎿  {result}")
                else:
                    tool_steps.append(call_str)
                current_tool = None
                current_tool_input = {}

        # Unified animation: renders thinking OR tool activity with cycling emoji
        async def animate():
            while True:
                lines = []

                # Plan header (if present) — with dynamic step status
                rendered_plan = _render_plan_lines()
                if rendered_plan:
                    lines.extend(rendered_plan)
                    lines.append("")  # blank line separator

                if tool_steps or current_tool:
                    # Tool activity mode: numbered steps + current activity
                    for i, s in enumerate(tool_steps):
                        lines.append(f"{i+1}. {s}")
                    if current_tool:
                        from promaia.agents.run_goal import _summarize_tool_input
                        params = _summarize_tool_input(current_tool, current_tool_input)
                        tool_label = f"`{current_tool}` ({params})" if params else f"`{current_tool}`"
                        lines.append(
                            f"{len(tool_steps)+1}. {tool_label}... "
                            f"{random.choice(THINKING_EMOJIS)}"
                        )
                    else:
                        # Between tools — LLM is thinking
                        lines.append(
                            f"\n*thinking...* "
                            f"{random.choice(THINKING_EMOJIS)}"
                        )
                elif not rendered_plan:
                    # No tools yet, no plan — standard thinking animation
                    lines.append(random.choice(THINKING_EMOJIS))
                else:
                    # Plan shown but no tools yet
                    lines.append(
                        f"*thinking...* {random.choice(THINKING_EMOJIS)}"
                    )

                content = "\n".join(lines)

                if self.state.temp_message_id:
                    try:
                        await self.platform.edit_message(
                            channel_id=self.state.channel_id,
                            message_id=self.state.temp_message_id,
                            content=content,
                            thread_id=self.state.thread_id,
                        )
                    except Exception:
                        pass
                await asyncio.sleep(0.5)

        animation_task = asyncio.create_task(animate())

        # Generate response
        try:
            response_text = await self._generate_response(
                on_tool_activity=on_tool_activity
            )
        except Exception as e:
            logger.error(f"Response generation failed: {e}", exc_info=True)
            response_text = "Sorry, I encountered an error generating a response."
        finally:
            animation_task.cancel()
            try:
                await animation_task
            except asyncio.CancelledError:
                pass

        # Remove control reactions from thinking message (stop lingering)
        await self._remove_control_reactions(self.state.temp_message_id)

        # Check if cancelled during generation (handle_cancel already announced)
        if self._cancelled or self._stop_requested:
            await self._cleanup_temp_message()
            return

        # Handle activity message based on whether tools were used
        if tool_steps or plan_steps:
            # Tools were used or plan was shown — finalize breadcrumb
            lines = []
            if plan_steps:
                lines.append("\U0001f4cb Plan:")
                for step in plan_steps:
                    lines.append(f"\u2705 {_strikethrough(step)}")
                lines.append("")  # blank separator
            for i, s in enumerate(tool_steps):
                lines.append(f"{i+1}. {s}")
            breadcrumb = "\n".join(lines)
            if self.state.temp_message_id:
                try:
                    await self.platform.edit_message(
                        channel_id=self.state.channel_id,
                        message_id=self.state.temp_message_id,
                        content=breadcrumb,
                        thread_id=self.state.thread_id,
                    )
                except Exception:
                    pass
            # Don't delete — breadcrumb stays as permanent activity log
            self.state.temp_message_id = None
        else:
            # No tools used — delete thinking message (existing behavior)
            await self._cleanup_temp_message()

        # Post actual response as new message (triggers notifications)
        # Discord has a 2000 char limit — split long responses into chunks
        try:
            if self.state.platform == 'discord' and len(response_text) > 2000:
                chunks = self._split_for_discord(response_text)
                for chunk in chunks:
                    await self.platform.send_message(
                        channel_id=self.state.channel_id,
                        content=chunk,
                        thread_id=self.state.thread_id,
                    )
            else:
                await self.platform.send_message(
                    channel_id=self.state.channel_id,
                    content=response_text,
                    thread_id=self.state.thread_id,
                )
            logger.info(
                f"[tag2chat:{(self.state.thread_id or self.state.channel_id or "dm")[:12]}] "
                f"Response posted ({len(response_text)} chars)"
            )
        except Exception as e:
            logger.error(f"Failed to post response: {e}", exc_info=True)

        # Update thread title if we own the parent message
        await self._update_thread_title(response_text)

        # Reset state after successful response
        self.state.pending_messages.clear()
        self.state.ultimate_timeout = time.time() + ULTIMATE_TIMEOUT

        # Go dormant — loop stops, thread stays watched
        await self._go_dormant()

    async def _countdown(self, seconds: int) -> bool:
        """Hidden countdown — checks for interrupts while thinking animation plays.

        Returns True if interrupted (cancel/typing/new message), False if completed.
        """
        countdown_started_at = self.state.last_message_at
        remaining = seconds
        while remaining > 0:
            if self._cancelled or self._stop_requested:
                return True

            if self.state.status in ("paused", "stopped"):
                return True

            if self._is_typing_active(time.time()):
                await self._handle_typing_interrupt()
                return True

            if self.state.last_message_at > countdown_started_at:
                await self._handle_typing_interrupt()
                return True

            await asyncio.sleep(1)
            remaining -= 1

        return False

    async def _handle_typing_interrupt(self):
        """Handle interruption — restart hidden countdown."""
        logger.info(f"[tag2chat:{(self.state.thread_id or self.state.channel_id or "dm")[:12]}] Interrupted, restarting hidden countdown")
        # Run a fresh hidden countdown — if interrupted again, recurse
        interrupted = await self._countdown(COUNTDOWN_SECONDS)
        if not interrupted:
            await self._do_thinking_and_respond()

    # ── Thread title ─────────────────────────────────────────────────────

    async def _update_thread_title(self, response_text: str):
        """Update the thread parent message with a Haiku-generated title + participants."""
        if not self.thread_parent_message_id:
            return

        try:
            from anthropic import Anthropic

            # Build conversation snippet for title generation
            recent = []
            for m in self.state.pending_messages[-3:]:
                recent.append(f"{m['username']}: {m['text']}")
            recent.append(f"{self.state.agent_id}: {response_text[:300]}")
            snippet = "\n".join(recent)

            from promaia.utils.ai import get_anthropic_client
            client, prefix = get_anthropic_client()
            if not client:
                return

            title_resp = await asyncio.to_thread(
                client.messages.create,
                model=f"{prefix}claude-haiku-4-5-20251001",
                max_tokens=30,
                messages=[{
                    "role": "user",
                    "content": (
                        "Generate a brief 3-6 word title for this conversation. "
                        "Reply with ONLY the title, no quotes or formatting.\n\n"
                        f"{snippet}"
                    ),
                }],
            )

            title = title_resp.content[0].text.strip() if title_resp.content else "Conversation"

            # Build participant list
            participant_names = sorted(self._participants | {self.state.agent_id})
            participant_str = ", ".join(participant_names)

            # For Discord /agent threads, the starter message lives in the
            # parent channel, not in the thread itself.
            edit_channel = self.thread_parent_channel_id or self.state.channel_id
            await self.platform.edit_message(
                channel_id=edit_channel,
                message_id=self.thread_parent_message_id,
                content=f"*{title}* — {participant_str}",
            )
            logger.info(f"[tag2chat:{(self.state.thread_id or self.state.channel_id or "dm")[:12]}] Thread titled: {title}")

        except Exception as e:
            logger.warning(f"Failed to update thread title: {e}")

    # ── Response generation ─────────────────────────────────────────────

    async def _sync_channel_to_kb(self):
        """Sync the active channel/DM to KB before generating a response.

        Incremental — only fetches messages from the last 10 minutes.
        If sync fails, logs and continues (don't block response generation).
        """
        if not hasattr(self.platform, 'bot_token'):
            return
        try:
            from promaia.connectors.slack_connector import SlackConnector
            from promaia.storage.unified_storage import get_unified_storage
            from promaia.config.databases import get_database_config

            db_config = get_database_config("slack", workspace=None)
            if not db_config:
                return

            connector = SlackConnector({"database_id": db_config.database_id, "bot_token": self.platform.bot_token})
            storage = get_unified_storage()

            result = await connector.sync_channel(
                channel_id=self.state.channel_id,
                storage=storage,
                db_config=db_config,
            )
            if result and result.pages_saved:
                logger.info(f"[tag2chat] Pre-response sync: {result.pages_saved} new messages in {self.state.channel_id[:12]}")
        except Exception as e:
            logger.debug(f"[tag2chat] Pre-response sync failed (non-fatal): {e}")

    async def _generate_response(self, on_tool_activity=None) -> str:
        """Generate the actual AI response using the conversation manager."""
        try:
            # Sync this channel/DM to KB before generating response
            await self._sync_channel_to_kb()

            # Load context from KB
            thread_context = await self._build_thread_context()

            # Inject channel/DM history as a named source so the agent sees it
            if thread_context:
                state = await self.conv_manager._load_state(self.state.conversation_id)
                if state:
                    source_name = f"slack_{'thread' if self.state.thread_id else 'dm'}"
                    existing_sources = state.context.get('source_states', {})
                    if source_name not in existing_sources:
                        existing_sources[source_name] = {
                            "content": thread_context,
                            "on": True,
                            "page_count": thread_context.count('\n') + 1,
                            "source": "channel_context",
                        }
                        state.context['source_states'] = existing_sources
                        await self.conv_manager._save_state(state)

            response = await self.conv_manager.handle_batched_messages(
                conversation_id=self.state.conversation_id,
                messages=self.state.pending_messages,
                thread_context=thread_context,
                on_tool_activity=on_tool_activity,
                platform=self.platform,
                channel_context={
                    "channel_id": self.state.channel_id,
                    "thread_id": self.state.thread_id,
                },
            )
            return response
        except Exception as e:
            logger.error(f"Failed to generate response: {e}", exc_info=True)
            return "Sorry, I encountered an error generating a response."

    async def _build_thread_context(self) -> Optional[str]:
        """Load conversation context from synced KB (not Slack API).

        For both channels and DMs, loads recent messages from the KB.
        The pre-response sync (_sync_channel_to_kb) ensures data is fresh.
        Messages since the last sync are already in state.messages.
        """
        try:
            from promaia.config.databases import get_database_config
            from promaia.storage.files import load_database_pages_with_filters
            import asyncio as _asyncio, json as _json

            slack_db_config = get_database_config("slack", workspace=None)
            if not slack_db_config:
                return None

            channel_name = await self.platform.get_channel_name(self.state.channel_id)

            pages = await _asyncio.to_thread(
                load_database_pages_with_filters,
                database_config=slack_db_config,
                days=2,
            )

            lines = []
            for page in sorted(pages, key=lambda x: x.get('created_time', '')):
                page_meta = page.get('metadata', {})
                if isinstance(page_meta, str):
                    try:
                        page_meta = _json.loads(page_meta)
                    except Exception:
                        page_meta = {}
                props = page_meta.get('properties', {}) if isinstance(page_meta, dict) else {}
                page_channel = props.get('channel_name', '')
                if page_channel != channel_name:
                    continue

                # For channel threads: also filter by thread_ts if we're in a thread
                if self.state.thread_id:
                    page_thread = props.get('thread_ts', '')
                    page_ts = props.get('ts', '') or page.get('page_id', '').replace('msg_', '')
                    # Include messages that are the thread parent or replies in this thread
                    if page_ts != self.state.thread_id and page_thread != self.state.thread_id:
                        continue

                uname = props.get('username', 'unknown')
                text = page.get('content', '').strip()
                if text:
                    lines.append(f"[{uname}]: {text[:500]}")

            return "\n".join(lines[-100:]) if lines else None
        except Exception as e:
            logger.warning(f"Failed to build thread context: {e}")
            return None

    # ── Thinking animation ──────────────────────────────────────────────

    async def _animate_thinking(self):
        """Random emoji on the thinking message each frame."""
        while True:
            if self.state.temp_message_id:
                try:
                    await self.platform.edit_message(
                        channel_id=self.state.channel_id,
                        message_id=self.state.temp_message_id,
                        content=random.choice(THINKING_EMOJIS),
                        thread_id=self.state.thread_id,
                    )
                except Exception:
                    pass
            await asyncio.sleep(0.5)

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _split_for_discord(text: str, max_length: int = 2000) -> List[str]:
        """Split text into chunks that fit Discord's character limit."""
        if len(text) <= max_length:
            return [text]
        chunks = []
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break
            # Try to split at a paragraph boundary
            cut = text.rfind('\n\n', 0, max_length)
            if cut == -1:
                cut = text.rfind('\n', 0, max_length)
            if cut == -1:
                cut = text.rfind(' ', 0, max_length)
            if cut == -1:
                cut = max_length
            chunks.append(text[:cut].rstrip())
            text = text[cut:].lstrip()
        return chunks

    def _is_typing_active(self, now: float) -> bool:
        """Check if someone was recently typing."""
        if self.state.last_typing_at is None:
            return False
        return (now - self.state.last_typing_at) < TYPING_RECENCY

    async def _post_temp_message(self, content: str) -> Optional[str]:
        """Post a temporary message in the thread. Returns message_id."""
        try:
            meta = await self.platform.send_message(
                channel_id=self.state.channel_id,
                content=content,
                thread_id=self.state.thread_id,
            )
            return meta.message_id
        except Exception as e:
            logger.error(f"Failed to post temp message: {e}")
            return None

    async def _remove_control_reactions(self, message_id: str):
        """Remove bot control reactions from a message."""
        if not message_id:
            return
        emojis = CONTROL_EMOJIS.get(self.state.platform, CONTROL_EMOJIS['slack'])
        for emoji in emojis.values():
            try:
                await self.platform.remove_reaction(
                    self.state.channel_id, message_id, emoji
                )
            except Exception:
                pass

    async def _cleanup_temp_message(self):
        """Delete the current temporary message if it exists."""
        if self.state.temp_message_id:
            try:
                await self.platform.delete_message(
                    channel_id=self.state.channel_id,
                    message_id=self.state.temp_message_id,
                )
            except Exception as e:
                logger.debug(f"Failed to delete temp message: {e}")
            self.state.temp_message_id = None

    async def _react_to_last_message(self, emoji: Optional[str] = None):
        """React to the last pending message with an emoji chosen by Haiku."""
        if not self.state.pending_messages:
            return
        last_ts = self.state.pending_messages[-1].get('timestamp')
        if not last_ts:
            return

        # Clean the emoji name — strip colons, whitespace
        shortcode = (emoji or "eyes").strip().strip(":").lower()
        # Remove any unicode emoji characters, keep only ascii shortcode
        shortcode = ''.join(c for c in shortcode if c.isascii())
        shortcode = shortcode.strip().replace(" ", "_") or "eyes"

        # Convert to platform-appropriate format
        if self.state.platform == 'discord':
            reaction = SHORTCODE_TO_UNICODE.get(shortcode, '\U0001f440')  # fallback: 👀
            fallback = '\U0001f440'
        else:
            reaction = shortcode
            fallback = "eyes"

        try:
            await self.platform.add_reaction(
                self.state.channel_id, last_ts, reaction
            )
            logger.info(f"[tag2chat:{(self.state.thread_id or self.state.channel_id or "dm")[:12]}] Reacted with {reaction}")
        except Exception:
            try:
                await self.platform.add_reaction(
                    self.state.channel_id, last_ts, fallback
                )
            except Exception as e:
                logger.debug(f"Failed to add reaction: {e}")

    async def _announce_leave(self):
        """Post a 'left the chat' message so users know how to re-engage.
        Skipped for DMs — it's a 1-on-1 conversation, no need to announce.
        """
        # Don't post leave messages in DMs — it's a 1-on-1 conversation
        if self.state.is_dm:
            return
        try:
            await self.platform.send_message(
                channel_id=self.state.channel_id,
                content="_Promaia has left the chat, tag promaia in this thread to continue._",
                thread_id=self.state.thread_id,
            )
        except Exception as e:
            logger.debug(f"Failed to post leave message: {e}")

    async def _go_dormant(self, announce: bool = False):
        """Transition to dormant state — loop stops, thread stays watched.

        Args:
            announce: If True, post a "left the chat" message. Used for
                      explicit leave requests and inactivity timeout,
                      but not after a normal response or end_conversation.
        """
        logger.info(f"[tag2chat:{(self.state.thread_id or self.state.channel_id or "dm")[:12]}] Going dormant (announce={announce})")
        self.state.status = "dormant"
        self.state.pending_messages.clear()  # Don't re-evaluate the same messages
        await self._update_db_status("dormant")

        if announce:
            await self._announce_leave()

    async def _update_db_status(self, status: str):
        """Update the conversation status in the database."""
        try:
            state = await self.conv_manager._load_state(self.state.conversation_id)
            if state:
                state.status = status
                await self.conv_manager._save_state(state)
        except Exception as e:
            logger.error(f"Failed to update DB status: {e}")
