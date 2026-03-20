"""
Chat modes for specialized behaviors in maia chat.

Modes allow maia chat to support specialized workflows while maintaining
a unified architecture. Examples: email drafting, blog writing, code generation.
"""
import logging
import os
from typing import Dict, Optional, Callable, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class ChatMode:
    """Base class for chat modes."""
    
    def __init__(self, workspace: str):
        """
        Initialize chat mode.
        
        Args:
            workspace: Workspace name
        """
        self.workspace = workspace
    
    def get_system_prompt(self) -> Optional[str]:
        """
        Get system prompt for this mode.
        
        Override in subclass to provide custom system prompt.
        Return None to use default chat system prompt.
        
        Returns:
            System prompt string or None
        """
        return None
    
    def get_additional_commands(self) -> Dict[str, Callable]:
        """
        Get mode-specific commands.
        
        Override in subclass to add custom commands.
        
        Returns:
            Dict of command_name -> handler_function
        """
        return {}
    
    def get_welcome_message(self, context_breakdown: dict, **kwargs) -> Optional[str]:
        """
        Get custom welcome message for this mode.
        
        Override in subclass to customize welcome message.
        Return None to use default welcome.
        
        Args:
            context_breakdown: Dict of database_name -> count
            **kwargs: Additional mode-specific data
            
        Returns:
            Welcome message string or None
        """
        return None
    
    def should_enable_artifacts(self) -> bool:
        """
        Whether artifacts should be enabled for this mode.

        Returns:
            True to enable artifacts
        """
        return True

    def should_force_artifacts(self) -> bool:
        """
        Whether ALL responses should be forced as artifacts in this mode.

        When True, every AI response will be treated as an artifact
        regardless of content or keywords. Useful for modes like email
        drafting where all outputs are meant to be structured content.

        Returns:
            True to force all responses as artifacts
        """
        return False

    def handles_own_context(self) -> bool:
        """
        Whether this mode builds its own context in get_system_prompt().

        When True, the chat interface will NOT append generic context formatting
        to the mode's system prompt. The mode is responsible for including
        all context in its custom format.

        Returns:
            True if mode handles its own context formatting
        """
        return False


