"""
Slack platform implementation.

Implements the BaseMessagingPlatform interface for Slack.
"""

from typing import Dict, Any, Optional, List
import logging
import re

from .base import BaseMessagingPlatform, MessageMetadata


def _markdown_to_mrkdwn(text: str) -> str:
    """Convert common Markdown formatting to Slack mrkdwn.

    Slack uses *bold*, _italic_, ~strikethrough~, and ```code```.
    Markdown uses **bold**, *italic* / _italic_, ~~strikethrough~~.
    """
    # Bold: **text** -> *text*  (do this before italic)
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    # Strikethrough: ~~text~~ -> ~text~
    text = re.sub(r'~~(.+?)~~', r'~\1~', text)
    # Headers: ### text -> *text* (bold, since Slack has no headers)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
    return text

logger = logging.getLogger(__name__)

# Lazy import for slack_sdk - only loaded when SlackPlatform is instantiated
slack_sdk = None
SlackApiError = None


def _ensure_slack_imported():
    """Ensure slack_sdk is imported. Raises ImportError if not available."""
    global slack_sdk, SlackApiError
    if slack_sdk is None:
        try:
            import slack_sdk as sdk_module
            from slack_sdk.errors import SlackApiError as error_class
            slack_sdk = sdk_module
            SlackApiError = error_class
        except ImportError:
            raise ImportError(
                "Slack integration requires slack-sdk\n"
                "Install with: pip install slack-sdk slack-bolt"
            )


