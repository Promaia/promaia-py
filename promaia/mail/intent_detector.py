"""
Email Intent Detector - AI-powered detection of email composition intent.

Analyzes user messages and conversation context to determine when the user
wants to compose, reply to, or send an email.
"""
import logging
import json
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class EmailIntent:
    """Structured email intent detection result."""
    has_intent: bool
    confidence: float  # 0.0 to 1.0
    intent_type: str  # 'compose', 'reply', 'forward', 'send_draft', 'none'
    recipient_hints: List[str]  # Names or emails mentioned
    subject_hints: List[str]  # Keywords for subject
    thread_reference: Optional[str]  # Reference to thread ("that email from X")
    reasoning: str  # Why this intent was detected


class EmailIntentDetector:
    """Detects email composition intent using AI analysis."""

    DETECTION_PROMPT = """Analyze this user message to determine if they want to compose or send an email.

User message: "{user_message}"

Recent conversation context (last 3 messages):
{conversation_context}

Determine:
1. Does the user want to compose/send an email? (high/medium/low/none confidence)
2. What type of email action? (compose_new, reply_to_thread, forward, send_existing_draft, none)
3. Any recipient hints? (names, email addresses, or references like "him" or "the team")
4. Any subject hints? (topics mentioned)
5. Any thread references? (e.g., "that email from Sarah", "the VAT discussion")

Respond with ONLY a JSON object (no other text):
{{
  "has_intent": true/false,
  "confidence": 0.0-1.0,
  "intent_type": "compose_new|reply_to_thread|forward|send_existing_draft|none",
  "recipient_hints": ["name1", "email@example.com"],
  "subject_hints": ["keyword1", "keyword2"],
  "thread_reference": "description of referenced thread or null",
  "reasoning": "brief explanation"
}}

Examples:

Message: "Can you draft a reply to that email from Alice?"
Response: {{"has_intent": true, "confidence": 0.95, "intent_type": "reply_to_thread", "recipient_hints": ["Alice"], "subject_hints": [], "thread_reference": "email from Alice", "reasoning": "User explicitly asked to draft a reply to a specific person's email"}}

Message: "Send this to John and cc Maria"
Response: {{"has_intent": true, "confidence": 0.98, "intent_type": "compose_new", "recipient_hints": ["John", "Maria"], "subject_hints": [], "thread_reference": null, "reasoning": "Direct instruction to send with explicit recipients"}}

Message: "What's the weather today?"
Response: {{"has_intent": false, "confidence": 0.0, "intent_type": "none", "recipient_hints": [], "subject_hints": [], "thread_reference": null, "reasoning": "General question with no email-related intent"}}

Message: "Email the team about the Q4 results"
Response: {{"has_intent": true, "confidence": 0.9, "intent_type": "compose_new", "recipient_hints": ["team"], "subject_hints": ["Q4 results"], "thread_reference": null, "reasoning": "Clear instruction to compose email with topic specified"}}
"""

    def __init__(self):
        """Initialize intent detector."""
        self.ai_client = None
        self.model_type = None

    def _get_ai_client(self):
        """Get AI client for intent detection."""
        if self.ai_client is not None:
            return self.ai_client

        import os
        from anthropic import Anthropic
        from openai import OpenAI

        # Try Anthropic first (preferred)
        if os.getenv("ANTHROPIC_API_KEY"):
            self.ai_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), max_retries=5)
            self.model_type = "anthropic"
            logger.debug("Using Anthropic for intent detection")
            return self.ai_client

        # Fallback to OpenAI
        if os.getenv("OPENAI_API_KEY"):
            self.ai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            self.model_type = "openai"
            logger.debug("Using OpenAI for intent detection")
            return self.ai_client

        logger.warning("No AI API keys found for intent detection")
        return None

    def detect_intent(
        self,
        user_message: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> EmailIntent:
        """
        Detect email intent in user message.

        Args:
            user_message: The user's current message
            conversation_history: Recent messages for context (last 3-5 messages)

        Returns:
            EmailIntent with detection results
        """
        # Quick keyword check for obvious non-email messages
        if self._is_obviously_not_email(user_message):
            return EmailIntent(
                has_intent=False,
                confidence=0.0,
                intent_type='none',
                recipient_hints=[],
                subject_hints=[],
                thread_reference=None,
                reasoning="Quick keyword check - no email indicators"
            )

        # Build conversation context
        context_text = self._format_conversation_context(conversation_history or [])

        # Build detection prompt
        prompt = self.DETECTION_PROMPT.format(
            user_message=user_message,
            conversation_context=context_text
        )

        # Get AI analysis
        try:
            client = self._get_ai_client()
            if not client:
                # Fallback to basic keyword detection
                return self._basic_keyword_detection(user_message)

            if self.model_type == "anthropic":
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",  # Fast, cheap model for intent detection
                    max_tokens=500,
                    messages=[{"role": "user", "content": prompt}]
                )
                response_text = response.content[0].text
            else:  # openai
                response = client.chat.completions.create(
                    model="gpt-4o-mini",  # Fast, cheap model
                    max_tokens=500,
                    messages=[{"role": "user", "content": prompt}]
                )
                response_text = response.choices[0].message.content

            # Parse JSON response - handle markdown code blocks and surrounding text
            cleaned = response_text.strip()
            if not cleaned:
                logger.warning("Intent detection returned empty response")
                return self._basic_keyword_detection(user_message)
            # Strip markdown code fences if present
            if '```' in cleaned:
                import re
                match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', cleaned, re.DOTALL)
                if match:
                    cleaned = match.group(1).strip()
            # Try to extract JSON object if there's surrounding text
            if not cleaned.startswith('{'):
                start = cleaned.find('{')
                if start != -1:
                    end = cleaned.rfind('}')
                    if end > start:
                        cleaned = cleaned[start:end + 1]
            intent_data = json.loads(cleaned)

            return EmailIntent(
                has_intent=intent_data.get('has_intent', False),
                confidence=float(intent_data.get('confidence', 0.0)),
                intent_type=intent_data.get('intent_type', 'none'),
                recipient_hints=intent_data.get('recipient_hints', []),
                subject_hints=intent_data.get('subject_hints', []),
                thread_reference=intent_data.get('thread_reference'),
                reasoning=intent_data.get('reasoning', '')
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse intent detection JSON: {e}")
            return self._basic_keyword_detection(user_message)
        except Exception as e:
            # Check for connection errors
            error_str = str(e).lower()
            if 'connection' in error_str or 'network' in error_str or 'timeout' in error_str:
                logger.warning(f"Intent detection skipped due to connection issue: {e}")
            else:
                logger.error(f"Error in intent detection: {e}")
            return self._basic_keyword_detection(user_message)

    def _format_conversation_context(self, messages: List[Dict[str, str]]) -> str:
        """Format recent messages for context."""
        if not messages:
            return "(No previous context)"

        # Take last 3 messages
        recent = messages[-3:]
        lines = []
        for msg in recent:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')[:200]  # Truncate long messages
            lines.append(f"{role}: {content}")

        return '\n'.join(lines)

    # Keywords that must be present to even consider calling the AI
    EMAIL_GATE_KEYWORDS = [
        'email', 'e-mail', 'mail', 'send', 'draft', 'compose',
        'reply', 'forward', 'cc', 'bcc', 'inbox', 'gmail',
    ]

    def _is_obviously_not_email(self, message: str) -> bool:
        """
        Quick check: only proceed to AI if the message contains
        an email-related keyword. Returns True if no keywords found.
        """
        message_lower = message.lower().strip()

        if len(message_lower) < 5:
            return True

        # Commands are never email intent
        if message_lower.startswith('/'):
            return True

        # Must contain at least one email keyword to warrant an AI call
        return not any(kw in message_lower for kw in self.EMAIL_GATE_KEYWORDS)

    def _basic_keyword_detection(self, message: str) -> EmailIntent:
        """
        Fallback to basic keyword detection if AI unavailable.

        Less sophisticated but better than nothing.
        """
        message_lower = message.lower()

        # Email action keywords
        email_keywords = ['email', 'send', 'draft', 'compose', 'reply', 'forward']
        has_keyword = any(kw in message_lower for kw in email_keywords)

        if not has_keyword:
            return EmailIntent(
                has_intent=False,
                confidence=0.0,
                intent_type='none',
                recipient_hints=[],
                subject_hints=[],
                thread_reference=None,
                reasoning="Keyword check - no email keywords found"
            )

        # Determine intent type
        if 'reply' in message_lower or 'respond' in message_lower:
            intent_type = 'reply_to_thread'
        elif 'forward' in message_lower:
            intent_type = 'forward'
        elif 'draft' in message_lower:
            intent_type = 'compose_new'
        else:
            intent_type = 'compose_new'

        # Simple recipient extraction
        import re
        # Look for "to X" patterns
        to_pattern = r'(?:to|cc)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)'
        recipients = re.findall(to_pattern, message)

        return EmailIntent(
            has_intent=True,
            confidence=0.7,  # Lower confidence for keyword-based
            intent_type=intent_type,
            recipient_hints=recipients,
            subject_hints=[],
            thread_reference=None,
            reasoning="Keyword-based detection (AI unavailable)"
        )
