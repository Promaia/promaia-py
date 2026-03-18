"""
Email Prompt Builder - Unified prompt construction for email drafting.

Builds structured prompts with consistent ordering and formatting for both:
- Batch processing (processor.py → response_generator.py)
- Interactive chat (draft_chat.py → DraftMode)

This ensures all email drafts use the same high-quality prompt structure.
"""
import logging
import os
import re
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


def strip_prompt_comments(text: str) -> str:
    """Strip HTML comments (<!-- ... -->) from prompt text.

    Used to remove documentation/guidance comments from prompt files
    before sending to the AI, saving tokens.
    """
    return re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL).strip()


class EmailPromptBuilder:
    """
    Builds structured email prompts with consistent formatting.

    Prompt structure (general → specific):
    1. Persona prompt (from prompts/maia_mail_prompt.md)
    2. Sent response examples (from learning system) - TODO: Add semantic search
    3. Non-email vector search (journal, CPJ, stories, etc.)
    4. Email vector search (similar Gmail threads)
    5. Thread conversation (if multi-message)
    6. Email we're replying to (the specific inbound message)
    """

    def __init__(self, workspace: str):
        """
        Initialize prompt builder.

        Args:
            workspace: Workspace name for workspace-specific prompts
        """
        self.workspace = workspace

    def build_prompt(self, structured_context: Dict[str, Any]) -> str:
        """
        Build email prompt from structured context.

        Args:
            structured_context: Dict with keys:
                - thread_email: Dict with from/to/cc/date/subject/body
                - thread_conversation: Full thread if multi-message (str)
                - message_count: Number of messages in thread (int)
                - non_email_docs: List of relevant non-email documents
                - email_docs: List of relevant email threads
                - draft_data: Optional raw draft data

        Returns:
            Complete system prompt string
        """
        sections = []

        # 1. PERSONA PROMPT
        persona = self._load_persona_prompt()
        sections.append(persona)

        # 2. SENT RESPONSE EXAMPLES (from learning system)
        # TODO: Add semantic search for relevant examples
        # For now, skip this section - learning system needs refactoring

        # 3. NON-EMAIL VECTOR SEARCH
        non_email_docs = structured_context.get('non_email_docs', [])
        if non_email_docs:
            non_email_section = self._format_non_email_context(non_email_docs)
            sections.append(non_email_section)
            logger.info(f"✅ Added {len(non_email_docs)} non-email documents to prompt")

        # 4. EMAIL VECTOR SEARCH
        email_docs = structured_context.get('email_docs', [])
        if email_docs:
            email_section = self._format_email_context(email_docs)
            sections.append(email_section)
            logger.info(f"✅ Added {len(email_docs)} related emails to prompt")

        # 5. THREAD CONVERSATION (if multi-message)
        message_count = structured_context.get('message_count', 1)
        thread_conversation = structured_context.get('thread_conversation', '')

        if message_count > 1 and thread_conversation:
            thread_section = self._format_thread_conversation(thread_conversation, message_count)
            sections.append(thread_section)
            logger.info(f"✅ Added thread conversation ({message_count} messages) to prompt")

        # 6. EMAIL WE'RE REPLYING TO
        thread_email = structured_context.get('thread_email', {})
        if thread_email:
            email_section = self._format_reply_to_email(thread_email)
            sections.append(email_section)
            logger.info(f"✅ Added email to reply to: {thread_email.get('subject', 'No subject')}")

        return '\n'.join(sections)

    def _load_persona_prompt(self) -> str:
        """Load persona prompt from file with date/time variables filled in.

        Tries workspace-specific prompt first (maia_mail_prompt_{workspace}.md),
        falls back to generic prompt (maia_mail_prompt.md).
        """
        from promaia.utils.env_writer import get_prompts_dir
        prompts_dir = get_prompts_dir()

        # Try workspace-specific prompt first
        workspace_prompt_path = prompts_dir / f"maia_mail_prompt_{self.workspace}.md"
        generic_prompt_path = prompts_dir / "maia_mail_prompt.md"

        if os.path.exists(workspace_prompt_path):
            prompt_path = workspace_prompt_path
        elif os.path.exists(generic_prompt_path):
            prompt_path = generic_prompt_path
        else:
            logger.warning(f"Persona prompt not found: tried {workspace_prompt_path} and {generic_prompt_path}")
            return "You are a helpful email assistant."

        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                persona = f.read()

            # Strip HTML comments (documentation for the editor)
            persona = strip_prompt_comments(persona)

            # Fill in date/time variables
            from promaia.utils.timezone_utils import now_utc
            now = now_utc()
            persona = persona.format(
                today_date=now.strftime("%B %d, %Y"),
                current_time=now.strftime("%I:%M %p %Z")
            )

            logger.info(f"✅ Loaded persona prompt from '{prompt_path}'")
            return persona

        except Exception as e:
            logger.error(f"Failed to load persona prompt: {e}")
            return "You are a helpful email assistant."

    def _format_non_email_context(self, non_email_docs: List[Dict[str, Any]]) -> str:
        """Format non-email vector search results."""
        lines = ["\n\n## RELEVANT CONTEXT FROM YOUR KNOWLEDGE BASE\n"]
        lines.append(f"Found {len(non_email_docs)} relevant documents from your personal notes and project files:\n")

        for i, doc in enumerate(non_email_docs, 1):
            lines.append(f"\n### [{i}] {doc.get('title', 'Untitled')}")
            lines.append(f"Database: {doc.get('database', 'unknown')} | Relevance: {doc.get('similarity', 0):.0%}")
            lines.append(f"\n{doc.get('content', '')}\n")

        return '\n'.join(lines)

    def _format_email_context(self, email_docs: List[Dict[str, Any]]) -> str:
        """Format email vector search results."""
        lines = ["\n\n## RELATED EMAIL THREADS\n"]
        lines.append(f"Found {len(email_docs)} related email conversations:\n")

        for i, doc in enumerate(email_docs, 1):
            lines.append(f"\n### [{i}] {doc.get('title', 'Untitled')}")
            lines.append(f"Relevance: {doc.get('similarity', 0):.0%}")
            lines.append(f"\n{doc.get('content', '')}\n")

        return '\n'.join(lines)

    def _format_thread_conversation(self, thread_conversation: str, message_count: int) -> str:
        """Format full thread conversation for multi-message threads."""
        lines = ["\n\n## FULL EMAIL THREAD CONVERSATION\n"]
        lines.append("This is the complete conversation history (messages in descending order, most recent last):\n")
        lines.append(f"\n{thread_conversation}\n")

        return '\n'.join(lines)

    def _format_reply_to_email(self, thread_email: Dict[str, str]) -> str:
        """Format the specific email being replied to."""
        lines = ["\n\n## EMAIL YOU ARE REPLYING TO\n"]
        lines.append(f"From: {thread_email.get('from', '')}")
        lines.append(f"Date: {thread_email.get('date', '')}")
        lines.append(f"Subject: {thread_email.get('subject', '')}")

        if thread_email.get('to'):
            lines.append(f"To: {thread_email.get('to')}")
        if thread_email.get('cc'):
            lines.append(f"CC: {thread_email.get('cc')}")

        lines.append(f"\n{thread_email.get('body', '')}")

        return '\n'.join(lines)