class SlackPlatform(BaseMessagingPlatform):
    """Slack messaging platform implementation."""
    
    def __init__(self, bot_token: str):
        """
        Initialize Slack platform.
        
        Args:
            bot_token: Slack bot token (xoxb-...)
        """
        super().__init__()
        
        # Ensure slack_sdk is available
        _ensure_slack_imported()
        
        self.platform_name = 'slack'
        self.bot_token = bot_token
        self.client = slack_sdk.WebClient(token=bot_token)
        
        self.logger.info("Slack platform initialized")
    
    async def send_message(
        self,
        channel_id: str,
        content: str,
        thread_id: Optional[str] = None,
        blocks: Optional[List[Dict]] = None
    ) -> MessageMetadata:
        """
        Send message to Slack channel.
        
        Args:
            channel_id: Slack channel ID (C...)
            content: Message text
            thread_id: Optional thread timestamp for threaded replies
            blocks: Optional Slack block kit blocks
        
        Returns:
            MessageMetadata with message info
        """
        try:
            response = self.client.chat_postMessage(
                channel=channel_id,
                text=_markdown_to_mrkdwn(content),
                thread_ts=thread_id,
                blocks=blocks
            )
            
            # Extract message metadata
            message = response['message']
            ts = response['ts']
            
            return MessageMetadata(
                message_id=ts,
                channel_id=channel_id,
                user_id=message.get('user', 'bot'),
                username=message.get('username', 'bot'),
                timestamp=ts,
                thread_id=thread_id or ts,  # Use message ts as thread_id if not in thread
                platform='slack'
            )
        
        except SlackApiError as e:
            self.logger.error(f"Slack API error sending message: {e.response['error']}")
            raise
        except Exception as e:
            self.logger.error(f"Error sending Slack message: {e}", exc_info=True)
            raise
    
    async def find_user_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find a Slack user by display name, real name, or username.

        Returns the first match or None.
        """
        try:
            response = self.client.users_list()
            name_lower = name.lower()
            for user in response.get("members", []):
                if user.get("deleted") or user.get("is_bot"):
                    continue
                profile = user.get("profile", {})
                candidates = [
                    user.get("name", ""),
                    user.get("real_name", ""),
                    profile.get("display_name", ""),
                    profile.get("real_name_normalized", ""),
                    profile.get("display_name_normalized", ""),
                ]
                if any(name_lower in c.lower() for c in candidates if c):
                    return {
                        "id": user["id"],
                        "name": user.get("name", ""),
                        "real_name": user.get("real_name", ""),
                        "display_name": profile.get("display_name", ""),
                    }
        except SlackApiError as e:
            self.logger.error(f"Error listing users: {e.response['error']}")
        return None

    async def open_dm(self, user_id: str) -> Optional[str]:
        """Open a DM channel with a user, returning the channel ID."""
        try:
            response = self.client.conversations_open(users=[user_id])
            return response["channel"]["id"]
        except SlackApiError as e:
            self.logger.error(f"Error opening DM: {e.response['error']}")
            return None

    async def send_typing_indicator(self, channel_id: str) -> None:
        """
        Slack doesn't have a persistent typing indicator.
        
        We could post an ephemeral "thinking..." message but that's noisy.
        For now, this is a no-op.
        """
        pass
    
    async def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        """
        Get Slack channel information.
        
        Args:
            channel_id: Slack channel ID
        
        Returns:
            Channel metadata
        """
        try:
            response = self.client.conversations_info(channel=channel_id)
            channel = response['channel']
            
            return {
                'id': channel['id'],
                'name': channel['name'],
                'is_private': channel.get('is_private', False),
                'is_channel': channel.get('is_channel', True),
                'topic': channel.get('topic', {}).get('value', ''),
                'purpose': channel.get('purpose', {}).get('value', '')
            }
        
        except SlackApiError as e:
            self.logger.error(f"Error getting channel info: {e.response['error']}")
            return {'id': channel_id, 'name': 'Unknown', 'error': str(e)}
    
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """
        Get Slack user information.
        
        Args:
            user_id: Slack user ID
        
        Returns:
            User metadata
        """
        try:
            response = self.client.users_info(user=user_id)
            user = response['user']
            
            return {
                'id': user['id'],
                'name': user.get('name', ''),
                'real_name': user.get('real_name', ''),
                'display_name': user.get('profile', {}).get('display_name', ''),
                'email': user.get('profile', {}).get('email', ''),
                'is_bot': user.get('is_bot', False)
            }
        
        except SlackApiError as e:
            self.logger.error(f"Error getting user info: {e.response['error']}")
            return {'id': user_id, 'name': 'Unknown', 'error': str(e)}
    
    async def edit_message(
        self,
        channel_id: str,
        message_id: str,
        content: str,
        thread_id: Optional[str] = None
    ) -> None:
        """Edit an existing Slack message."""
        try:
            self.client.chat_update(
                channel=channel_id,
                ts=message_id,
                text=content
            )
        except SlackApiError as e:
            self.logger.error(f"Slack API error editing message: {e.response['error']}")
            raise

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        """Delete a Slack message."""
        try:
            self.client.chat_delete(
                channel=channel_id,
                ts=message_id
            )
        except SlackApiError as e:
            self.logger.error(f"Slack API error deleting message: {e.response['error']}")
            raise

    async def create_thread(
        self,
        channel_id: str,
        message_id: str,
        name: Optional[str] = None
    ) -> str:
        """
        Create a thread from a message.

        Slack threads are implicit — posting with thread_ts creates the thread.
        Returns the parent message's ts as the thread_id.
        """
        return message_id

    async def get_thread_messages(
        self,
        channel_id: str,
        thread_id: str
    ) -> List[Dict[str, Any]]:
        """Get all messages in a Slack thread."""
        try:
            response = self.client.conversations_replies(
                channel=channel_id,
                ts=thread_id
            )
            messages = []
            for msg in response.get('messages', []):
                messages.append({
                    'user_id': msg.get('user', 'unknown'),
                    'text': msg.get('text', ''),
                    'timestamp': msg.get('ts', ''),
                })
            return messages
        except SlackApiError as e:
            self.logger.error(f"Slack API error getting thread: {e.response['error']}")
            raise

    async def add_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str
    ) -> None:
        """Add an emoji reaction to a Slack message."""
        try:
            self.client.reactions_add(
                channel=channel_id,
                timestamp=message_id,
                name=emoji
            )
        except SlackApiError as e:
            # Ignore "already_reacted" errors
            if e.response['error'] != 'already_reacted':
                self.logger.error(f"Slack API error adding reaction: {e.response['error']}")
                raise

    async def remove_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str
    ) -> None:
        """Remove the bot's emoji reaction from a Slack message."""
        try:
            self.client.reactions_remove(
                channel=channel_id,
                timestamp=message_id,
                name=emoji
            )
        except SlackApiError as e:
            if e.response['error'] != 'no_reaction':
                self.logger.debug(f"Slack API error removing reaction: {e.response['error']}")

    def format_message(
        self,
        content: str,
        agent_name: Optional[str] = None
    ) -> str:
        """
        Format message for Slack using Slack markdown.
        
        Slack uses * for bold, _ for italic, ` for code.
        
        Args:
            content: Message content
            agent_name: Optional agent name for header
        
        Returns:
            Formatted message
        """
        if agent_name:
            # Slack uses * for bold
            return f"*{agent_name}*\n\n{content}"
        return content