class DraftMode(ChatMode):
    """
    Email draft mode - specialized for email composition.

    Uses artifacts for email drafts and adds /send and /archive commands.
    """

    def __init__(
        self,
        workspace: str,
        draft_id: str,
        draft_data: dict,
        draft_manager,
        user_email: str,
        context_builder=None,
        response_generator=None,
        structured_context=None
    ):
        """
        Initialize draft mode.

        Args:
            workspace: Workspace name
            draft_id: Draft ID
            draft_data: Draft data dict
            draft_manager: DraftManager instance
            user_email: User's email address
            context_builder: ResponseContextBuilder instance for -dc support
            response_generator: ResponseGenerator instance for -dc support
            structured_context: Structured context dict with email/non-email docs separated
        """
        super().__init__(workspace)
        self.draft_id = draft_id
        self.draft_data = draft_data
        self.draft_manager = draft_manager
        self.user_email = user_email
        self.context_builder = context_builder
        self.response_generator = response_generator
        self.structured_context = structured_context or {}

    def get_system_prompt(self) -> Optional[str]:
        """
        Build custom system prompt for draft mode using EmailPromptBuilder.

        Returns:
            Custom system prompt for email drafting
        """
        from promaia.mail.prompt_builder import EmailPromptBuilder

        # Use shared prompt builder for consistency with batch processing
        builder = EmailPromptBuilder(workspace=self.workspace)
        return builder.build_prompt(self.structured_context)

    def get_additional_commands(self) -> Dict[str, Callable]:
        """
        Add /send and /archive commands for draft mode.

        Returns:
            Dict of draft-specific commands
        """
        return {
            '/send': self.handle_send,
            '/archive': self.handle_archive,
        }

    def should_force_artifacts(self) -> bool:
        """
        Allow AI to choose when to use artifacts in draft mode.

        The AI should use <artifact> tags when composing email drafts,
        but use regular messages for clarifications and questions.

        Returns:
            False - let AI decide based on system prompt instructions
        """
        return False

    def handles_own_context(self) -> bool:
        """
        DraftMode builds its own context in custom format.

        Returns:
            True - DraftMode handles all context formatting in get_system_prompt()
        """
        return True

    def get_welcome_message(self, context_state: Dict[str, Any]) -> Optional[str]:
        """
        Custom welcome for draft chat showing structured context info.

        Args:
            context_state: Chat context state dict

        Returns:
            Welcome message for draft mode
        """
        from promaia.chat.interface import get_current_model_name

        lines = []

        # Header - use ANSI codes directly since we're in a thread
        lines.append("\033[1m\033[95m🐙 maia mail draft chat\033[0m")

        # Show context loaded from structured_context
        non_email_count = len(self.structured_context.get('non_email_docs', []))
        email_count = len(self.structured_context.get('email_docs', []))
        total_sources = non_email_count + email_count

        if total_sources > 0:
            lines.append(f"\033[2mContext loaded: {total_sources} sources\033[0m")
            if non_email_count > 0:
                lines.append(f"\033[2m  • {non_email_count} from knowledge base (notes, docs)\033[0m")
            if email_count > 0:
                lines.append(f"\033[2m  • {email_count} related email threads\033[0m")
        else:
            lines.append("\033[2mContext: Email thread only (no additional sources)\033[0m")

        # Model
        try:
            model_name = get_current_model_name()
            lines.append(f"\033[2mModel: {model_name}\033[0m")
        except Exception as e:
            logger.warning(f"Could not get model name in draft mode: {e}")
            lines.append("\033[2mModel: (unknown)\033[0m")

        lines.append("")

        # Commands - MODE SPECIFIC
        lines.append("\033[2mAvailable commands:\033[0m")
        lines.append("\033[2m  /send - Send this draft\033[0m")
        lines.append("\033[2m  /archive or /a - Archive this email\033[0m")
        lines.append("\033[2m  /e - Edit context\033[0m")
        lines.append("\033[2m  /s - Sync databases\033[0m")
        lines.append("\033[2m  /model - Switch model\033[0m")
        lines.append("\033[2m  /q - Return to draft list\033[0m")
        lines.append("")

        return "\n".join(lines)

    async def handle_send(self, artifact_manager, messages, context_state):
        """
        Handle /send command for drafts.

        Args:
            artifact_manager: ArtifactManager instance
            messages: Chat messages
            context_state: Chat context state

        Returns:
            True if should exit chat, False to continue
        """
        from promaia.utils.display import print_text
        from promaia.mail.recipient_selector import RecipientSelector
        from promaia.mail.gmail_sender import GmailSender
        from promaia.utils.timezone_utils import now_utc

        # Get the latest artifact (draft)
        if not artifact_manager or not artifact_manager.artifacts:
            print_text("❌ No draft to send", style="red")
            return False

        latest_artifact_id = max(artifact_manager.artifacts.keys())

        # Extract email body from artifact (handles both JSON and plain text)
        from promaia.mail.artifact_helpers import get_email_body_from_artifact
        draft_to_send = get_email_body_from_artifact(artifact_manager, latest_artifact_id)

        if not draft_to_send:
            print_text("❌ Could not extract email body from artifact", style="red")
            return False

        # Check for routing overrides from persona prompt
        target_action = self.draft_data.get('target_action', 'reply') or 'reply'
        target_to = self.draft_data.get('target_to')
        target_cc = self.draft_data.get('target_cc')

        # Pre-populate recipients if routing specifies a target
        default_recipients = None
        if target_to:
            default_recipients = [r.strip() for r in target_to.split(',')]

        default_cc = None
        if target_cc:
            default_cc = [r.strip() for r in target_cc.split(',')]

        # Show recipient selector
        selector = RecipientSelector(
            from_addr=self.draft_data.get('inbound_from', ''),
            to_addr=self.draft_data.get('inbound_to', ''),
            cc_addr=self.draft_data.get('inbound_cc', ''),
            thread_context=self.draft_data.get('thread_context', ''),
            user_email=self.user_email,
            default_recipients=default_recipients,
            default_cc=default_cc,
        )

        print_text("\n📧 Select recipients for this email...", style="cyan")
        confirmed, recipients = await selector.run()

        if not confirmed:
            print_text("\n↩️  Send cancelled\n", style="cyan")
            return False

        if not recipients:
            print_text("\n❌ No recipients selected\n", style="red")
            return False

        print_text(f"\n✅ Sending to: {', '.join(recipients)}", style="green")

        # Format the draft
        from promaia.mail.response_generator import ResponseGenerator
        generator = ResponseGenerator()
        draft_to_send = generator._format_email_body(draft_to_send)

        # Display the draft for review
        print()
        print_text("─" * 80, style="dim")
        print_text(f"DRAFT TO SEND (Artifact #{latest_artifact_id})", style="bold cyan")
        print_text(f"Subject: {self.draft_data['inbound_subject']}", style="cyan")
        print_text("─" * 80, style="dim")
        print()
        print_text(draft_to_send, style="white")
        print()
        print_text("─" * 80, style="dim")

        # Safety confirmation
        print()
        print_text(f"⚠️  Ready to send Draft #{latest_artifact_id}", style="bold yellow")
        print_text(f"To: {', '.join(recipients)}", style="yellow")
        print_text(f"\nType the first 5 characters to confirm: '{self.draft_data['safety_string']}'", style="yellow")
        print_text(f"Or type 'cancel' (or press Enter) to abort", style="dim")

        confirmation = input("\nConfirm: ").strip()

        if not confirmation or confirmation.lower() == 'cancel':
            print_text("\n↩️  Send cancelled\n", style="cyan")
            return False

        # Get safety string (already lowercase from helper function)
        safety_string = self.draft_data['safety_string'].rstrip()

        if confirmation.lower() != safety_string:
            print_text("\n❌ Confirmation failed\n", style="red")
            return False

        print_text("\n📤 Sending email...", style="cyan")

        # Send email — branch on action type
        sender = GmailSender(self.workspace, self.user_email)
        if target_action == 'new':
            # New email — no thread association
            success = await sender.send_email(
                to=', '.join(recipients),
                subject=self.draft_data['draft_subject'],
                body_text=draft_to_send,
            )
        else:
            # Both 'reply' and 'forward' use send_reply (threaded)
            success = await sender.send_reply(
                thread_id=self.draft_data['thread_id'],
                message_id=self.draft_data['message_id'],
                subject=self.draft_data['draft_subject'],
                body_text=draft_to_send,
                recipients=recipients,
            )

        if success:
            print_text("✅ Email sent!", style="green")
            self.draft_manager.mark_sent(self.draft_id)

            # Save to learning system
            from promaia.mail.learning_system import EmailResponseLearningSystem
            learning = EmailResponseLearningSystem(workspace=self.workspace)

            pattern = {
                "inbound": {
                    "from": self.draft_data['inbound_from'],
                    "subject": self.draft_data['inbound_subject'],
                    "body_snippet": self.draft_data['inbound_snippet'],
                },
                "response": {
                    "subject": self.draft_data['draft_subject'],
                    "body": draft_to_send,
                    "tone": "professional",
                    "length": len(draft_to_send.split())
                },
                "metadata": {
                    "workspace": self.workspace,
                    "ai_model": context_state.get('current_api', 'unknown'),
                    "timestamp": now_utc().isoformat()
                }
            }
            learning.save_successful_response(pattern)

            print()
            print_text("↩️  Returning to draft list...", style="cyan")
            print()
            return True
        else:
            print_text("❌ Failed to send\n", style="red")
            return False

    async def handle_archive(self, artifact_manager, messages, context_state):
        """
        Handle /archive command.

        Args:
            artifact_manager: ArtifactManager instance (not used, but required for consistency)
            messages: Chat messages (not used, but required for consistency)
            context_state: Chat context state (not used, but required for consistency)

        Returns:
            True if should exit chat, False to continue
        """
        from promaia.utils.display import print_text

        self.draft_manager.update_draft_status(self.draft_id, 'archived')
        print_text("\n🗄️  Archived - cleared from your queue\n", style="green")
        return True
