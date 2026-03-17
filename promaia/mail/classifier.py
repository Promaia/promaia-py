"""
Email Classifier - AI-based email classification.

Determines if an email:
- Pertains to the user
- Is spam/promotional
- Requires a response
"""
import json
import logging
import os
from typing import Dict, Any, Optional

from promaia.utils.env_writer import get_prompts_dir

logger = logging.getLogger(__name__)


class EmailClassifier:
    """AI-based email classification using existing chat infrastructure."""
    
    def __init__(self):
        """Initialize classifier with AI client."""
        # Will use the existing AI client infrastructure
        self.ai_client = None
        self.model_type = None
        # Cache for loaded prompts per workspace
        self._prompt_cache = {}

    def _load_classification_prompt(self, workspace: str) -> str:
        """
        Load classification prompt template from file.

        Tries to load workspace-specific prompt first (e.g., maia_mail_classification_prompt_acme.md),
        falls back to generic prompt if not found.

        Args:
            workspace: Workspace name

        Returns:
            Prompt template string
        """
        # Check cache first
        if workspace in self._prompt_cache:
            return self._prompt_cache[workspace]

        # Try workspace-specific prompt first
        workspace_prompt_file = str(get_prompts_dir() / f"maia_mail_classification_prompt_{workspace}.md")

        try:
            with open(workspace_prompt_file, 'r') as f:
                prompt = f.read()
                self._prompt_cache[workspace] = prompt
                logger.info(f"Loaded workspace-specific classification prompt for '{workspace}'")
                return prompt
        except FileNotFoundError:
            logger.debug(f"No workspace-specific prompt found at {workspace_prompt_file}, trying generic")

        # Fall back to generic prompt
        generic_prompt_file = str(get_prompts_dir() / "maia_mail_classification_prompt.md")
        try:
            with open(generic_prompt_file, 'r') as f:
                prompt = f.read()
                self._prompt_cache[workspace] = prompt
                logger.warning(f"Using generic classification prompt for workspace '{workspace}' (no workspace-specific prompt found)")
                return prompt
        except FileNotFoundError:
            logger.error(f"Classification prompt file not found: {generic_prompt_file}")
            raise
        except Exception as e:
            logger.error(f"Error loading classification prompt: {e}")
            raise
    
    def _get_ai_client(self):
        """Get AI client from existing chat infrastructure."""
        if self.ai_client is not None:
            return self.ai_client
        
        import os
        from anthropic import Anthropic
        from openai import OpenAI
        
        # Try Anthropic first (preferred)
        if os.getenv("ANTHROPIC_API_KEY"):
            self.ai_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), base_url=os.environ.get("ANTHROPIC_BASE_URL"), max_retries=5)
            self.model_type = "anthropic"
            logger.info("Using Anthropic for email classification")
            return self.ai_client
        
        # Fall back to OpenAI
        if os.getenv("OPENAI_API_KEY"):
            self.ai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            self.model_type = "openai"
            logger.info("Using OpenAI for email classification")
            return self.ai_client
        
        raise ValueError("No AI API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY")
    
    async def classify(self, email_thread: Dict[str, Any], user_email: str, workspace: str) -> Dict[str, Any]:
        """
        Classify an email thread.
        
        Args:
            email_thread: Dict containing email data (from, subject, body, etc.)
            user_email: Email address of the user (from gmail database_id)
            workspace: Workspace name
            
        Returns:
            Dict with classification results:
            {
                "pertains_to_me": bool,
                "is_spam": bool,
                "addressed_to_user": bool/str,
                "requires_response": bool,
                "reasoning": str
            }
        """
        try:
            # Extract email data
            from_addr = email_thread.get('from', 'Unknown')
            to_addr = email_thread.get('to', 'Unknown')
            subject = email_thread.get('subject', 'No Subject')
            date = email_thread.get('date', 'Unknown')
            body = email_thread.get('conversation_body', '') or email_thread.get('body', '')
            thread_context = email_thread.get('thread_context', 'No previous context')
            
            # Truncate body if too long (keep first 1000 chars)
            if len(body) > 1000:
                body = body[:1000] + "\n[... truncated ...]"

            # Load workspace-specific prompt
            prompt_template = self._load_classification_prompt(workspace)

            # Build prompt with user identity
            prompt = prompt_template.format(
                user_email=user_email,
                workspace=workspace,
                from_addr=from_addr,
                to_addr=to_addr,
                subject=subject,
                date=date,
                body=body,
                thread_context=thread_context
            )
            
            # Get AI client
            client = self._get_ai_client()
            
            # Call AI based on model type
            if self.model_type == "anthropic":
                response = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=500,
                    messages=[{
                        "role": "user",
                        "content": prompt
                    }]
                )
                response_text = response.content[0].text
            
            elif self.model_type == "openai":
                response = client.chat.completions.create(
                    model="gpt-4o",
                    max_tokens=500,
                    messages=[{
                        "role": "user",
                        "content": prompt
                    }]
                )
                response_text = response.choices[0].message.content
            
            else:
                raise ValueError(f"Unknown model type: {self.model_type}")
            
            # Parse JSON response
            # Extract JSON from response (it might have markdown code blocks)
            response_text = response_text.strip()
            if response_text.startswith('```json'):
                response_text = response_text[7:]  # Remove ```json
            if response_text.startswith('```'):
                response_text = response_text[3:]  # Remove ```
            if response_text.endswith('```'):
                response_text = response_text[:-3]  # Remove trailing ```
            response_text = response_text.strip()
            
            classification = json.loads(response_text)
            
            # Validate required fields
            required_fields = ['pertains_to_me', 'is_spam', 'addressed_to_user', 'requires_response', 'reasoning']
            for field in required_fields:
                if field not in classification:
                    raise ValueError(f"Missing required field in classification: {field}")
            
            logger.info(
                f"Classified email from {from_addr}: "
                f"pertains={classification['pertains_to_me']}, "
                f"spam={classification['is_spam']}, "
                f"addressed_to_user={classification['addressed_to_user']}, "
                f"requires_response={classification['requires_response']}"
            )
            
            return classification
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Failed to parse classification JSON: {e}")
            logger.error(f"Response was: {response_text}")
            # Return conservative defaults
            return {
                "pertains_to_me": True,
                "is_spam": False,
                "addressed_to_user": "ambiguous",
                "requires_response": True,
                "reasoning": f"Classification failed (JSON parse error), defaulting to requiring response"
            }
        
        except Exception as e:
            logger.error(f"❌ Classification failed: {e}")
            # Return conservative defaults (assume it needs attention)
            return {
                "pertains_to_me": True,
                "is_spam": False,
                "addressed_to_user": "ambiguous",
                "requires_response": True,
                "reasoning": f"Classification error: {str(e)}, defaulting to requiring response"
            }
    
    def should_generate_draft(self, classification: Dict[str, Any]) -> bool:
        """
        Determine if we should generate a draft based on classification.
        
        Args:
            classification: Result from classify()
            
        Returns:
            True if we should generate a draft (includes both "pending" and "unsure")
        """
        # Don't generate if spam
        if classification.get('is_spam', False):
            return False
        
        # Don't generate if clearly addressed to someone else
        addressed = classification.get('addressed_to_user', True)
        if addressed is False:  # Explicitly False, not just falsy
            return False
        
        # Generate if pertains to user and requires response
        # (includes ambiguous cases as "unsure")
        return (
            classification.get('pertains_to_me', False) and
            classification.get('requires_response', False)
        )
    
    def get_draft_status(self, classification: Dict[str, Any]) -> str:
        """
        Determine what status a draft should have based on classification.
        
        Args:
            classification: Result from classify()
            
        Returns:
            "pending", "unsure", or "skipped"
        """
        # Skip if spam or clearly addressed to someone else
        if classification.get('is_spam', False):
            return "skipped"
        
        addressed = classification.get('addressed_to_user', True)
        if addressed is False:  # Explicitly addressed to someone else
            return "skipped"
        
        # Skip if doesn't pertain or doesn't require response
        if not classification.get('pertains_to_me', False):
            return "skipped"
        
        if not classification.get('requires_response', False):
            return "skipped"
        
        # Unsure if ambiguous recipient
        if addressed == "ambiguous":
            return "unsure"
        
        # Otherwise pending
        return "pending"

