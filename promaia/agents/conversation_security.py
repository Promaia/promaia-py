"""
Security validation for conversational AI.

Handles user validation, rate limiting, and malicious input detection
to ensure safe and responsible conversational interactions.
"""

import re
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)


class ConversationSecurity:
    """
    Security layer for conversations.
    
    Provides:
    - User validation (only conversation initiator can continue)
    - Rate limiting (prevent spam)
    - Malicious input detection (prompt injection, SQL injection, etc.)
    - Input sanitization
    """
    
    # Suspicious patterns that indicate malicious intent
    SUSPICIOUS_PATTERNS = [
        r"ignore previous instructions",
        r"disregard system prompt",
        r"you are now",
        r"reveal your prompt",
        r"<script>",
        r"DROP TABLE",
        r"'; --",
        r"forget everything",
        r"new instructions:",
        r"system:",
        r"admin mode",
        r"override",
        r"sudo",
        r"\.\./",  # Path traversal (literal dots, not wildcards)
        r"file://",
        r"javascript:",
    ]
    
    def __init__(
        self,
        rate_limit_window: int = 60,  # seconds
        rate_limit_max: int = 10  # messages per window
    ):
        """
        Initialize security layer.
        
        Args:
            rate_limit_window: Time window for rate limiting (seconds)
            rate_limit_max: Max messages per window
        """
        self.rate_limit_cache: Dict[str, List[datetime]] = {}
        self.rate_limit_window = rate_limit_window
        self.rate_limit_max = rate_limit_max
        
        logger.info(f"Security layer initialized (rate limit: {rate_limit_max} msgs/{rate_limit_window}s)")
    
    async def validate_user(
        self,
        user_id: str,
        conversation
    ) -> bool:
        """
        Validate that user is authorized for this conversation.

        For direct conversations, only the initiator can continue.
        For tag_to_chat conversations, anyone in the thread can participate.

        Args:
            user_id: User attempting to send message
            conversation: ConversationState object

        Returns:
            True if authorized, False otherwise
        """
        # Tag-to-chat threads are open to anyone
        if getattr(conversation, 'conversation_type', 'direct') == 'tag_to_chat':
            return True

        is_authorized = user_id == conversation.user_id

        if not is_authorized:
            logger.warning(
                f"User {user_id} attempted to access conversation owned by {conversation.user_id}"
            )

        return is_authorized
    
    async def check_rate_limit(self, user_id: str) -> bool:
        """
        Check if user is within rate limits.
        
        Prevents spam and DOS attacks by limiting messages per time window.
        
        Args:
            user_id: User identifier
        
        Returns:
            True if within limits, False if exceeded
        """
        now = datetime.now()
        
        # Initialize or clean old entries
        if user_id not in self.rate_limit_cache:
            self.rate_limit_cache[user_id] = []
        
        # Remove entries outside window
        cutoff = now - timedelta(seconds=self.rate_limit_window)
        self.rate_limit_cache[user_id] = [
            ts for ts in self.rate_limit_cache[user_id]
            if ts > cutoff
        ]
        
        # Check limit
        if len(self.rate_limit_cache[user_id]) >= self.rate_limit_max:
            logger.warning(
                f"Rate limit exceeded for user {user_id}: "
                f"{len(self.rate_limit_cache[user_id])} msgs in {self.rate_limit_window}s"
            )
            return False
        
        # Add current timestamp
        self.rate_limit_cache[user_id].append(now)
        return True
    
    async def detect_malicious_input(
        self,
        message: str
    ) -> Tuple[bool, str]:
        """
        Detect malicious or suspicious input patterns.
        
        Checks for:
        - Prompt injection attempts
        - SQL injection patterns
        - XSS attempts
        - Path traversal
        - Excessive length
        
        Args:
            message: User message to check
        
        Returns:
            Tuple of (is_malicious: bool, reason: str)
        """
        # Check length
        if len(message) > 4000:
            return True, "Message too long (max 4000 chars)"
        
        # Check for suspicious patterns
        message_lower = message.lower()
        
        for pattern in self.SUSPICIOUS_PATTERNS:
            if re.search(pattern, message_lower, re.IGNORECASE):
                logger.warning(f"Suspicious pattern detected: {pattern}")
                return True, f"Suspicious pattern detected: {pattern}"
        
        # Check for excessive special characters (potential injection)
        special_char_ratio = sum(
            1 for c in message if not c.isalnum() and not c.isspace()
        ) / max(len(message), 1)
        
        if special_char_ratio > 0.3:  # More than 30% special chars
            logger.warning(f"Excessive special characters: {special_char_ratio:.2%}")
            return True, "Excessive special characters"
        
        return False, ""
    
    async def sanitize_input(self, message: str) -> str:
        """
        Sanitize user input.
        
        Removes or escapes potentially dangerous content while
        preserving the message's intent.
        
        Args:
            message: Raw user message
        
        Returns:
            Sanitized message
        """
        # Remove code blocks (can contain malicious code)
        message = re.sub(
            r'```.*?```',
            '[code block removed]',
            message,
            flags=re.DOTALL
        )
        
        # Remove inline code
        message = re.sub(r'`[^`]+`', '[code removed]', message)
        
        # Truncate if too long
        if len(message) > 4000:
            message = message[:4000] + "... [truncated]"
        
        # Remove leading/trailing whitespace
        message = message.strip()
        
        return message
    
    def get_rate_limit_stats(self, user_id: str) -> Dict[str, any]:
        """
        Get rate limit statistics for a user.
        
        Useful for debugging and monitoring.
        
        Args:
            user_id: User identifier
        
        Returns:
            Dictionary with rate limit stats
        """
        if user_id not in self.rate_limit_cache:
            return {
                'messages_in_window': 0,
                'remaining': self.rate_limit_max,
                'window_seconds': self.rate_limit_window
            }
        
        messages_count = len(self.rate_limit_cache[user_id])
        
        return {
            'messages_in_window': messages_count,
            'remaining': max(0, self.rate_limit_max - messages_count),
            'window_seconds': self.rate_limit_window,
            'rate_limited': messages_count >= self.rate_limit_max
        }
