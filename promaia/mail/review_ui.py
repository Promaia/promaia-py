"""
Email Draft Review UI - Interactive review interface for email drafts.
"""
import json
import logging
import os
import subprocess
import sys
from typing import List, Dict, Any, Optional
from datetime import datetime

from prompt_toolkit import prompt
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from promaia.mail.draft_manager import DraftManager
from promaia.mail.gmail_sender import GmailSender
from promaia.mail.thread_formatter import format_thread_for_display
from promaia.utils.display import print_text, print_separator
from promaia.utils.timezone_utils import to_local, get_local_timezone_name, now_utc

logger = logging.getLogger(__name__)


class EmailReviewUI:
    """Interactive review interface for email drafts."""
    
    def __init__(self, default_days: int = 7, show_all: bool = False, auto_archive_threshold: int = 30):
        """
        Initialize Email Review UI.

        Args:
            default_days: Default number of days to show in queue (default: 7)
            show_all: If True, show all drafts regardless of age (default: False)
            auto_archive_threshold: Days after which to auto-archive skipped drafts (default: 30)
        """
        self.draft_manager = DraftManager()
        self.default_days = None if show_all else default_days
        self.show_all = show_all
        self.auto_archive_threshold = auto_archive_threshold
        self.session_start_count = 0  # Track how many items at session start
        self.session_sent = 0  # Track items sent this session
        self.session_archived = 0  # Track items archived this session
        self.history_mode = False  # Toggle between queue and history view
    
    def _clear_screen_and_home(self):
        """Clear terminal screen and position cursor at top."""
        # Use ANSI escape sequences for better control
        # ESC[r resets scroll region
        # ESC[H moves cursor to home (1,1) 
        # ESC[2J clears entire screen
        # ESC[3J clears scrollback buffer
        print('\033[r\033[H\033[2J\033[3J', end='', flush=True)
    
    def _enter_alternate_screen(self):
        """Enter alternate screen buffer (like vim/less)."""
        # ESC[?1049h switches to alternate screen
        # ESC[?7l disables auto-wrap
        # ESC[H moves cursor to top immediately
        # ESC[2J clears the alternate screen
        print('\033[?1049h\033[?7l\033[H\033[2J', end='', flush=True)
    
    def _exit_alternate_screen(self):
        """Exit alternate screen buffer."""
        # ESC[?1049l switches back to main screen
        print('\033[?1049l', end='', flush=True)
    
    def _jump_to_top(self):
        """Force terminal viewport to top using tput."""
        try:
            # Use tput home command - more reliable than cup
            os.system('tput home >/dev/null 2>&1')
        except Exception:
            pass
        # Also use ANSI home sequence
        print('\033[H', end='', flush=True)
    
    def _clean_email_body(self, body: str) -> str:
        """
        Remove redundant email headers from body content.
        
        Email bodies often start with headers like:
        From: ...
        Sent: ...
        To: ...
        Subject: ...
        
        We strip these out since we display them separately.
        """
        if not body:
            return body
        
        lines = body.split('\n')
        cleaned_lines = []
        skip_headers = True
        
        for line in lines:
            # Check if line looks like an email header
            if skip_headers:
                # Common email header patterns
                if line.strip().startswith(('From:', 'Sent:', 'To:', 'Subject:', 'Date:', 'Cc:', 'Bcc:')):
                    continue
                # Empty line often follows headers
                elif not line.strip():
                    continue
                else:
                    # Found actual content, stop skipping
                    skip_headers = False
                    cleaned_lines.append(line)
            else:
                cleaned_lines.append(line)
        
        return '\n'.join(cleaned_lines).strip()
    
    async def _get_keystroke(self) -> str:
        """Capture a single keystroke without requiring Enter."""
        from prompt_toolkit.application import Application
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        
        kb = KeyBindings()
        result = {'value': None}
        
        # Navigation keys
        @kb.add(Keys.Up)
        def _(event):
            result['value'] = 'up'
            event.app.exit()
        
        @kb.add(Keys.Down)
        def _(event):
            result['value'] = 'down'
            event.app.exit()
        
        @kb.add(Keys.Escape)
        def _(event):
            result['value'] = 'escape'
            event.app.exit()
        
        @kb.add(Keys.ControlC)
        def _(event):
            result['value'] = 'q'
            event.app.exit()
        
        @kb.add('q')
        def _(event):
            result['value'] = 'q'
            event.app.exit()
        
        # Enter to open chat
        @kb.add(Keys.Enter)
        def _(event):
            result['value'] = 'enter'
            event.app.exit()
            
        # Archive key
        @kb.add('a')
        def _(event):
            result['value'] = 'a'
            event.app.exit()
        
        # History key
        @kb.add('h')
        def _(event):
            result['value'] = 'h'
            event.app.exit()
        
        # Create minimal application to capture keystroke
        app = Application(
            layout=Layout(Window(FormattedTextControl(text=''))),
            key_bindings=kb,
            full_screen=False,
            mouse_support=False
        )
        
        try:
            await app.run_async()
            return result['value'] or ''
        except KeyboardInterrupt:
            return 'q'
    
    def _load_drafts(self, workspaces: List[str], include_resolved: bool = False) -> List[Dict[str, Any]]:
        """Load drafts based on current mode (queue or history).

        Args:
            workspaces: List of workspace names
            include_resolved: If True, include sent/archived drafts (for stats calculation)
        """
        all_drafts = []
        for workspace in workspaces:
            # Auto-archive old skipped drafts before loading queue (only in queue mode)
            if not self.history_mode and not include_resolved:
                archived_count = self.draft_manager.auto_archive_old_skipped_drafts(
                    workspace=workspace,
                    days_threshold=self.auto_archive_threshold
                )
                if archived_count > 0:
                    logger.debug(f"Auto-archived {archived_count} old skipped drafts for {workspace}")

            if self.history_mode:
                drafts = self.draft_manager.get_history_for_workspace(workspace)
            else:
                # Pass days filter to DraftManager (pending/unsure always shown regardless of date)
                drafts = self.draft_manager.get_drafts_for_workspace(
                    workspace,
                    include_resolved=include_resolved,
                    days=self.default_days
                )
            all_drafts.extend(drafts)
        return all_drafts
    
    def _filter_queue_drafts(self, drafts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter drafts to only show pending/skipped/unsure in queue view."""
        if self.history_mode:
            return drafts  # History shows all completed
        else:
            # Queue only shows pending, unsure, and skipped (not sent/archived)
            return [d for d in drafts if d.get('status') in ('pending', 'unsure', 'skipped')]
    
    def _calculate_session_stats(self, current_queue_size: int) -> Dict[str, int]:
        """Calculate session-based stats for progress tracking.
        
        Args:
            current_queue_size: Current number of items in queue (pending + skipped)
        
        Returns:
            Dict with session stats including progress percentage
        """
        stats = {
            'session_start': self.session_start_count,
            'current_queue': current_queue_size,
            'sent': self.session_sent,
            'archived': self.session_archived,
        }
        stats['resolved'] = self.session_sent + self.session_archived
        stats['remaining'] = current_queue_size
        
        # Progress based on session: how many of the original items have been dealt with
        if self.session_start_count > 0:
            stats['percent'] = int((stats['resolved'] / self.session_start_count) * 100)
        else:
            stats['percent'] = 0
        
        return stats
    
    def _calculate_queue_counts(self, drafts: List[Dict[str, Any]]) -> Dict[str, int]:
        """Calculate current queue composition (pending/skipped/unsure counts)."""
        return {
            'pending': sum(1 for d in drafts if d.get('status') == 'pending'),
            'skipped': sum(1 for d in drafts if d.get('status') == 'skipped'),
            'unsure': sum(1 for d in drafts if d.get('status') == 'unsure'),
            'total': len(drafts)
        }
    
    def _render_progress_bar(self, stats: Dict[str, int]) -> str:
        """Render just the progress bar."""
        total_width = 30
        filled = int((stats['percent'] / 100) * total_width)
        bar = '█' * filled + '░' * (total_width - filled)
        return bar
    
    def _render_status_bar(self, session_stats: Dict[str, int], queue_counts: Dict[str, int]) -> str:
        """Render progress and status bar.

        Args:
            session_stats: Session-level progress stats
            queue_counts: Current queue composition (pending/skipped)
        """
        if self.history_mode:
            # History view header - simple total count
            total_history = queue_counts['total']
            return (
                f"Maia Mail - History (Completed Messages)\n\n"
                f"Total: {total_history} completed messages\n\n"
            )
        else:
            # Time window indicator
            if self.default_days is not None:
                time_window = f"Showing last {self.default_days} days (pending/unsure shown regardless of age)  •  --all to see more\n"
            else:
                time_window = "Showing all drafts\n"

            # Queue view header with session progress
            if session_stats['session_start'] > 0:
                bar = self._render_progress_bar(session_stats)
                progress_line = f"Session Progress: [{bar}] {session_stats['resolved']}/{session_stats['session_start']} completed ({session_stats['percent']}%)\n"
            else:
                progress_line = ""

            return (
                f"Maia Mail - Draft Review Queue\n\n"
                f"{time_window}"
                f"{progress_line}"
                f"Queue: ⏳ {queue_counts['pending']} pending  •  🤷‍♀️ {queue_counts['unsure']} unsure  •  ⏭️ {queue_counts['skipped']} skipped  •  "
                f"Session: ✅ {session_stats['sent']} sent  •  🗄️ {session_stats['archived']} archived\n\n"
            )
    
    def _render_review_list(self, drafts: List[Dict[str, Any]], current_selection: int, start_offset: int = 0) -> str:
        """Render list of drafts for review.
        
        Args:
            drafts: List of drafts to render
            current_selection: Index of currently selected draft (relative to drafts list)
            start_offset: Offset to add to draft numbers for display (for pagination)
        """
        output = []
        
        for idx, draft in enumerate(drafts):
            # Status icon
            if draft.get('status') == 'sent':
                icon = '✅'
            elif draft.get('status') == 'archived':
                icon = '🗄️'
            elif draft.get('status') == 'skipped':
                icon = '⏭️'
            elif draft.get('status') == 'unsure':
                icon = '🤷‍♀️'
            else:
                icon = '⏳'
            
            # Selection indicator
            selector = '▶' if idx == current_selection else ' '
            
            # Format date (convert to local timezone)
            try:
                date_obj = datetime.fromisoformat(draft.get('inbound_date', '').replace('Z', '+00:00'))
                local_date = to_local(date_obj)
                tz_name = get_local_timezone_name()
                date_str = local_date.strftime(f'%b %d, %I:%M %p {tz_name}')
            except:
                date_str = 'Unknown'
            
            # Preview
            from_addr = draft.get('inbound_from', 'Unknown')
            if '<' in from_addr and '>' in from_addr:
                from_name = from_addr.split('<')[0].strip()
            else:
                from_name = from_addr
            
            subject = draft.get('inbound_subject', 'No Subject')
            snippet = draft.get('inbound_snippet', '')[:80]
            
            # Actual draft number in full list (1-based)
            draft_number = start_offset + idx + 1
            
            # Handle skipped drafts differently
            if draft.get('status') == 'skipped':
                reasoning = draft.get('classification_reasoning', 'No response needed')
                output.append(f"{selector} [{draft_number}] {icon} {subject}")
                output.append(f"       From: {from_name} | {date_str}")
                output.append(f"       Preview: {snippet}...")
                output.append(f"       Draft: n/a  •  {reasoning}")
                output.append("")
            else:
                # Context count
                try:
                    context_data = json.loads(draft.get('response_context', '{}'))
                    context_count = len(context_data.get('documents', []))
                except:
                    context_count = 0
                
                # Word count
                word_count = len(draft.get('draft_body', '').split())
                
                output.append(f"{selector} [{draft_number}] {icon} {subject}")
                output.append(f"       From: {from_name} | {date_str}")
                output.append(f"       Preview: {snippet}...")
                output.append(f"       Draft: {word_count} words | Context: {context_count} sources")
                output.append("")
        
        return '\n'.join(output)
    
    def _render_draft_detail(self, draft: Dict[str, Any]) -> str:
        """Render full draft details."""
        # Get context
        try:
            context_data = json.loads(draft.get('response_context', '{}'))
            context_sources = context_data.get('documents', [])
            context_count = len(context_sources)
        except:
            context_count = 0
            context_sources = []
        
        # Format date (convert to local timezone)
        try:
            received_dt = datetime.fromisoformat(draft.get('inbound_date', '').replace('Z', '+00:00'))
            local_received = to_local(received_dt)
            tz_name = get_local_timezone_name()
            received_str = local_received.strftime(f'%A, %B %d, %Y at %I:%M %p {tz_name}')
        except:
            received_str = draft.get('inbound_date', 'Unknown')
        
        # Word count
        draft_words = len(draft.get('draft_body', '').split())
        
        # Context summary
        if context_sources:
            context_summary = ', '.join([s.get('database', 'unknown') for s in context_sources[:3]])
            if len(context_sources) > 3:
                context_summary += f" + {len(context_sources) - 3} more"
        else:
            context_summary = "None"
        
        # Clean email body to remove redundant headers
        cleaned_body = self._clean_email_body(draft.get('inbound_body', 'No body available'))
        
        # Format thread with copy-friendly styling and position indicators
        message_count = draft.get('message_count', 1)
        thread_display = format_thread_for_display(
            conversation_body=cleaned_body,
            message_count=message_count,
            from_addr=draft.get('inbound_from', 'Unknown'),
            subject=draft.get('inbound_subject', 'No Subject'),
            received_str=received_str,
            use_colors=True
        )
        
        return f"""
Draft Review - Full View

{thread_display}

─────────────────────────────────────────────────────────────────

YOUR DRAFT RESPONSE ({draft_words} words)

{draft.get('draft_body', 'No draft available')}

─────────────────────────────────────────────────────────────────

CONTEXT USED

Sources:     {context_count} documents from knowledge base
Databases:   {context_summary}
AI Model:    {draft.get('ai_model', 'unknown')}
Generated:   {draft.get('created_time', 'unknown')}

─────────────────────────────────────────────────────────────────

ACTIONS

[Enter] Chat  [a] Archive  [v] Context  [b] Back  [q] Quit

"""
    
    def _render_context_view(self, draft: Dict[str, Any]) -> str:
        """Show full context sources used for generation."""
        try:
            context_data = json.loads(draft.get('response_context', '{}'))
            context_sources = context_data.get('documents', [])
        except:
            return "\n❌ No context sources available\n"

        if not context_sources:
            return "\n📭 No context sources were used for this draft\n"

        output = [
            "\nContext Sources Used for Draft Generation\n"
        ]

        for idx, source in enumerate(context_sources, 1):
            output.append(f"[{idx}] {source.get('title', 'Untitled')}")
            output.append(f"    Database: {source.get('database', 'unknown')} | Relevance: {source.get('similarity', 0):.0%}")
            snippet = source.get('snippet', '')[:100]
            output.append(f"    Preview: {snippet}...")
            output.append("")

        output.append("\n[b] Back  [q] Quit")
        return '\n'.join(output)

    def _show_queue_cleared_celebration(self):
        """Show celebration message when queue is completely cleared."""
        import random

        # Clear screen for celebration
        self._clear_screen_and_home()

        # Collection of fun celebration messages
        celebrations = [
            {
                "emoji": "🎉🎊✨",
                "title": "INBOX ZERO ACHIEVED!",
                "message": "You absolute legend! Every draft reviewed, every email handled.",
                "tagline": "Time to celebrate with a well-deserved break! ☕️"
            },
            {
                "emoji": "🏆🌟💫",
                "title": "QUEUE CONQUERED!",
                "message": "Not a single draft left standing. You're on fire!",
                "tagline": "Your inbox management skills are unmatched! 🚀"
            },
            {
                "emoji": "✅🎯🔥",
                "title": "ALL CLEAR!",
                "message": "Zero drafts pending. Zero stress. One hundred percent awesome.",
                "tagline": "Enjoy the zen of an empty queue! 🧘"
            },
            {
                "emoji": "🎪🎨🌈",
                "title": "DRAFT-FREE ZONE!",
                "message": "Every email responded to, every thread handled. Perfection!",
                "tagline": "You've earned this moment of peace! ✨"
            }
        ]

        celebration = random.choice(celebrations)

        # Stats summary
        total_resolved = self.session_sent + self.session_archived

        print()
        print()
        print_text("═" * 70, style="bold cyan")
        print()
        print_text(f"  {celebration['emoji']}", style="bold yellow")
        print_text(f"  {celebration['title']}", style="bold green")
        print()
        print_text(f"  {celebration['message']}", style="cyan")
        print()
        print_text("  " + "─" * 66, style="dim")
        print()
        print_text(f"  📊 Session Stats:", style="bold")
        print_text(f"     • Started with: {self.session_start_count} drafts", style="dim")
        print_text(f"     • Sent: ✅ {self.session_sent}", style="green")
        print_text(f"     • Archived: 🗄️ {self.session_archived}", style="blue")
        print_text(f"     • Total cleared: {total_resolved} 🎯", style="bold green")
        print()
        print_text(f"  {celebration['tagline']}", style="yellow")
        print()
        print_text("═" * 70, style="bold cyan")
        print()
        print()
    
    async def launch_review(self, workspaces: List[str], start_in_history: bool = False):
        """
        Main review flow with queue and history views.
        
        Args:
            workspaces: List of workspace names
            start_in_history: If True, start in history view instead of queue
        """
        self.history_mode = start_in_history
        
        # Load drafts for current mode
        if self.history_mode:
            display_drafts = self._load_drafts(workspaces, include_resolved=False)
            # History mode shows completed items
            display_drafts = [d for d in self._load_drafts(workspaces, include_resolved=True) 
                            if d.get('status') in ('sent', 'archived')]
        else:
            # Queue mode shows only pending/skipped
            all_drafts = self._load_drafts(workspaces, include_resolved=True)
            display_drafts = self._filter_queue_drafts(all_drafts)
        
        if not display_drafts:
            if self.history_mode:
                print_text("\n📭 No history yet. Complete some emails to see them here!\n", style="green")
            else:
                print_text("\n📭 No email drafts to review. All caught up!\n", style="green")
            return
        
        # Initialize session tracking - remember starting queue size
        self.session_start_count = len(display_drafts)
        self.session_sent = 0
        self.session_archived = 0
        
        # State
        current_selection = 0
        page_start = 0  # Start of current page window
        
        # Get terminal height for pagination
        try:
            rows, _ = os.popen('stty size', 'r').read().split()
            terminal_height = int(rows)
        except:
            terminal_height = 24  # Default fallback
        
        # Reserve lines for:
        # - Top margin (3)
        # - Header/progress (5)
        # - Controls (2)
        # - Pagination (2)
        # - Prompt (1)
        # - Bottom margin (2)
        # = 15 lines total overhead
        # Each draft takes ~5 lines (4 lines + blank line)
        max_visible_drafts = max(1, min(5, (terminal_height - 15) // 5))
        
        # Main loop - just show list and open chat
        while True:
            try:
                # --- 1. Calculate Pagination ---
                # Fixed window pagination - window only moves when selection reaches edges
                page_start = max(0, min(page_start, len(display_drafts) - max_visible_drafts))
                if len(display_drafts) <= max_visible_drafts:
                    page_start = 0
                
                start_idx = page_start
                end_idx = min(len(display_drafts), start_idx + max_visible_drafts)

                # --- 2. Build Display String ---
                # Build the entire display as a single string for atomic rendering
                display_parts = []

                # Top margin (for terminals that have UI elements at top)
                display_parts.append("\n\n\n")

                # Calculate current stats
                session_stats = self._calculate_session_stats(len(display_drafts))
                queue_counts = self._calculate_queue_counts(display_drafts)

                # Header
                display_parts.append(self._render_status_bar(session_stats, queue_counts))
                
                # Controls
                if self.history_mode:
                    controls_line = "Navigation: ↑/↓ | Enter to view | h back to queue | q quit"
                else:
                    controls_line = "Navigation: ↑/↓ | Enter to open chat | a archive | h history | q quit"
                display_parts.append(controls_line + "\n")
                
                # Pagination info
                if len(display_drafts) > max_visible_drafts:
                    display_parts.append(f"Showing {start_idx + 1}-{end_idx} of {len(display_drafts)} drafts\n")
                
                # Queue list
                visible_drafts = display_drafts[start_idx:end_idx]
                visible_selection = current_selection - start_idx
                queue_list = self._render_review_list(visible_drafts, visible_selection, start_offset=start_idx)
                display_parts.append(queue_list)
                
                final_output = "".join(display_parts)
                
                # --- 3. Atomic Render ---
                # Use a single write call with ANSI codes for a flicker-free update
                # \033[?25l = hide cursor
                # \033[H = move to home (top-left)
                # \033[J = clear screen from cursor down
                # \033[?25h = show cursor
                atomic_render_sequence = f"\033[?25l\033[H\033[J{final_output}\033[?25h"
                sys.stdout.write(atomic_render_sequence)
                sys.stdout.flush()
                
                # --- 4. Get Input ---
                action = await self._get_keystroke()
                
                # --- 5. Handle Input ---
                if action == 'q' or action == 'escape':
                    break
                elif action == 'h':
                    # Toggle between queue and history
                    self.history_mode = not self.history_mode
                    # Reload drafts for new mode
                    if self.history_mode:
                        # History mode shows completed items
                        all_drafts = self._load_drafts(workspaces, include_resolved=True)
                        display_drafts = [d for d in all_drafts if d.get('status') in ('sent', 'archived')]
                    else:
                        # Queue mode shows pending/skipped
                        all_drafts = self._load_drafts(workspaces, include_resolved=True)
                        display_drafts = self._filter_queue_drafts(all_drafts)
                    
                    if not display_drafts:
                        # No drafts in this mode, switch back
                        self.history_mode = not self.history_mode
                        if self.history_mode:
                            all_drafts = self._load_drafts(workspaces, include_resolved=True)
                            display_drafts = [d for d in all_drafts if d.get('status') in ('sent', 'archived')]
                        else:
                            all_drafts = self._load_drafts(workspaces, include_resolved=True)
                            display_drafts = self._filter_queue_drafts(all_drafts)
                        print_text("\n📭 No items in that view!\n", style="yellow")
                    
                    # Reset selection
                    current_selection = 0
                    page_start = 0
                elif action == 'enter':
                    # Open draft chat for selected draft
                    old_status = display_drafts[current_selection].get('status')
                    await self._handle_chat(display_drafts[current_selection])
                    
                    # Check if status changed and update session counters
                    updated_draft = self.draft_manager.get_draft(display_drafts[current_selection]['draft_id'])
                    if updated_draft:
                        new_status = updated_draft.get('status')
                        if old_status != new_status:
                            if new_status == 'sent':
                                self.session_sent += 1
                            elif new_status == 'archived':
                                self.session_archived += 1
                    
                    # Reload drafts after chat (status may have changed)
                    all_drafts = self._load_drafts(workspaces, include_resolved=True)
                    display_drafts = self._filter_queue_drafts(all_drafts)

                    # Check if queue is now empty (all cleared!)
                    if len(display_drafts) == 0 and self.session_start_count > 0:
                        self._show_queue_cleared_celebration()
                        return

                    # Adjust selection if draft was removed from queue
                    if current_selection >= len(display_drafts):
                        current_selection = max(0, len(display_drafts) - 1)
                elif action == 'a':
                    # Archive only available in queue mode (not in history)
                    if not self.history_mode:
                        # Archive: mark as archived to clear from queue
                        await self._handle_archive(display_drafts[current_selection])
                        self.session_archived += 1
                        
                        # Reload drafts after archiving (will remove from queue)
                        all_drafts = self._load_drafts(workspaces, include_resolved=True)
                        display_drafts = self._filter_queue_drafts(all_drafts)

                        # Check if queue is now empty (all cleared!)
                        if len(display_drafts) == 0 and self.session_start_count > 0:
                            self._show_queue_cleared_celebration()
                            return

                        # Adjust selection if needed
                        if current_selection >= len(display_drafts):
                            current_selection = max(0, len(display_drafts) - 1)
                elif action == 'up':
                    if current_selection > 0:
                        current_selection -= 1
                        # Scroll page up if selection goes above visible window
                        if current_selection < page_start:
                            page_start = current_selection
                elif action == 'down':
                    if current_selection < len(display_drafts) - 1:
                        current_selection += 1
                        # Scroll page down if selection goes below visible window
                        if current_selection >= page_start + max_visible_drafts:
                            page_start = current_selection - max_visible_drafts + 1
                        
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Error in review loop: {e}")
                print_text(f"\n❌ Error: {e}\n", style="red")
                input("Press Enter to continue...")
        
        # Final summary (back on main screen now)
        print()
        if self.session_sent > 0 or self.session_archived > 0:
            print_text(
                f"✅ Session complete: {self.session_sent} sent, {self.session_archived} archived",
                style="green"
            )
        else:
            print_text("✅ Session complete", style="green")
    
    async def _handle_archive(self, draft: Dict[str, Any]):
        """
        Handle archiving a draft.
        
        Archive clears the item from your queue - gives the satisfying feeling
        of clearing papers off your desk.
        """
        self.draft_manager.update_draft_status(draft['draft_id'], 'archived')
        logger.info(f"Draft {draft['draft_id']} archived")
    
    async def _handle_chat(self, draft: Dict[str, Any]):
        """Handle opening chat for a draft."""
        from promaia.mail.draft_chat import DraftChatInterface
        
        chat = DraftChatInterface(draft['draft_id'], draft['workspace'])
        await chat.run_chat_loop()
    
    async def _save_to_learning(self, draft: Dict[str, Any]):
        """Save successful send to learning system."""
        try:
            from promaia.mail.learning_system import EmailResponseLearningSystem
            workspace = draft.get('workspace', 'default')
            learning = EmailResponseLearningSystem(workspace=workspace)
            
            pattern = {
                "inbound": {
                    "from": draft['inbound_from'],
                    "subject": draft['inbound_subject'],
                    "body_snippet": draft['inbound_snippet'],
                },
                "response": {
                    "subject": draft['draft_subject'],
                    "body": draft['draft_body'],
                    "tone": "professional",
                    "length": len(draft['draft_body'].split())
                },
                "metadata": {
                    "workspace": workspace,
                    "ai_model": draft.get('ai_model', 'unknown'),
                    "context_sources": draft.get('response_context', '{}'),
                    "timestamp": now_utc().isoformat()
                }
            }
            
            learning.save_successful_response(pattern)
            logger.info(f"✅ Saved response pattern to learning system (workspace: {workspace})")
            
        except Exception as e:
            logger.warning(f"⚠️  Could not save to learning system: {e}")
