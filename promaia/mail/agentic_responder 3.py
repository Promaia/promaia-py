"""
Agentic Mail Responder - replaces the simple generate-one-draft pipeline.

Uses agentic_turn() to give the mail responder access to tools:
query workspace data, search Gmail, create multiple drafts, send Slack messages.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from promaia.agents.agentic_turn import (
    AgenticTurnResult,
    ToolExecutor,
    agentic_turn,
    QUERY_TOOL_DEFINITIONS,
    GMAIL_READ_TOOL_DEFINITIONS,
    GMAIL_TOOL_DEFINITIONS,
    GOOGLE_SHEETS_TOOL_DEFINITIONS,
    MESSAGING_TOOL_DEFINITIONS,
)
from promaia.mail.draft_manager import DraftManager

logger = logging.getLogger(__name__)


# ── Tool definition for the maia mail review queue ────────────────────────

ADD_TO_REVIEW_QUEUE_TOOL = {
    "name": "add_to_maia_mail_review_queue",
    "description": (
        "Create an email draft for the user to review in their maia mail queue before sending. "
        "The user will see the draft, can edit it, and confirm before it is sent. "
        "Call multiple times to create multiple drafts from one email."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email(s), comma-separated"
            },
            "subject": {
                "type": "string",
                "description": "Email subject line"
            },
            "body": {
                "type": "string",
                "description": "Email body text"
            },
            "cc": {
                "type": "string",
                "description": "CC recipients, comma-separated"
            },
            "action": {
                "type": "string",
                "enum": ["reply", "forward", "new"],
                "description": (
                    "reply = threaded reply to the sender, "
                    "forward = threaded to a different recipient, "
                    "new = fresh email (no thread association)"
                )
            },
        },
        "required": ["to", "subject", "body", "action"]
    }
}


# ── Mail tool executor ───────────────────────────────────────────────────

class MailToolExecutor(ToolExecutor):
    """Routes tool calls for the mail agentic responder.

    Intercepts add_to_maia_mail_review_queue to save drafts to SQLite.
    Blocks send_email and reply_to_email (drafts only, never send directly).
    Delegates everything else to the parent ToolExecutor.
    """

    # Tools that have side effects and should be previewed in dry-run mode
    WRITE_TOOLS = {
        "add_to_maia_mail_review_queue",
        "create_email_draft",
        "send_message",
        "start_conversation",
        "send_email",
        "reply_to_email",
        "sheets_update_cells",
        "notion_append_blocks",
    }

    def __init__(
        self,
        workspace: str,
        draft_manager: DraftManager,
        thread: Dict[str, Any],
        classification: Dict[str, Any],
        email: str,
        platform=None,
        dry_run: bool = False,
    ):
        # ToolExecutor expects an 'agent' object — we pass None since mail
        # doesn't have an agent config.  The base class only uses it for
        # calendar_id / agent_calendars which we don't need.
        super().__init__(agent=None, workspace=workspace, platform=platform)
        self.draft_manager = draft_manager
        self.thread = thread
        self.classification = classification
        self.email = email  # Gmail database_id / user email
        self.drafts_created = 0
        self.dry_run = dry_run
        self.dry_run_actions: List[Dict[str, Any]] = []  # Log of what would happen

    async def execute(self, tool_name: str, tool_input: Dict[str, Any]) -> str:
        # Dry-run mode: preview write tools, execute read tools normally
        if self.dry_run and tool_name in self.WRITE_TOOLS:
            return self._preview_action(tool_name, tool_input)

        # Intercept: save draft to SQLite review queue
        if tool_name == "add_to_maia_mail_review_queue":
            return await self._add_to_review_queue(tool_input)

        # Block: never send email directly from the batch pipeline
        if tool_name in ("send_email", "reply_to_email"):
            return (
                "Error: You cannot send emails directly. "
                "Use add_to_maia_mail_review_queue to create a draft for user review, "
                "or create_email_draft to save a draft in Gmail."
            )

        # Everything else (query, search, sheets, messaging, create_email_draft)
        return await super().execute(tool_name, tool_input)

    def _preview_action(self, tool_name: str, tool_input: Dict[str, Any]) -> str:
        """Log and preview a write action without executing it."""
        self.dry_run_actions.append({"tool": tool_name, "input": tool_input})

        if tool_name == "add_to_maia_mail_review_queue":
            self.drafts_created += 1
            action = tool_input.get('action', 'reply')
            to = tool_input.get('to', '?')
            subject = tool_input.get('subject', '?')
            body = tool_input.get('body', '')
            preview = (
                f"[DRY RUN] Would create review queue draft:\n"
                f"  Action: {action}\n"
                f"  To: {to}\n"
                f"  Subject: {subject}\n"
                f"  Body: {body[:200]}{'...' if len(body) > 200 else ''}"
            )
            logger.info(preview)
            return f"[DRY RUN] Draft preview recorded (to={to}, action={action}). No draft was actually created."

        if tool_name == "create_email_draft":
            self.drafts_created += 1
            preview = (
                f"[DRY RUN] Would create Gmail draft:\n"
                f"  To: {tool_input.get('to', '?')}\n"
                f"  Subject: {tool_input.get('subject', '?')}\n"
                f"  Body: {tool_input.get('body', '')[:200]}"
            )
            logger.info(preview)
            return f"[DRY RUN] Gmail draft preview recorded. No draft was actually created."

        if tool_name == "send_message":
            preview = (
                f"[DRY RUN] Would send message:\n"
                f"  To: {tool_input.get('user') or tool_input.get('channel_id', '?')}\n"
                f"  Content: {tool_input.get('content', '')[:200]}"
            )
            logger.info(preview)
            return f"[DRY RUN] Message preview recorded. No message was actually sent."

        # Generic fallback for other write tools
        logger.info(f"[DRY RUN] Would call {tool_name} with: {tool_input}")
        return f"[DRY RUN] {tool_name} preview recorded. No action was taken."

    async def _add_to_review_queue(self, tool_input: Dict[str, Any]) -> str:
        """Save a draft to the maia mail SQLite review queue."""
        thread = self.thread
        thread_id = thread.get('thread_id')
        message_ids = thread.get('message_ids', [])
        last_message_id = message_ids[-1] if message_ids else thread_id

        action = tool_input.get('action', 'reply')
        to_addr = tool_input.get('to', '')
        cc_addr = tool_input.get('cc')
        subject = tool_input.get('subject', f"Re: {thread.get('subject', 'No Subject')}")
        body = tool_input.get('body', '')

        draft_data = {
            'draft_id': str(uuid.uuid4()),
            'workspace': self.workspace,
            'thread_id': thread_id,
            'message_id': last_message_id,
            'inbound_subject': thread.get('subject', 'No Subject'),
            'inbound_from': thread.get('from', ''),
            'inbound_to': thread.get('to', ''),
            'inbound_cc': thread.get('cc', ''),
            'inbound_snippet': thread.get('snippet', ''),
            'inbound_date': thread.get('date'),
            'inbound_body': thread.get('conversation_body', ''),
            'pertains_to_me': self.classification.get('pertains_to_me', True),
            'is_spam': self.classification.get('is_spam', False),
            'requires_response': self.classification.get('requires_response', True),
            'classification_reasoning': self.classification.get('reasoning'),
            'draft_subject': subject,
            'draft_body': body,
            'ai_model': 'agentic',
            'thread_context': thread.get('conversation_body', '')[:500],
            'message_count': thread.get('message_count', 1),
            'status': 'pending',
            'target_action': action,
            'target_to': to_addr,
            'target_cc': cc_addr,
            'generation_type': 'agent',
        }

        try:
            draft_id = self.draft_manager.save_draft(draft_data)
            self.drafts_created += 1
            logger.info(f"  ✅ Agent draft saved: {draft_id} (to={to_addr}, action={action})")
            return f"Draft created successfully (ID: {draft_id}). The user will review it before sending."
        except Exception as e:
            logger.error(f"  ❌ Failed to save agent draft: {e}")
            return f"Error saving draft: {e}"


# ── Build tool list ──────────────────────────────────────────────────────

def _build_tools(available: List[str]) -> List[Dict]:
    """Build the tool list for the mail agentic responder."""
    tools = []

    # Always include query tools and the review queue tool
    tools.extend(QUERY_TOOL_DEFINITIONS)
    tools.append(ADD_TO_REVIEW_QUEUE_TOOL)

    # Gmail read tools (search_emails, get_email_thread) — always if gmail available
    if "gmail" in available:
        tools.extend(GMAIL_READ_TOOL_DEFINITIONS)
        # Include create_email_draft (Gmail Drafts folder) from GMAIL_TOOL_DEFINITIONS
        for tool_def in GMAIL_TOOL_DEFINITIONS:
            if tool_def["name"] == "create_email_draft":
                tools.append(tool_def)

    # Google Sheets read tools
    if "google_sheets" in available:
        for tool_def in GOOGLE_SHEETS_TOOL_DEFINITIONS:
            if tool_def["name"] == "sheets_read_range":
                tools.append(tool_def)

    # Messaging tools (Slack/Discord)
    if "slack" in available or "discord" in available:
        tools.extend(MESSAGING_TOOL_DEFINITIONS)

    return tools


def _init_messaging_platform_from_env():
    """Create a messaging platform from env vars (no agent config needed)."""
    import os

    slack_token = os.environ.get("SLACK_BOT_TOKEN")
    if slack_token:
        try:
            from promaia.agents.messaging.slack_platform import SlackPlatform
            return SlackPlatform(bot_token=slack_token)
        except ImportError:
            pass

    discord_token = os.environ.get("DISCORD_BOT_TOKEN")
    if discord_token:
        try:
            from promaia.agents.messaging.discord_platform import DiscordPlatform
            return DiscordPlatform(bot_token=discord_token)
        except ImportError:
            pass

    return None


# ── Build system prompt ──────────────────────────────────────────────────

def _build_system_prompt(workspace: str) -> str:
    """Build system prompt: persona + tool guidance."""
    from promaia.mail.prompt_builder import EmailPromptBuilder

    # Load persona prompt (workspace-specific or generic)
    builder = EmailPromptBuilder(workspace=workspace)
    persona = builder._load_persona_prompt()

    # Add tool guidance
    guidance = """

