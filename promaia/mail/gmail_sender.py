"""
Gmail Sender - Wrapper for sending emails via Gmail API.

Provides a clean interface for sending emails using the GmailConnector.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class GmailSender:
    """Send emails via Gmail API using GmailConnector."""
    
    def __init__(self, workspace: str, email: str):
        """
        Initialize Gmail sender.
        
        Args:
            workspace: Workspace name
            email: Gmail email address (database_id)
        """
        self.workspace = workspace
        self.email = email
        self.connector = None
    
    async def _get_connector(self):
        """Lazy load and connect Gmail connector."""
        if self.connector is None:
            from promaia.connectors.gmail_connector import GmailConnector
            
            config = {
                "database_id": self.email,
                "workspace": self.workspace
            }
            
            self.connector = GmailConnector(config)
            await self.connector.connect()
            
            logger.info(f"✅ Connected to Gmail for {self.email}")
        
        return self.connector
    
    async def send_reply(
        self,
        thread_id: str,
        message_id: str,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
        recipients: Optional[list] = None,
        attachments: Optional[list] = None
    ) -> bool:
        """
        Send a reply to an existing thread.

        Args:
            thread_id: Gmail thread ID
            message_id: Original message ID being replied to
            subject: Email subject
            body_text: Plain text body
            body_html: HTML body (optional)
            recipients: List of recipient email addresses (defaults to reply-all if not provided)
            attachments: List of file paths to attach (optional)

        Returns:
            True if sent successfully, False otherwise
        """
        try:
            connector = await self._get_connector()

            # If recipients are specified, use send_email with threading
            if recipients:
                # Join multiple recipients with comma
                to_field = ', '.join(recipients)

                success = await connector.send_email(
                    to=to_field,
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                    thread_id=thread_id,
                    in_reply_to=message_id,
                    attachments=attachments
                )
            else:
                # Use default reply behavior (if connector has send_reply)
                if hasattr(connector, 'send_reply'):
                    success = await connector.send_reply(
                        thread_id=thread_id,
                        message_id=message_id,
                        subject=subject,
                        body_text=body_text,
                        body_html=body_html
                    )
                else:
                    # Fallback to send_email (reply-all behavior)
                    logger.warning("send_reply not implemented, using send_email")
                    success = await connector.send_email(
                        to='',  # Will need to get from original message
                        subject=subject,
                        body_text=body_text,
                        body_html=body_html,
                        thread_id=thread_id,
                        in_reply_to=message_id
                    )
            
            if success:
                logger.info(f"✅ Successfully sent reply to thread {thread_id}")
            else:
                logger.error(f"❌ Failed to send reply to thread {thread_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Error sending reply: {e}")
            return False
    
    async def send_email(
        self,
        to: str,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
        attachments: Optional[list] = None
    ) -> bool:
        """
        Send a new email (not a reply).

        Args:
            to: Recipient email address
            subject: Email subject
            body_text: Plain text body
            body_html: HTML body (optional)
            attachments: List of file paths to attach (optional)

        Returns:
            True if sent successfully, False otherwise
        """
        try:
            connector = await self._get_connector()

            success = await connector.send_email(
                to=to,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                attachments=attachments
            )
            
            if success:
                logger.info(f"✅ Successfully sent email to {to}")
            else:
                logger.error(f"❌ Failed to send email to {to}")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Error sending email: {e}")
            return False

