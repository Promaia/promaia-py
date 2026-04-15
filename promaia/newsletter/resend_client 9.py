"""
Simple Resend email client for newsletters.
"""
import os
import resend
from typing import List, Optional, Dict, Any
from promaia.utils.display import print_text, print_separator


class ResendClient:
    """Simple client for sending emails via Resend API."""

    def __init__(self, api_key: Optional[str] = None, silent: bool = False):
        """Initialize Resend client."""
        self.api_key = api_key or os.getenv("RESEND_API_KEY")
        if not self.api_key:
            raise ValueError("RESEND_API_KEY environment variable is required")

        # Set the API key for resend
        resend.api_key = self.api_key
        self.silent = silent
    
    def send_newsletter(
        self, 
        subject: str,
        plain_text: str,
        html_content: Optional[str] = None,
        from_email: Optional[str] = None,
        from_name: Optional[str] = None,
        to_emails: Optional[List[str]] = None,
        reply_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a newsletter email using Resend.
        
        Args:
            subject: Email subject line
            plain_text: Plain text content
            html_content: Optional HTML content (if None, will be generated from plain_text)
            from_email: Sender email (defaults to env var)
            from_name: Sender name (defaults to env var)
            to_emails: List of recipient emails (defaults to test email)
            reply_to: Reply-to email address (defaults to a working email)
            
        Returns:
            Dictionary with send results
        """
        # Default values from environment
        from_email = from_email or os.getenv("RESEND_FROM_EMAIL", "newsletter@koiibenvenutto.com")
        from_name = from_name or os.getenv("RESEND_FROM_NAME", "Koii Benvenutto")
        
        # Set reply-to to a working email address (use test email as default working address)
        if not reply_to:
            reply_to = os.getenv("RESEND_REPLY_TO", os.getenv("RESEND_TEST_EMAIL", "koii@koiibenvenutto.com"))
        
        # For testing, send to yourself or test emails
        if not to_emails:
            test_email = os.getenv("RESEND_TEST_EMAIL", "koii@koiibenvenutto.com")
            to_emails = [test_email]
        
        # If no HTML content provided, use plain text for both
        # (The calling code should provide proper HTML now)
        if not html_content:
            html_content = self._plain_text_to_html(plain_text)
        
        try:
            # Send the email
            response = resend.Emails.send({
                "from": f"{from_name} <{from_email}>",
                "to": to_emails,
                "subject": subject,
                "text": plain_text,
                "html": html_content,
                "reply_to": reply_to
            })

            return {
                "success": True,
                "email_id": response.get("id"),
                "response": response
            }

        except Exception as e:
            if not self.silent:
                print(f"❌ Error sending email: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def _plain_text_to_html(self, plain_text: str) -> str:
        """
        Convert plain text to simple, clean HTML with clickable links.
        
        Args:
            plain_text: Plain text content
            
        Returns:
            Simple HTML version with clickable links
        """
        import re
        
        lines = plain_text.split('\n')
        html_lines = []
        
        for line in lines:
            line = line.strip()
            
            # Skip empty lines
            if not line:
                html_lines.append('<br>')
                continue
            
            # Headers (lines with === or ---)
            if line.startswith('=') and len(line) > 5:
                continue  # Skip separator lines
            
            # Check if previous line might be a title (look for === after it)
            if len(html_lines) > 0 and lines and html_lines[-1] != '<br>':
                next_idx = lines.index(line.strip()) if line.strip() in lines else -1
                if next_idx < len(lines) - 1 and lines[next_idx + 1].startswith('='):
                    # This is a title
                    html_lines[-1] = f'<h1 style="font-size: 24px; font-weight: bold; margin: 20px 0 10px 0; color: #333;">{html_lines[-1]}</h1>'
                    continue
            
            # Subheaders (lines with --- or lines that end with :)
            if line.startswith('-') and len(line) > 5:
                continue  # Skip separator lines
            elif line.endswith(':') and len(line) < 50:
                html_lines.append(f'<h2 style="font-size: 18px; font-weight: bold; margin: 15px 0 8px 0; color: #555;">{line}</h2>')
                continue
            
            # Cover image (convert to actual image)
            if line.startswith('🖼️ http'):
                image_url = line.replace('🖼️ ', '').strip()
                html_lines.append(f'<p style="margin: 16px 0; text-align: center;"><img src="{image_url}" alt="Cover Image" style="max-width: 100%; height: auto; display: block; margin: 0 auto; border-radius: 8px;" /></p>')
                continue
            
            # URLs (convert to clickable links)
            if line.startswith('http'):
                html_lines.append(f'<p style="margin: 8px 0;"><a href="{line}" style="color: #007acc; text-decoration: underline;">{line}</a></p>')
                continue
            
            # Lines that contain "Read on website:" - make the URL clickable
            if "Read on website:" in line:
                url_match = re.search(r'https?://[^\s]+', line)
                if url_match:
                    url = url_match.group(0)
                    # Replace the entire line with just the clickable link
                    html_lines.append(f'<p style="margin: 8px 0; line-height: 1.5; color: #333;"><a href="{url}" style="color: #007acc; text-decoration: none;">Read on website</a></p>')
                    continue
            
            # Lines that contain "Subscribe:" - make the URL clickable
            if "Forwarded this email? Subscribe:" in line:
                url_match = re.search(r'https?://[^\s]+', line)
                if url_match:
                    url = url_match.group(0)
                    # Replace the entire line with just the clickable link
                    html_lines.append(f'<p style="margin: 8px 0; line-height: 1.5; color: #333;">Forwarded this email? <a href="{url}" style="color: #007acc; text-decoration: none;">Subscribe here</a></p>')
                    continue
            
            # Regular paragraphs - check for any URLs within the text and make them clickable
            url_pattern = r'(https?://[^\s]+)'
            if re.search(url_pattern, line):
                clickable_line = re.sub(url_pattern, r'<a href="\1" style="color: #007acc; text-decoration: underline;">\1</a>', line)
                html_lines.append(f'<p style="margin: 8px 0; line-height: 1.5; color: #333;">{clickable_line}</p>')
            else:
                html_lines.append(f'<p style="margin: 8px 0; line-height: 1.5; color: #333;">{line}</p>')
        
        # Wrap in minimal HTML structure
        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; line-height: 1.6;">
                {''.join(html_lines)}
        </body>
        </html>
        """
        
        return html.strip()

    def send_broadcast_to_audience(
        self,
        subject: str,
        plain_text: str,
        html_content: Optional[str] = None,
        from_email: Optional[str] = None,
        from_name: Optional[str] = None,
        audience_id: Optional[str] = None,
        reply_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a broadcast email to a Resend audience.
        
        Args:
            subject: Email subject line
            plain_text: Plain text content
            html_content: Optional HTML content (if None, will be generated from plain_text)
            from_email: Sender email (defaults to env var)
            from_name: Sender name (defaults to env var)
            audience_id: Resend audience ID (defaults to env var)
            reply_to: Reply-to email address (defaults to a working email)
            
        Returns:
            Dictionary with send results
        """
        # Default values from environment
        from_email = from_email or os.getenv("RESEND_FROM_EMAIL", "newsletter@koiibenvenutto.com")
        from_name = from_name or os.getenv("RESEND_FROM_NAME", "Koii Benvenutto")
        audience_id = audience_id or os.getenv("RESEND_AUDIENCE_ID")
        
        if not audience_id:
            return {
                "success": False,
                "error": "No audience ID provided. Set RESEND_AUDIENCE_ID environment variable."
            }
        
        # Set reply-to to a working email address
        if not reply_to:
            reply_to = os.getenv("RESEND_REPLY_TO", os.getenv("RESEND_TEST_EMAIL", "koii@koiibenvenutto.com"))
        
        # If no HTML content provided, convert plain text to simple HTML
        # (The calling code should provide proper HTML now)
        if not html_content:
            html_content = self._plain_text_to_html(plain_text)
        
        # Add unsubscribe link to HTML content
        if "RESEND_UNSUBSCRIBE_URL" not in html_content:
            html_content = html_content.replace(
                "</body>",
                '<p style="margin: 20px 0 10px 0; font-size: 12px; color: #888;"><a href="{{{RESEND_UNSUBSCRIBE_URL}}}" style="color: #888;">Unsubscribe</a></p></body>'
            )
        
        try:
            # Two-step process: CREATE then SEND broadcast
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            # Step 1: Create the broadcast
            create_params = {
                "audience_id": audience_id,  # Correct field name from documentation
                "from": f"{from_name} <{from_email}>",
                "reply_to": reply_to,
                "subject": subject,
                "text": plain_text,
                "html": html_content
            }

            create_url = "https://api.resend.com/broadcasts"
            create_response = requests.post(create_url, json=create_params, headers=headers)

            if create_response.status_code not in [200, 201]:
                raise Exception(f"Failed to create broadcast (status {create_response.status_code}): {create_response.text}")

            create_data = create_response.json()
            broadcast_id = create_data.get('id')
            if not broadcast_id:
                raise Exception(f"No broadcast ID returned from create: {create_data}")

            # Step 2: Send the broadcast
            send_url = f"https://api.resend.com/broadcasts/{broadcast_id}/send"
            send_response = requests.post(send_url, headers=headers)

            if send_response.status_code not in [200, 201]:
                raise Exception(f"Failed to send broadcast (status {send_response.status_code}): {send_response.text}")

            send_data = send_response.json()

            return {
                "success": True,
                "broadcast_id": broadcast_id,
                "response": {
                    "create": create_data,
                    "send": send_data
                }
            }

        except Exception as e:
            if not self.silent:
                print(f"❌ Error sending broadcast: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }


# Default client instance
resend_client = None

def get_resend_client(silent: bool = False) -> ResendClient:
    """Get or create default Resend client."""
    global resend_client
    if resend_client is None or resend_client.silent != silent:
        resend_client = ResendClient(silent=silent)
    return resend_client 