## TOOL GUIDANCE

You are processing an inbound email. Your job is to draft a response for the user to review.

**Creating drafts:**
- Use `add_to_maia_mail_review_queue` to create drafts the user reviews in their maia mail queue.
  This is the preferred tool — it supports routing (reply/forward/new), safety confirmation,
  and learning system integration.
- Use `create_email_draft` only if you want to save a draft directly in Gmail's Drafts folder.
- You may call either tool multiple times to create multiple drafts from one email.
- NEVER use send_email or reply_to_email — always create drafts for user review.

**Looking up context:**
- Use `query_sql` or `query_vector` to search the knowledge base for relevant information.
- Use `search_emails` / `get_email_thread` to find related Gmail threads.
- Use `sheets_read_range` to look up data in Google Sheets.
- Only look things up if the email requires information you don't already have.

**Messaging:**
- Use `send_message` to notify someone on Slack/Discord if the persona prompt instructs you to.

**Efficiency:**
- For simple emails, just create one draft and finish. Don't overthink it.
- Only use query/search tools when you genuinely need more context.
"""

    return persona + guidance


# ── Main entry point ─────────────────────────────────────────────────────

async def respond(
    thread: Dict[str, Any],
    classification: Dict[str, Any],
    workspace: str,
    email: str,
    draft_manager: DraftManager,
    dry_run: bool = False,
) -> int:
    """
    Process an email thread with the agentic responder.

    Args:
        thread: Gmail thread data dict
        classification: Classification results from EmailClassifier
        workspace: Workspace name
        email: Gmail database_id / user email
        draft_manager: DraftManager instance for saving drafts
        dry_run: If True, preview actions without saving drafts or executing writes

    Returns:
        Number of drafts created (or would-be-created in dry_run mode)
    """
    subject = thread.get('subject', 'No Subject')
    mode_label = " [DRY RUN]" if dry_run else ""
    logger.info(f"  🤖 Agentic responder{mode_label}: {subject}")

    # 1. Detect available tools
    from promaia.chat.agentic_adapter import detect_available_tools
    available = detect_available_tools(workspace)

    # Check for Slack/Discord
    import os
    if os.environ.get("SLACK_BOT_TOKEN"):
        available.append("slack")
    if os.environ.get("DISCORD_BOT_TOKEN"):
        available.append("discord")

    # 2. Build tool list
    tools = _build_tools(available)
    logger.info(f"  → Tools available: {[t['name'] for t in tools]}")

    # 3. Init messaging platform if needed
    platform = None
    if "slack" in available or "discord" in available:
        platform = _init_messaging_platform_from_env()

    # 4. Create executor
    executor = MailToolExecutor(
        workspace=workspace,
        draft_manager=draft_manager,
        thread=thread,
        classification=classification,
        email=email,
        platform=platform,
        dry_run=dry_run,
    )

    # 5. Build system prompt
    system_prompt = _build_system_prompt(workspace)

    # 6. Build user message — the inbound email
    from_addr = thread.get('from', 'Unknown')
    to_addr = thread.get('to', '')
    cc_addr = thread.get('cc', '')
    date = thread.get('date', '')
    body = thread.get('conversation_body', '')
    message_count = thread.get('message_count', 1)

    user_message = f"From: {from_addr}\n"
    if to_addr:
        user_message += f"To: {to_addr}\n"
    if cc_addr:
        user_message += f"CC: {cc_addr}\n"
    user_message += f"Date: {date}\n"
    user_message += f"Subject: {subject}\n"
    if message_count > 1:
        user_message += f"Thread messages: {message_count}\n"
    user_message += f"\n{body}"

    messages = [{"role": "user", "content": user_message}]

    # 7. Run agentic turn
    try:
        result: AgenticTurnResult = await agentic_turn(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            tool_executor=executor,
            max_iterations=15,
        )
        logger.info(
            f"  → Agentic turn complete: {executor.drafts_created} draft(s), "
            f"{result.iterations_used} iterations, "
            f"{result.input_tokens + result.output_tokens} tokens"
        )
    except Exception as e:
        logger.error(f"  ❌ Agentic turn failed: {e}", exc_info=True)
        if not dry_run:
            # Save a fallback skipped draft so the email is marked as processed
            _save_fallback_draft(thread, classification, workspace, draft_manager, f"Agent error: {e}")
        return 1  # Count the fallback draft

    # 8. Dry-run summary
    if dry_run and executor.dry_run_actions:
        _print_dry_run_summary(thread, executor.dry_run_actions, result)

    # 9. If agent created 0 drafts, save a fallback
    if executor.drafts_created == 0:
        reasoning = result.response_text[:500] if result.response_text else "Agent completed without creating drafts"
        logger.warning(f"  ⚠️  Agent created 0 drafts — saving fallback: {reasoning[:100]}")
        if not dry_run:
            _save_fallback_draft(thread, classification, workspace, draft_manager, reasoning)
        return 1

    return executor.drafts_created


def _print_dry_run_summary(
    thread: Dict[str, Any],
    actions: List[Dict[str, Any]],
    result: AgenticTurnResult,
):
    """Print a rich summary of what the agent would do."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    console = Console()
    subject = thread.get('subject', 'No Subject')
    from_addr = thread.get('from', 'Unknown')

    lines = []
    lines.append(f"Subject: {subject}")
    lines.append(f"From: {from_addr}")
    lines.append(f"Iterations: {result.iterations_used} | Tokens: {result.input_tokens + result.output_tokens}")
    lines.append("")

    for i, action in enumerate(actions, 1):
        tool = action["tool"]
        inp = action["input"]

        if tool == "add_to_maia_mail_review_queue":
            lines.append(f"  [{i}] REVIEW QUEUE DRAFT ({inp.get('action', 'reply')})")
            lines.append(f"      To: {inp.get('to', '?')}")
            if inp.get('cc'):
                lines.append(f"      CC: {inp['cc']}")
            lines.append(f"      Subject: {inp.get('subject', '?')}")
            body = inp.get('body', '')
            # Show full body for dry run preview
            for body_line in body.split('\n'):
                lines.append(f"      | {body_line}")
        elif tool == "create_email_draft":
            lines.append(f"  [{i}] GMAIL DRAFT")
            lines.append(f"      To: {inp.get('to', '?')}")
            lines.append(f"      Subject: {inp.get('subject', '?')}")
            body = inp.get('body', '')
            for body_line in body.split('\n'):
                lines.append(f"      | {body_line}")
        elif tool == "send_message":
            target = inp.get('user') or inp.get('channel_id', '?')
            lines.append(f"  [{i}] SLACK/DISCORD MESSAGE to {target}")
            content = inp.get('content', '')
            for content_line in content.split('\n'):
                lines.append(f"      | {content_line}")
        else:
            lines.append(f"  [{i}] {tool}")
            lines.append(f"      Input: {inp}")

        lines.append("")

    console.print(Panel(
        "\n".join(lines),
        title="[bold cyan]DRY RUN PREVIEW[/bold cyan]",
        border_style="cyan",
    ))


