"""
Response Generator - Generates email responses using AI with learning.

Uses learned patterns from previous successful responses to match user's style.
"""
import logging
import re
from typing import Dict, Any, Optional
import os
from promaia.utils.timezone_utils import now_utc
from promaia.utils.env_writer import get_data_dir, get_prompts_dir

from promaia.mail.learning_system import EmailResponseLearningSystem
from promaia.mail.context_builder import ResponseContext

logger = logging.getLogger(__name__)


class ResponseGenerator:
    """Generates email responses using AI with learning."""

    # DEPRECATED: Old prompt template, replaced by EmailPromptBuilder for consistency
    # RESPONSE_PROMPT_TEMPLATE = """{user_persona}
    #
    # You are writing an email response based on the following context.
    #
    # {learned_patterns}
    #
    # === THREAD HISTORY ===
    # {thread_history}
    #
    # {context_documents}
    #
    # === LATEST MESSAGE TO RESPOND TO ===
    # From: {from_addr}
    # Subject: {subject}
    # Date: {date}"""

    def __init__(self):
        """Initialize response generator."""
        self.learning_systems = {}  # Cache learning systems by workspace
        self.ai_client = None
        self.model_type = None
        # Note: user_persona loading moved to EmailPromptBuilder
        self.refinement_prompt_template = self._load_refinement_prompt()
    
    def _get_learning_system(self, workspace: str) -> EmailResponseLearningSystem:
        """Get or create learning system for workspace."""
        if workspace not in self.learning_systems:
            self.learning_systems[workspace] = EmailResponseLearningSystem(workspace=workspace)
        return self.learning_systems[workspace]

    # REMOVED: _load_user_persona() - now handled by EmailPromptBuilder
    
    def _load_refinement_prompt(self) -> str:
        """Load refinement prompt template from file.

        Returns a sensible default if the file doesn't exist yet —
        the refinement prompt is only used by DraftMode chat, not
        by the agentic responder or batch processing.
        """
        prompt_file = str(get_prompts_dir() / "maia_mail_refinement_prompt.md")
        try:
            with open(prompt_file, 'r') as f:
                return f.read()
        except FileNotFoundError:
            logger.warning(f"Refinement prompt not found: {prompt_file} — using default")
            return (
                "You are helping the user refine an email draft. "
                "Make edits based on their feedback while preserving their voice and intent."
            )
        except Exception as e:
            logger.error(f"Error loading refinement prompt: {e}")
            raise

    def _save_mail_context_log(
        self, 
        prompt_content: str, 
        log_type: str,
        subject: Optional[str] = None,
        from_addr: Optional[str] = None,
        workspace: Optional[str] = None,
        context: Optional[ResponseContext] = None,
        model_type: Optional[str] = None
    ):
        """
        Save the prompt content to a log file for debugging with structured formatting.
        
        Args:
            prompt_content: The full prompt sent to the AI.
            log_type: Type of log (e.g., 'initial_draft', 'refinement').
            subject: Email subject (optional, for filename)
            from_addr: Sender email address (optional, for metadata)
            workspace: Workspace name (optional, for metadata)
            context: ResponseContext (optional, for metadata)
            model_type: AI model type (optional, for metadata)
        """
        try:
            # Determine log directory based on type
            data_root = get_data_dir()
            if log_type == "initial_draft":
                log_dir = str(data_root / "context_logs" / "mail_draft_logs")
            else:
                log_dir = str(data_root / "context_logs" / "mail_context_logs")

            os.makedirs(log_dir, exist_ok=True)
            
            timestamp = now_utc().strftime("%Y%m%d-%H%M%S")
            
            # Create filename with subject if available
            if subject and log_type == "initial_draft":
                # Sanitize subject for filename (truncate and remove invalid chars)
                safe_subject = re.sub(r'[^\w\s-]', '', subject)
                safe_subject = re.sub(r'[-\s]+', '_', safe_subject)
                safe_subject = safe_subject[:50]  # Truncate to 50 chars
                filename = f"{log_dir}/{timestamp}_{log_type}_{safe_subject}.txt"
            else:
                filename = f"{log_dir}/{timestamp}_{log_type}_prompt.txt"
            
            with open(filename, 'w', encoding='utf-8') as f:
                # Write header with metadata
                f.write(f"=== MAIA MAIL - {log_type.upper().replace('_', ' ')} ===\n")
                f.write(f"Timestamp: {timestamp}\n")
                if model_type:
                    f.write(f"Model: {model_type}\n")
                if workspace:
                    f.write(f"Workspace: {workspace}\n")
                if from_addr:
                    f.write(f"From: {from_addr}\n")
                if subject:
                    f.write(f"Subject: {subject}\n")
                if context:
                    f.write(f"Context Sources: {context.total_sources} relevant documents\n")
                f.write("\n")
                
                # Write prompt sections with clear headers
                f.write("=" * 50 + "\n")
                f.write("FULL PROMPT SENT TO AI:\n")
                f.write("=" * 50 + "\n\n")
                f.write(prompt_content)
                f.write("\n\n")
                
            logger.info(f"Saved mail context log to {filename}")
            
        except Exception as e:
            logger.error(f"Failed to save mail context log: {e}")
    
    def _parse_routing_headers(self, response_text: str) -> tuple:
        """
        Parse optional routing headers from AI response.

        Headers are lines at the top matching KEY: value where KEY is one of
        ACTION, TO, CC, SUBJECT. Parsing stops at the first blank line or
        non-header line.

        Returns:
            (routing_dict, body_text)
            routing_dict may contain: target_action, target_to, target_cc, subject
            body_text is the response with headers stripped
        """
        VALID_HEADERS = {'ACTION', 'TO', 'CC', 'SUBJECT'}
        routing = {}
        lines = response_text.split('\n')
        body_start = 0

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                # Blank line ends header section
                body_start = i + 1
                break

            # Check for HEADER: value pattern
            colon_pos = stripped.find(':')
            if colon_pos > 0:
                key = stripped[:colon_pos].strip().upper()
                if key in VALID_HEADERS:
                    value = stripped[colon_pos + 1:].strip()
                    if key == 'ACTION':
                        if value.lower() in ('reply', 'forward', 'new'):
                            routing['target_action'] = value.lower()
                    elif key == 'TO':
                        routing['target_to'] = value
                    elif key == 'CC':
                        routing['target_cc'] = value
                    elif key == 'SUBJECT':
                        routing['subject'] = value
                    body_start = i + 1
                    continue

            # Not a recognized header — everything from here is body
            body_start = i
            break

        body = '\n'.join(lines[body_start:]).strip()
        return routing, body

    def _format_email_body(self, text: str) -> str:
        """
        Remove unnecessary hard line breaks from email body while preserving intentional formatting.
        
        This fixes the issue where AI generates text with hard wraps at ~70-80 characters,
        which looks bad in modern email clients. We want continuous paragraphs that wrap naturally.
        
        Rules:
        - Remove single line breaks within paragraphs (hard wraps)
        - Preserve double line breaks (paragraph separators)
        - Remove hard breaks within list items
        - Preserve line breaks between list items
        - Preserve intentional formatting like signatures
        """
        if not text:
            return text
        
        # Split into lines
        lines = text.split('\n')
        formatted_lines = []
        current_paragraph = []
        in_list_item = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # Empty line = paragraph break
            if not stripped:
                # Flush current paragraph
                if current_paragraph:
                    formatted_lines.append(' '.join(current_paragraph))
                    current_paragraph = []
                in_list_item = False
                # Add paragraph break (single empty line)
                if formatted_lines and formatted_lines[-1] != '':
                    formatted_lines.append('')
                continue
            
            # Check if this is a list item start (numbered or bulleted)
            is_list_start = re.match(r'^\d+[\.\)]\s', stripped) or re.match(r'^[-\*•]\s', stripped)
            
            if is_list_start:
                # Flush previous paragraph/list item
                if current_paragraph:
                    formatted_lines.append(' '.join(current_paragraph))
                    current_paragraph = []
                # Start new list item
                current_paragraph = [stripped]
                in_list_item = True
                continue
            
            # Salutations and closings - preserve as separate lines
            if stripped in ['Hi!', 'Hello!', 'Thanks!', 'Best!', 'Cheers!', 'Best regards,', 'Thanks,', 'Cheers,', 'Best,']:
                # Flush current paragraph
                if current_paragraph:
                    formatted_lines.append(' '.join(current_paragraph))
                    current_paragraph = []
                formatted_lines.append(stripped)
                in_list_item = False
                continue
            
            # Check if this looks like a signature line
            if len(stripped) < 40 and i == len(lines) - 1:
                # Last line and short - likely a signature
                if current_paragraph:
                    formatted_lines.append(' '.join(current_paragraph))
                    current_paragraph = []
                formatted_lines.append(stripped)
                continue
            
            # If we're in a list item or regular paragraph, add to current
            current_paragraph.append(stripped)
        
        # Flush any remaining paragraph
        if current_paragraph:
            formatted_lines.append(' '.join(current_paragraph))
        
        # Join with single newlines (paragraphs separated by blank lines)
        result = '\n'.join(formatted_lines)
        
        # Clean up any excessive blank lines (max 1 blank line between paragraphs)
        result = re.sub(r'\n\n\n+', '\n\n', result)
        
        return result.strip()
    
    def _get_ai_client(self):
        """Get AI client from existing infrastructure."""
        if self.ai_client is not None:
            return self.ai_client
        
        import os
        from anthropic import Anthropic
        from openai import OpenAI
        
        # Try Anthropic first (preferred)
        if os.getenv("ANTHROPIC_API_KEY"):
            self.ai_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), base_url=os.environ.get("ANTHROPIC_BASE_URL"), max_retries=5)
            self.model_type = "anthropic"
            logger.info("Using Anthropic for response generation")
            return self.ai_client
        
        # Fall back to OpenAI
        if os.getenv("OPENAI_API_KEY"):
            self.ai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            self.model_type = "openai"
            logger.info("Using OpenAI for response generation")
            return self.ai_client
        
        raise ValueError("No AI API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY")
    
    async def generate_response(
        self,
        email_thread: Dict[str, Any],
        workspace: str,
        structured_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Generate email response using EmailPromptBuilder for consistency.

        Args:
            email_thread: Email thread data
            workspace: Workspace name
            structured_context: Structured context dict with:
                - thread_email: Dict with from/to/cc/date/subject/body
                - thread_conversation: Full thread if multi-message
                - message_count: Number of messages in thread
                - non_email_docs: List of relevant non-email documents
                - email_docs: List of relevant email threads

        Returns:
            Dict with 'body' (response text), 'subject', 'model' keys
        """
        try:
            # Extract email details
            from_addr = email_thread.get('from', 'Unknown')
            subject = email_thread.get('subject', 'No Subject')

            # Build prompt using shared EmailPromptBuilder for consistency
            from promaia.mail.prompt_builder import EmailPromptBuilder
            builder = EmailPromptBuilder(workspace=workspace)
            prompt = builder.build_prompt(structured_context)

            # Get AI client (this sets self.model_type)
            client = self._get_ai_client()

            # Save prompt for debugging with full metadata (after getting client so model_type is set)
            self._save_mail_context_log(
                prompt,
                "initial_draft",
                subject=subject,
                from_addr=from_addr,
                workspace=workspace,
                context=None,  # No longer using ResponseContext
                model_type=self.model_type
            )
            
            # Generate response based on model type
            if self.model_type == "anthropic":
                response = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2000,
                    messages=[{
                        "role": "user",
                        "content": prompt
                    }]
                )
                response_body = response.content[0].text.strip()
                model_used = "claude-sonnet-4-6"
            
            elif self.model_type == "openai":
                response = client.chat.completions.create(
                    model="gpt-4o",
                    max_tokens=2000,
                    messages=[{
                        "role": "user",
                        "content": prompt
                    }]
                )
                response_body = response.choices[0].message.content.strip()
                model_used = "gpt-4o"
            
            else:
                raise ValueError(f"Unknown model type: {self.model_type}")
            
            # Parse optional routing headers from AI response
            routing, clean_body = self._parse_routing_headers(response_body)

            # Format the email body to remove hard line breaks
            clean_body = self._format_email_body(clean_body)

            # Prepare response subject — use routing override if present
            if 'subject' in routing:
                response_subject = routing['subject']
            else:
                response_subject = subject
                if not response_subject.upper().startswith('RE:'):
                    response_subject = f"RE: {response_subject}"

            logger.info(f"✅ Generated response ({len(clean_body.split())} words)")
            if routing:
                logger.info(f"  → Routing headers: {routing}")

            result = {
                'body': clean_body,
                'subject': response_subject,
                'model': model_used,
                'prompt': prompt  # Store for debugging/refinement
            }

            # Include routing fields only if explicitly set by AI
            if 'target_action' in routing:
                result['target_action'] = routing['target_action']
            if 'target_to' in routing:
                result['target_to'] = routing['target_to']
            if 'target_cc' in routing:
                result['target_cc'] = routing['target_cc']

            return result
            
        except Exception as e:
            logger.error(f"❌ Failed to generate response: {e}")
            raise
    
    async def refine_response(
        self,
        current_draft: str,
        user_feedback: str,
        email_thread: Dict[str, Any],
        context: ResponseContext,
        user_email: str = None,
        workspace: str = None
    ) -> str:
        """
        Refine an existing draft based on user feedback.
        
        Args:
            current_draft: The current draft text
            user_feedback: User's refinement request
            email_thread: Original email thread data
            context: ResponseContext
            user_email: Email address of the user (optional, for context)
            workspace: Workspace name (optional, for context)
            
        Returns:
            Refined draft text
        """
        try:
            # Get email body for context
            email_body = email_thread.get('conversation_body') or email_thread.get('body', '')
            
            # Prepare draft section
            if current_draft:
                current_draft_section = f"You previously generated this email draft:\n\n{current_draft}\n\n"
                action = "revise the draft"
                result_type = "revised"
            else:
                current_draft_section = "You are generating a NEW draft for this email.\n\n"
                action = "generate a response"
                result_type = ""
            
            # Build refinement prompt from template
            from_addr = email_thread.get('from', 'Unknown')
            subject = email_thread.get('subject', 'No Subject')
            
            refinement_prompt = self.refinement_prompt_template.format(
                user_persona=self.user_persona,
                user_email=user_email or "the user",
                workspace=workspace or "unknown",
                current_draft_section=current_draft_section,
                user_feedback=user_feedback,
                from_addr=from_addr,
                subject=subject,
                email_body=email_body,
                context_docs=context.relevant_docs_text if context.relevant_docs_text else "No additional context available",
                action=action,
                result_type=result_type
            )
            
            # Get AI client (this sets self.model_type)
            client = self._get_ai_client()
            
            # Save refinement prompt for debugging with metadata (after getting client so model_type is set)
            self._save_mail_context_log(
                refinement_prompt, 
                "refinement",
                subject=subject,
                from_addr=from_addr,
                workspace=workspace,
                context=context,
                model_type=self.model_type
            )
            
            # Generate refined response
            if self.model_type == "anthropic":
                response = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2000,
                    messages=[{
                        "role": "user",
                        "content": refinement_prompt
                    }]
                )
                refined_body = response.content[0].text.strip()
            
            elif self.model_type == "openai":
                response = client.chat.completions.create(
                    model="gpt-4o",
                    max_tokens=2000,
                    messages=[{
                        "role": "user",
                        "content": refinement_prompt
                    }]
                )
                refined_body = response.choices[0].message.content.strip()
            
            # Format the refined body to remove hard line breaks
            refined_body = self._format_email_body(refined_body)
            
            logger.info(f"✅ Refined response based on feedback")
            
            return refined_body
            
        except Exception as e:
            logger.error(f"❌ Failed to refine response: {e}")
            # Return original on error
            return current_draft

