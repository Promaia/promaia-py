"""
Base messaging platform abstraction.

Defines the common interface that all messaging platforms must implement.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class MessageMetadata:
    """
    Platform-agnostic message metadata.
    
    This structure is returned after sending a message and contains
    all the information needed to track and respond to messages.
    """
    message_id: str
    channel_id: str
    user_id: str
    username: str
    timestamp: str
    thread_id: Optional[str] = None  # For threaded conversations
    platform: str = "unknown"  # "slack" or "discord"


class BaseMessagingPlatform(ABC):
    """
    Abstract base class for messaging platforms.
    
    All messaging platforms (Slack, Discord, Teams, etc.) must implement
    this interface to work with the conversation manager.
    """
    
    def __init__(self):
        self.platform_name = "unknown"
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
    
    @abstractmethod
    async def send_message(
        self,
        channel_id: str,
        content: str,
        thread_id: Optional[str] = None,
        blocks: Optional[List[Dict]] = None
    ) -> MessageMetadata:
        """
        Send a message to the platform.
        
        Args:
            channel_id: Platform-specific channel identifier
            content: Message text content
            thread_id: Optional thread/conversation ID for replies
            blocks: Optional rich formatting blocks (platform-specific)
        
        Returns:
            MessageMetadata with information about the sent message
        """
        pass
    
    @abstractmethod
    async def send_typing_indicator(self, channel_id: str) -> None:
        """
        Show typing indicator to user.
        
        Args:
            channel_id: Platform-specific channel identifier
        """
        pass
    
    @abstractmethod
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """
        Get information about a user.
        
        Args:
            user_id: Platform-specific user identifier
        
        Returns:
            Dictionary with user metadata (name, email, etc.)
        """
        pass
    
    @abstractmethod
    def format_message(
        self,
        content: str,
        agent_name: Optional[str] = None
    ) -> str:
        """
        Format message for the platform.
        
        Different platforms use different markup (Slack uses *, Discord uses **).
        This method handles platform-specific formatting.
        
        Args:
            content: Message content
            agent_name: Optional agent name to include in header
        
        Returns:
            Formatted message string
        """
        pass
    
    @abstractmethod
    async def edit_message(
        self,
        channel_id: str,
        message_id: str,
        content: str,
        thread_id: Optional[str] = None
    ) -> None:
        """
        Edit an existing message.

        Args:
            channel_id: Platform-specific channel identifier
            message_id: Platform-specific message identifier
            content: New message content
            thread_id: Optional thread ID (required by some platforms)
        """
        pass

    @abstractmethod
    async def delete_message(self, channel_id: str, message_id: str) -> None:
        """
        Delete a message.

        Args:
            channel_id: Platform-specific channel identifier
            message_id: Platform-specific message identifier
        """
        pass

    @abstractmethod
    async def create_thread(
        self,
        channel_id: str,
        message_id: str,
        name: Optional[str] = None
    ) -> str:
        """
        Create a thread from a message. Returns thread_id.

        Args:
            channel_id: Platform-specific channel identifier
            message_id: Message to create thread from
            name: Optional thread name (Discord only)

        Returns:
            Thread identifier
        """
        pass

    @abstractmethod
    async def get_thread_messages(
        self,
        channel_id: str,
        thread_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get all messages in a thread.

        Args:
            channel_id: Platform-specific channel identifier
            thread_id: Thread identifier

        Returns:
            List of message dicts with 'user_id', 'text', 'timestamp' keys
        """
        pass

    @abstractmethod
    async def add_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str
    ) -> None:
        """
        Add an emoji reaction to a message.

        Args:
            channel_id: Platform-specific channel identifier
            message_id: Platform-specific message identifier
            emoji: Emoji name (without colons)
        """
        pass

    async def remove_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str
    ) -> None:
        """
        Remove a bot emoji reaction from a message.

        Args:
            channel_id: Platform-specific channel identifier
            message_id: Platform-specific message identifier
            emoji: Emoji name (without colons)
        """
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} platform={self.platform_name}>"