def _save_fallback_draft(
    thread: Dict[str, Any],
    classification: Dict[str, Any],
    workspace: str,
    draft_manager: DraftManager,
    reasoning: str,
):
    """Save a skipped draft so the email is marked as processed."""
    thread_id = thread.get('thread_id')
    message_ids = thread.get('message_ids', [])
    last_message_id = message_ids[-1] if message_ids else thread_id

    draft_data = {
        'workspace': workspace,
        'thread_id': thread_id,
        'message_id': last_message_id,
        'inbound_subject': thread.get('subject', 'No Subject'),
        'inbound_from': thread.get('from', ''),
        'inbound_to': thread.get('to', ''),
        'inbound_cc': thread.get('cc', ''),
        'inbound_snippet': thread.get('snippet', ''),
        'inbound_date': thread.get('date'),
        'inbound_body': thread.get('conversation_body', ''),
        'pertains_to_me': classification.get('pertains_to_me', True),
        'is_spam': classification.get('is_spam', False),
        'requires_response': classification.get('requires_response', True),
        'classification_reasoning': reasoning,
        'draft_subject': f"Re: {thread.get('subject', 'No Subject')}",
        'draft_body': 'n/a',
        'status': 'skipped',
        'generation_type': 'agent',
    }

    draft_manager.save_draft(draft_data)
