"""
Recipient Selector - Interactive interface for selecting email recipients.

Allows user to choose between reply to sender, reply all, or custom/forward modes.
"""
import re
from typing import List, Dict, Tuple, Optional
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import TextArea


class RecipientMode:
    """Recipient selection modes."""
    REPLY_SENDER = "reply_sender"
    REPLY_ALL = "reply_all"
    CUSTOM = "custom"


class RecipientSelector:
    """Interactive recipient selector for emails."""
    
    def __init__(
        self,
        from_addr: str,
        to_addr: str,
        cc_addr: Optional[str] = None,
        thread_context: Optional[str] = None,
        user_email: Optional[str] = None,
        default_recipients: Optional[List[str]] = None,
        default_cc: Optional[List[str]] = None,
    ):
        """
        Initialize recipient selector.

        Args:
            from_addr: The sender's email address
            to_addr: The original TO field
            cc_addr: The original CC field
            thread_context: Optional thread context to extract more recipients
            user_email: User's email address (to exclude from reply all)
            default_recipients: Pre-populated recipients (starts in Custom mode)
            default_cc: Pre-populated CC recipients (merged into recipient list)
        """
        self.from_addr = from_addr
        self.to_addr = to_addr
        self.cc_addr = cc_addr or ""
        self.thread_context = thread_context or ""
        self.user_email = user_email.lower() if user_email else None

        # Extract all unique email addresses from thread
        self.all_recipients = self._extract_all_recipients()

        # Merge default recipients/CC into the recipient list if provided
        if default_recipients or default_cc:
            extras = set()
            for email in (default_recipients or []):
                extras.add(email.strip().lower())
            for email in (default_cc or []):
                extras.add(email.strip().lower())
            # Add any new addresses not already in the list
            for email in sorted(extras):
                if email not in self.all_recipients:
                    self.all_recipients.append(email)

        # State
        if default_recipients:
            # Pre-populated: start in Custom mode with only the specified recipients selected
            self.mode = RecipientMode.CUSTOM
            default_set = {e.strip().lower() for e in default_recipients}
            if default_cc:
                default_set.update(e.strip().lower() for e in default_cc)
            self.selected_recipients = default_set
        else:
            self.mode = RecipientMode.REPLY_ALL  # Default to reply all
            self.selected_recipients = set(self.all_recipients)  # Initially all selected
        self.current_selection = 0  # For custom mode navigation
        self.editing_index = None  # Track which recipient is being edited
        self.edit_buffer = ""  # Buffer for editing email address
    
    def _extract_all_recipients(self) -> List[str]:
        """Extract all unique email addresses from FROM, TO, CC, and thread."""
        recipients = set()
        
        # Add FROM address
        recipients.update(self._extract_emails_from_field(self.from_addr))
        
        # Add TO addresses
        recipients.update(self._extract_emails_from_field(self.to_addr))
        
        # Add CC addresses
        recipients.update(self._extract_emails_from_field(self.cc_addr))
        
        # Extract from thread context (look for email patterns)
        thread_emails = self._extract_emails_from_text(self.thread_context)
        recipients.update(thread_emails)
        
        # Remove user's own email address
        if self.user_email:
            recipients.discard(self.user_email)
        
        # Return as sorted list
        return sorted(list(recipients))
    
    def _extract_emails_from_field(self, field: str) -> List[str]:
        """Extract email addresses from a field like 'Name <email>' or 'email1, email2'."""
        if not field:
            return []
        
        emails = []
        # Pattern: email in angle brackets or standalone
        email_pattern = r'<([^>]+@[^>]+)>|([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
        
        matches = re.findall(email_pattern, field)
        for match in matches:
            email = match[0] if match[0] else match[1]
            if email:
                emails.append(email.strip().lower())
        
        return emails
    
    def _extract_emails_from_text(self, text: str) -> List[str]:
        """Extract email addresses from plain text."""
        if not text:
            return []
        
        email_pattern = r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b'
        emails = re.findall(email_pattern, text)
        return [email.lower() for email in emails[:10]]  # Limit to 10 from thread
    
    def get_sender_emails(self) -> List[str]:
        """Get just the sender's email(s)."""
        return self._extract_emails_from_field(self.from_addr)
    
    def get_all_recipients_emails(self) -> List[str]:
        """Get all recipients (for reply all)."""
        return self.all_recipients
    
    def get_selected_recipients(self) -> List[str]:
        """Get currently selected recipients based on mode."""
        if self.mode == RecipientMode.REPLY_SENDER:
            return self.get_sender_emails()
        elif self.mode == RecipientMode.REPLY_ALL:
            return self.all_recipients
        else:  # CUSTOM
            # Filter out placeholders and return only selected recipients
            return [r for r in self.all_recipients 
                    if r in self.selected_recipients and not r.startswith("__editing_")]
    
    def _render_mode_selector(self, current_mode: str) -> str:
        """Render the mode selector line."""
        modes = [
            ("Reply to Sender", RecipientMode.REPLY_SENDER),
            ("Reply All", RecipientMode.REPLY_ALL),
            ("Custom/Forward", RecipientMode.CUSTOM)
        ]
        
        parts = []
        for label, mode in modes:
            if mode == current_mode:
                parts.append(f"▶ {label}")
            else:
                parts.append(f"  {label}")
        
        return "  •  ".join(parts)
    
    def _render_recipient_list(self, mode: str, current_selection: int, selected_set: set) -> List[str]:
        """Render the recipient checklist."""
        lines = []
        
        if mode == RecipientMode.REPLY_SENDER:
            lines.append("\033[2m(Replying to sender only)\033[0m")
            for email in self.get_sender_emails():
                lines.append(f"\033[2m  → {email}\033[0m")
        elif mode == RecipientMode.REPLY_ALL:
            lines.append("\033[2m(Replying to all recipients)\033[0m")
            for email in self.all_recipients[:5]:  # Show first 5
                lines.append(f"\033[2m  → {email}\033[0m")
            if len(self.all_recipients) > 5:
                lines.append(f"\033[2m  ... and {len(self.all_recipients) - 5} more\033[0m")
        else:  # CUSTOM
            lines.append("\n✉️  Recipients:")
            for idx, email in enumerate(self.all_recipients):
                checkbox = "☑" if email in selected_set else "☐"
                prefix = "▶" if idx == current_selection else " "
                
                # Show edit buffer if this item is being edited
                if self.editing_index == idx:
                    display_email = self.edit_buffer + "█"  # Show cursor
                elif email.startswith("__editing_"):
                    # Placeholder shouldn't be visible (shouldn't happen)
                    display_email = ""
                else:
                    display_email = email
                
                lines.append(f"{prefix}     {checkbox}       {display_email}")
        
        return lines
    
    async def run(self) -> Tuple[bool, List[str]]:
        """
        Run the interactive recipient selector.
        
        Returns:
            (confirmed, selected_recipients) tuple
            - confirmed: True if user confirmed, False if cancelled
            - selected_recipients: List of selected email addresses
        """
        # State
        modes = [RecipientMode.REPLY_SENDER, RecipientMode.REPLY_ALL, RecipientMode.CUSTOM]
        mode_index = modes.index(self.mode)  # Start at current mode (Custom if pre-populated)
        confirmed = False
        
        while True:
            # Render display
            self.mode = modes[mode_index]
            
            print("\033[H\033[J", end='', flush=True)  # Clear screen
            print("\n📧 Select Recipients\n")
            print(self._render_mode_selector(self.mode))
            if self.mode == RecipientMode.CUSTOM:
                print("\n⬅️ ➡️  Switch Mode  •  ↑↓ Navigate  •  SPACE Toggle  •  A Add  •  ENTER Confirm  •  ESC Cancel")
            else:
                print("\n⬅️ ➡️  Switch Mode  •  ↑↓ Navigate (Custom)  •  SPACE Toggle (Custom)  •  ENTER Confirm  •  ESC Cancel")
            print()
            
            recipient_lines = self._render_recipient_list(
                self.mode,
                self.current_selection,
                self.selected_recipients
            )
            for line in recipient_lines:
                print(line)
            
            # Get keystroke
            from prompt_toolkit.application import Application
            from prompt_toolkit.layout import Layout, Window
            from prompt_toolkit.layout.controls import FormattedTextControl
            from prompt_toolkit.key_binding import KeyBindings
            
            kb = KeyBindings()
            result = {'action': None}
            
            @kb.add(Keys.Left)
            def _(event):
                result['action'] = 'left'
                event.app.exit()
            
            @kb.add(Keys.Right)
            def _(event):
                result['action'] = 'right'
                event.app.exit()
            
            @kb.add(Keys.Up)
            def _(event):
                result['action'] = 'up'
                event.app.exit()
            
            @kb.add(Keys.Down)
            def _(event):
                result['action'] = 'down'
                event.app.exit()
            
            @kb.add(' ')
            def _(event):
                result['action'] = 'space'
                event.app.exit()
            
            @kb.add('a')
            def _(event):
                # 'a' is add when NOT editing, typing when editing
                if self.editing_index is None:
                    result['action'] = 'add'
                else:
                    result['action'] = ('type', 'a')
                event.app.exit()
            
            @kb.add(Keys.Backspace)
            def _(event):
                result['action'] = 'backspace'
                event.app.exit()
            
            # Allow typing characters when editing (excluding 'a' since it's handled above)
            for char in 'bcdefghijklmnopqrstuvwxyz0123456789@.-_':
                if char == 'a':
                    continue  # Skip 'a' since it's handled separately
                @kb.add(char)
                def _(event, c=char):
                    result['action'] = ('type', c)
                    event.app.exit()
            
            @kb.add(Keys.Enter)
            def _(event):
                result['action'] = 'enter'
                event.app.exit()
            
            @kb.add(Keys.Escape)
            def _(event):
                result['action'] = 'escape'
                event.app.exit()
            
            @kb.add(Keys.ControlC)
            def _(event):
                result['action'] = 'escape'
                event.app.exit()
            
            app = Application(
                layout=Layout(Window(FormattedTextControl(text=''))),
                key_bindings=kb,
                full_screen=False,
                mouse_support=False
            )
            
            try:
                await app.run_async()
                action = result['action']
            except KeyboardInterrupt:
                action = 'escape'
            
            # Handle action
            if action == 'left':
                # Exit editing mode when switching modes
                self.editing_index = None
                self.edit_buffer = ""
                mode_index = max(0, mode_index - 1)
                self.mode = modes[mode_index]
            elif action == 'right':
                # Exit editing mode when switching modes
                self.editing_index = None
                self.edit_buffer = ""
                mode_index = min(len(modes) - 1, mode_index + 1)
                self.mode = modes[mode_index]
            elif action == 'up' and self.mode == RecipientMode.CUSTOM:
                if self.editing_index is None:
                    self.current_selection = max(0, self.current_selection - 1)
            elif action == 'down' and self.mode == RecipientMode.CUSTOM:
                if self.editing_index is None:
                    self.current_selection = min(len(self.all_recipients) - 1, self.current_selection + 1)
            elif action == 'space' and self.mode == RecipientMode.CUSTOM:
                if self.editing_index is None:
                    email = self.all_recipients[self.current_selection]
                    if email in self.selected_recipients:
                        self.selected_recipients.remove(email)
                    else:
                        self.selected_recipients.add(email)
            elif action == 'add' and self.mode == RecipientMode.CUSTOM:
                # Only add if not already editing
                if self.editing_index is None:
                    # Add a new blank recipient entry
                    placeholder = f"__editing_{len(self.all_recipients)}__"
                    self.all_recipients.append(placeholder)
                    self.selected_recipients.add(placeholder)  # Pre-check it
                    self.current_selection = len(self.all_recipients) - 1
                    self.editing_index = self.current_selection
                    self.edit_buffer = ""
            elif action == 'backspace' and self.editing_index is not None:
                # Delete character from edit buffer
                if self.edit_buffer:
                    self.edit_buffer = self.edit_buffer[:-1]
            elif isinstance(action, tuple) and action[0] == 'type' and self.editing_index is not None:
                # Add character to edit buffer
                self.edit_buffer += action[1]
            elif action == 'enter':
                if self.editing_index is not None:
                    # Finish editing - save the email
                    if self.edit_buffer.strip() and '@' in self.edit_buffer:
                        # Valid email, update it
                        old_placeholder = self.all_recipients[self.editing_index]
                        new_email = self.edit_buffer.strip().lower()
                        self.all_recipients[self.editing_index] = new_email
                        # Update selected set
                        if old_placeholder in self.selected_recipients:
                            self.selected_recipients.discard(old_placeholder)
                            self.selected_recipients.add(new_email)
                        self.editing_index = None
                        self.edit_buffer = ""
                    else:
                        # Invalid email, remove it
                        old_placeholder = self.all_recipients[self.editing_index]
                        self.all_recipients.pop(self.editing_index)
                        self.selected_recipients.discard(old_placeholder)
                        self.editing_index = None
                        self.edit_buffer = ""
                        self.current_selection = max(0, min(self.current_selection, len(self.all_recipients) - 1))
                else:
                    # Not editing, confirm and exit
                    confirmed = True
                    break
            elif action == 'escape':
                if self.editing_index is not None:
                    # Cancel editing - remove the placeholder entry
                    old_placeholder = self.all_recipients[self.editing_index]
                    self.all_recipients.pop(self.editing_index)
                    self.selected_recipients.discard(old_placeholder)
                    self.editing_index = None
                    self.edit_buffer = ""
                    self.current_selection = max(0, min(self.current_selection, len(self.all_recipients) - 1))
                else:
                    # Not editing, cancel the whole thing
                    confirmed = False
                    break
        
        # Clear screen
        print("\033[H\033[J", end='', flush=True)
        
        return confirmed, self.get_selected_recipients()

