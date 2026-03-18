"""
Maia Mail Setup UI - Interactive setup and configuration for maia mail.

Provides:
- Overview of Gmail accounts, workspaces, and prompt configuration
- Sub-flows for adding Gmail databases and configuring per-workspace prompts
- Editor integration for prompt editing
"""
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from promaia.utils.display import print_text
from promaia.utils.env_writer import get_prompts_dir

logger = logging.getLogger(__name__)


async def _inline_select(rows: List) -> Optional[Dict]:
    """Interactive arrow-key selector with inline selectable items.

    Renders a mixed list of static text lines and selectable items.
    Arrow keys move between selectable items only; static lines are
    displayed but not focusable.

    Args:
        rows: List where each element is either:
            - str: static display line (not selectable)
            - dict: selectable item with 'label' (str) and action fields.
                    Optional 'indent' (int, default 0) for leading spaces.

    Returns:
        The selected dict, or None if cancelled (Escape/b).
        A dict with action='quit' for q/Ctrl-C.
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    # Build index of selectable row positions
    selectable_indices = [i for i, r in enumerate(rows) if isinstance(r, dict)]
    if not selectable_indices:
        return None

    cursor = [0]  # index into selectable_indices
    result = {'value': None}

    def _render():
        active_row = selectable_indices[cursor[0]]
        out = []
        for i, row in enumerate(rows):
            if isinstance(row, str):
                out.append(row)
            else:
                indent = " " * row.get("indent", 0)
                label = row["label"]
                if i == active_row:
                    out.append(f"{indent}\033[96m❯ \033[1m{label}\033[0m")
                else:
                    out.append(f"{indent}  {label}")
        out.append("")
        out.append("  \033[2m↑/↓ navigate  •  Enter select  •  q quit\033[0m")
        return "\n".join(out)

    def _redraw():
        sys.stdout.write("\033[H\033[2J")
        sys.stdout.write(_render())
        sys.stdout.write("\n")
        sys.stdout.flush()

    kb = KeyBindings()

    @kb.add(Keys.Up)
    def _up(event):
        cursor[0] = (cursor[0] - 1) % len(selectable_indices)
        _redraw()

    @kb.add(Keys.Down)
    def _down(event):
        cursor[0] = (cursor[0] + 1) % len(selectable_indices)
        _redraw()

    @kb.add(Keys.Enter)
    def _enter(event):
        result['value'] = rows[selectable_indices[cursor[0]]]
        event.app.exit()

    @kb.add('q')
    def _quit(event):
        result['value'] = {'action': 'quit'}
        event.app.exit()

    @kb.add(Keys.Escape)
    def _esc(event):
        result['value'] = None
        event.app.exit()

    @kb.add(Keys.ControlC)
    def _ctrl_c(event):
        result['value'] = {'action': 'quit'}
        event.app.exit()

    _redraw()

    app = Application(
        layout=Layout(Window(FormattedTextControl(text=''))),
        key_bindings=kb,
        full_screen=False,
        mouse_support=False,
    )

    try:
        await app.run_async()
    except KeyboardInterrupt:
        return {'action': 'quit'}

    return result['value']


def _get_google_auth_status(email: str) -> Tuple[bool, str]:
    """Check if a Google account has valid credentials (local check only).

    Returns:
        (has_token, status_label) - status_label is a short description
    """
    from promaia.auth.integrations.google import GoogleIntegration
    from promaia.auth.token_refresh import is_token_expired

    path = GoogleIntegration._token_path(email)
    if not path.exists():
        return False, "no credentials"

    try:
        token_data = json.loads(path.read_text())
    except Exception:
        return False, "corrupt token file"

    if not token_data.get("refresh_token"):
        return False, "no refresh token"

    # Access token expired is normal (refresh happens on use), but
    # we can flag if there's no way to refresh (missing refresh_token)
    # For user_oauth, check if client creds are present
    if token_data.get("mode") == "user_oauth":
        if not token_data.get("client_id") or not token_data.get("client_secret"):
            return False, "missing client credentials"
        return True, "authenticated (own project)"
    else:
        return True, "authenticated (proxy)"


def _get_prompt_status(filename: str) -> Tuple[bool, Optional[int]]:
    """Check if a prompt file exists and get its word count.

    Returns:
        (exists, word_count) - word_count is None if file doesn't exist
    """
    prompts_dir = get_prompts_dir()
    path = prompts_dir / filename
    if not path.exists():
        return False, None

    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return False, 0
        word_count = len(content.split())
        return True, word_count
    except Exception:
        return False, None


def _build_overview_rows() -> List:
    """Build the main overview as a flat list of rows for _inline_select.

    Returns:
        List of str (static lines) and dict (selectable items).
    """
    from promaia.auth.registry import get_integration
    from promaia.config.databases import get_database_manager
    from promaia.config.workspaces import get_workspace_manager

    ws_manager = get_workspace_manager()
    db_manager = get_database_manager()
    google_int = get_integration("google")

    rows: List = []

    rows.append("")
    rows.append("  \033[1m\033[96mMaia Mail Setup\033[0m")
    rows.append("")

    # ── Gmail Accounts ──────────────────────────────────────────────
    rows.append("  \033[1mGmail Accounts\033[0m")

    authed_accounts = google_int.list_authenticated_accounts()
    if authed_accounts:
        for email in authed_accounts:
            has_token, status = _get_google_auth_status(email)
            icon = "\033[32m✓\033[0m" if has_token else "\033[31m✗\033[0m"
            if has_token:
                rows.append(f"    {icon} {email} \033[2m({status})\033[0m")
            else:
                rows.append({
                    "label": f"✗ {email} \033[2m({status})\033[0m",
                    "indent": 4,
                    "action": "reauth",
                    "email": email,
                })
    else:
        rows.append("    \033[2mNo Gmail accounts configured\033[0m")

    rows.append({
        "label": "Add Gmail account",
        "indent": 2,
        "action": "add_gmail_account",
    })
    rows.append("")

    # ── Workspaces ──────────────────────────────────────────────────
    rows.append("  \033[1mWorkspaces\033[0m")

    workspaces = ws_manager.list_workspaces()
    if not workspaces:
        rows.append("    \033[2mNo workspaces configured\033[0m")
    else:
        for ws_name in workspaces:
            # Mail enabled toggle
            ws_config = ws_manager.get_workspace(ws_name)
            mail_on = ws_config.mail_enabled if ws_config else True
            if mail_on:
                mail_icon = "\033[32m●\033[0m"
                mail_label = "Mail: on"
            else:
                mail_icon = "\033[2m○\033[0m"
                mail_label = "Mail: off"

            rows.append(f"    \033[1m{ws_name}\033[0m  {mail_icon} \033[2m{mail_label}\033[0m")

            # Mail toggle — selectable
            toggle_text = "Disable mail processing" if mail_on else "Enable mail processing"
            rows.append({
                "label": f"⚙  {toggle_text}",
                "indent": 4,
                "action": "toggle_mail",
                "workspace": ws_name,
            })

            # Gmail databases section
            gmail_dbs = [
                db for db in db_manager.get_workspace_databases(ws_name)
                if db.source_type == "gmail"
            ]

            rows.append("      📧 Gmail databases")

            if gmail_dbs:
                for db in gmail_dbs:
                    email = db.database_id
                    has_token, status = _get_google_auth_status(email)
                    icon = "\033[32m✓\033[0m" if has_token else "\033[31m✗\033[0m"
                    rows.append({
                        "label": f"{email} {icon}",
                        "indent": 6,
                        "action": "manage_gmail",
                        "workspace": ws_name,
                        "email": email,
                        "db_name": db.name if hasattr(db, 'name') else "gmail",
                    })
            else:
                rows.append("        \033[2mNone configured\033[0m")

            rows.append({
                "label": "Add Gmail database",
                "indent": 6,
                "action": "add_gmail",
                "workspace": ws_name,
            })

            # Prompt status — selectable to edit directly
            cls_file = f"maia_mail_classification_prompt_{ws_name}.md"
            gen_file = f"maia_mail_prompt_{ws_name}.md"
            cls_exists, cls_words = _get_prompt_status(cls_file)
            gen_exists, gen_words = _get_prompt_status(gen_file)

            if cls_exists:
                cls_label = f"📝 Classification prompt: \033[32mconfigured\033[0m \033[2m({cls_words} words)\033[0m"
            else:
                cls_label = f"📝 Classification prompt: \033[33mnot configured\033[0m"
            rows.append({
                "label": cls_label,
                "indent": 4,
                "action": "edit_prompt",
                "filename": cls_file,
                "prompt_type": "classification",
            })

            if gen_exists:
                gen_label = f"📝 Generation prompt: \033[32mconfigured\033[0m \033[2m({gen_words} words)\033[0m"
            else:
                gen_label = f"📝 Generation prompt: \033[33mnot configured\033[0m"
            rows.append({
                "label": gen_label,
                "indent": 4,
                "action": "edit_prompt",
                "filename": gen_file,
                "prompt_type": "generation",
            })

            rows.append("")

    rows.append({
        "label": "Add workspace",
        "indent": 2,
        "action": "add_workspace",
    })
    rows.append("")

    # ── Fallback Prompts ────────────────────────────────────────────
    rows.append("  \033[1mFallback Prompts\033[0m")

    cls_file = "maia_mail_classification_prompt.md"
    gen_file = "maia_mail_prompt.md"
    cls_exists, cls_words = _get_prompt_status(cls_file)
    gen_exists, gen_words = _get_prompt_status(gen_file)

    if cls_exists:
        cls_fb_label = f"📝 Classification: \033[32mconfigured\033[0m \033[2m({cls_words} words)\033[0m"
    else:
        cls_fb_label = f"📝 Classification: \033[33mnot configured\033[0m"
    rows.append({
        "label": cls_fb_label,
        "indent": 2,
        "action": "edit_prompt",
        "filename": cls_file,
        "prompt_type": "classification",
    })

    if gen_exists:
        gen_fb_label = f"📝 Generation: \033[32mconfigured\033[0m \033[2m({gen_words} words)\033[0m"
    else:
        gen_fb_label = f"📝 Generation: \033[33mnot configured\033[0m"
    rows.append({
        "label": gen_fb_label,
        "indent": 2,
        "action": "edit_prompt",
        "filename": gen_file,
        "prompt_type": "generation",
    })

    rows.append("")

    rows.append({
        "label": "Cancel",
        "indent": 0,
        "action": "quit",
    })

    rows.append("")

    return rows


def _print_qr_code(url: str, quiet_zone: int = 2):
    """Print a QR code to the terminal using Unicode half-block characters.

    Uses the segno library if available, otherwise falls back to qrcode.
    If neither is installed, prints nothing (URL is always shown separately).
    """
    try:
        # Try segno first (lightweight, no deps)
        import segno
        qr = segno.make(url)
        matrix = qr.symbol_size()[0]
        # segno doesn't expose matrix directly, use qrcode instead
        raise ImportError("use qrcode path")
    except ImportError:
        pass

    try:
        import qrcode
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=quiet_zone,
        )
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
    except ImportError:
        # No QR library — generate minimal QR using built-in approach
        # Fall back to no QR display; URL is always printed
        return

    # Render using Unicode half-block characters
    # Each character row represents 2 matrix rows:
    #   ▀ (U+2580) = top black, bottom white
    #   ▄ (U+2584) = top white, bottom black
    #   █ (U+2588) = both black
    #   ' '        = both white
    rows = len(matrix)
    cols = len(matrix[0]) if rows > 0 else 0

    lines = []
    for y in range(0, rows, 2):
        line = "  "  # indent
        for x in range(cols):
            top = matrix[y][x]
            bottom = matrix[y + 1][x] if y + 1 < rows else False
            if top and bottom:
                line += "\033[40m \033[0m"  # both dark
            elif top and not bottom:
                line += "\033[40m\033[37m▄\033[0m"  # top dark, bottom light
            elif not top and bottom:
                line += "\033[40m\033[37m▀\033[0m"  # top light, bottom dark
            else:
                line += "\033[47m \033[0m"  # both light
        lines.append(line)

    print("\n".join(lines))


def _get_proxy_url() -> str:
    """Get the OAuth proxy URL."""
    from promaia.auth.callback_server import DEFAULT_PROXY_URL
    return os.environ.get("PROMAIA_OAUTH_PROXY_URL", DEFAULT_PROXY_URL).rstrip("/")


def _get_editor_secret() -> Optional[str]:
    """Get the EDITOR_SECRET from environment."""
    return os.environ.get("EDITOR_SECRET")


async def _edit_via_web(content: str, filename: str) -> Optional[str]:
    """Edit content via the web-based editor on the OAuth proxy.

    Creates an editor session, shows the QR/URL, polls for completion.

    Args:
        content: Initial file content
        filename: Display name for the file

    Returns:
        Updated content string, or None if cancelled/expired
    """
    import asyncio
    import uuid
    import httpx

    editor_secret = _get_editor_secret()
    if not editor_secret:
        return None

    proxy_url = _get_proxy_url()
    entry_id = uuid.uuid4().hex + uuid.uuid4().hex  # 64-char hex

    # Create editor session
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{proxy_url}/editor",
                json={"id": entry_id, "content": content},
                headers={"Authorization": f"Bearer {editor_secret}"},
            )
            if resp.status_code not in (200, 201):
                print_text(f"  ✗ Failed to create editor session: HTTP {resp.status_code}", style="red")
                try:
                    print_text(f"    {resp.json().get('error', resp.text[:200])}", style="dim")
                except Exception:
                    print_text(f"    {resp.text[:200]}", style="dim")
                print()
                return None

            data = resp.json()
    except httpx.ConnectError:
        print_text("  ✗ Could not connect to editor service", style="red")
        print()
        return None
    except Exception as e:
        print_text(f"  ✗ Error creating editor session: {e}", style="red")
        print()
        return None

    short_code = data.get("short_code", "")
    qr_url = f"{proxy_url}/qr/{short_code}"

    # Display QR code and URL
    print()
    _print_qr_code(qr_url)
    print()
    print_text(f"  Scan or open to edit \033[1m{filename}\033[0m:", style="cyan")
    print_text(f"    {qr_url}", style="bold")
    print()
    print_text("  Waiting for you to save in the browser...", style="dim")
    print_text("  Press Escape or q to cancel.\n", style="dim")

    # Use an asyncio event so a keystroke listener can cancel the poll
    cancel_event = asyncio.Event()

    async def _watch_for_cancel():
        """Listen for Escape/q/Ctrl-C to set the cancel event."""
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import Window
        from prompt_toolkit.layout.controls import FormattedTextControl

        kb = KeyBindings()

        @kb.add(Keys.Escape)
        @kb.add('q')
        @kb.add(Keys.ControlC)
        def _cancel(event):
            cancel_event.set()
            event.app.exit()

        app = Application(
            layout=Layout(Window(FormattedTextControl(text=''))),
            key_bindings=kb,
            full_screen=False,
            mouse_support=False,
        )
        try:
            await app.run_async()
        except Exception:
            cancel_event.set()

    async def _poll_for_result():
        """Poll the editor endpoint until ready, expired, or cancelled."""
        poll_interval = 2
        ping_interval = 55
        elapsed = 0

        async with httpx.AsyncClient(timeout=15.0) as client:
            while not cancel_event.is_set():
                await asyncio.sleep(poll_interval)
                if cancel_event.is_set():
                    return None
                elapsed += poll_interval

                # Ping to keep session alive
                if elapsed % ping_interval < poll_interval:
                    try:
                        await client.post(f"{proxy_url}/editor/{entry_id}/ping")
                    except Exception:
                        pass

                # Poll for result
                try:
                    resp = await client.get(f"{proxy_url}/editor/{entry_id}/poll")
                    if resp.status_code == 200:
                        poll_data = resp.json()
                        status = poll_data.get("status")
                        if status == "ready":
                            return poll_data.get("content")
                        elif status == "expired":
                            print_text("  ✗ Editor session expired.\n", style="yellow")
                            return None
                    elif resp.status_code == 404:
                        print_text("  ✗ Editor session not found (expired?).\n", style="yellow")
                        return None
                except httpx.ConnectError:
                    print_text("  ✗ Lost connection to editor service.\n", style="red")
                    return None

                # Update timer display
                minutes = elapsed // 60
                seconds = elapsed % 60
                sys.stdout.write(f"\033[2A\033[K  Waiting... ({minutes}m {seconds:02d}s elapsed)\n")
                sys.stdout.write("\033[K  Press Escape or q to cancel.\n")
                sys.stdout.flush()

        return None

    # Run poll and keystroke listener concurrently
    poll_task = asyncio.create_task(_poll_for_result())
    cancel_task = asyncio.create_task(_watch_for_cancel())

    done, pending = await asyncio.wait(
        [poll_task, cancel_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel whichever is still running
    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    result = poll_task.result() if poll_task in done else None

    if cancel_event.is_set() or result is None:
        # Clean up the editor session on the proxy
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.delete(f"{proxy_url}/editor/{entry_id}")
        except Exception:
            pass
        if cancel_event.is_set():
            print_text("\n  Cancelled.\n", style="dim")
        return None

    return result


async def _edit_prompt_file(filename: str, prompt_type: str) -> bool:
    """Edit a prompt file, offering web editor or manual editing.

    Args:
        filename: Prompt filename (relative to prompts dir)
        prompt_type: "classification" or "generation"

    Returns:
        True if file was saved (modified), False if unchanged or cancelled
    """
    prompts_dir = get_prompts_dir()
    prompts_dir.mkdir(parents=True, exist_ok=True)
    filepath = prompts_dir / filename

    # Load existing content or create starter template
    if filepath.exists():
        content = filepath.read_text(encoding="utf-8")
        created_new = False
    else:
        content = _get_starter_template(prompt_type, filename)
        created_new = True

    # Show editing options via inline selector
    has_secret = bool(_get_editor_secret())

    edit_rows: List = [
        "",
        f"  \033[1m\033[96mEdit: {filename}\033[0m",
        "",
    ]

    if has_secret:
        edit_rows.append({"label": "Open in web editor (browser-based)", "indent": 0, "action": "web"})
        edit_rows.append({"label": "Edit file manually (shows path)", "indent": 0, "action": "manual"})
    else:
        edit_rows.append({"label": "Edit file manually (shows path)", "indent": 0, "action": "manual"})
        edit_rows.append({"label": "Set up web editor (requires EDITOR_SECRET)", "indent": 0, "action": "setup_editor"})

    if not created_new:
        edit_rows.append({"label": "Clear prompt (delete file)", "indent": 0, "action": "clear"})

    edit_rows.append({"label": "Back", "indent": 0, "action": "back"})
    edit_rows.append("")

    selected = await _inline_select(edit_rows)

    if not selected or selected.get('action') in ('quit', 'back'):
        return False

    action = selected['action']
    if action == 'web':
        return await _do_web_edit(filepath, content, filename, created_new)
    elif action == 'manual':
        return _do_manual_edit(filepath, content, filename, created_new)
    elif action == 'clear':
        filepath.unlink()
        print_text(f"\n  ✓ Prompt cleared ({filename} removed)\n", style="green")
        return True
    elif action == 'setup_editor':
        _prompt_for_editor_secret()
        return False

    return False


async def _do_web_edit(filepath: Path, content: str, filename: str, created_new: bool) -> bool:
    """Handle the web editor flow."""
    result = await _edit_via_web(content, filename)

    if result is None:
        return False

    if not result.strip():
        # User cleared content
        if filepath.exists():
            filepath.unlink()
        print_text("  Prompt cleared (file removed).\n", style="yellow")
        return True

    # Save updated content
    filepath.write_text(result, encoding="utf-8")
    word_count = len(result.split())
    print_text(f"  ✓ Prompt saved ({word_count} words)\n", style="green")
    return True


def _do_manual_edit(filepath: Path, content: str, filename: str, created_new: bool) -> bool:
    """Handle the manual file editing flow."""
    # Write content to file (either starter template or existing)
    if created_new:
        filepath.write_text(content, encoding="utf-8")

    pre_edit = filepath.read_text(encoding="utf-8")

    print()
    print_text(f"  File location:", style="dim")
    print_text(f"    {filepath}", style="bold")
    print()
    print_text("  Edit the file in your preferred editor, then press Enter here.", style="dim")
    print()

    try:
        input("  Press Enter when done editing (or Ctrl+C to cancel)... ")
    except (EOFError, KeyboardInterrupt):
        print()
        if created_new and filepath.exists():
            filepath.unlink()  # Clean up template if cancelled
        return False

    # Check if content changed
    if not filepath.exists():
        # User deleted the file
        print_text("  File was removed.\n", style="yellow")
        return True

    post_edit = filepath.read_text(encoding="utf-8")

    if post_edit == pre_edit:
        if created_new and post_edit.strip():
            print_text("  ✓ Prompt saved (template kept as-is)\n", style="green")
            return True
        else:
            print_text("  No changes detected.\n", style="dim")
            if created_new:
                filepath.unlink()
            return False
    else:
        if not post_edit.strip():
            filepath.unlink()
            print_text("  Prompt cleared (file removed).\n", style="yellow")
            return True
        else:
            word_count = len(post_edit.split())
            print_text(f"  ✓ Prompt saved ({word_count} words)\n", style="green")
            return True


def _prompt_for_editor_secret():
    """Prompt user to enter EDITOR_SECRET and save it to .env."""
    from promaia.utils.env_writer import get_env_path

    env_path = get_env_path()

    print()
    print_text("  Web Editor Setup", style="bold cyan")
    print()
    print_text("  The web editor lets you edit prompts from any device via a browser.", style="dim")
    print_text("  It requires an EDITOR_SECRET that matches the one on the OAuth proxy.", style="dim")
    print()

    try:
        secret = input("  Enter EDITOR_SECRET (or press Enter to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if not secret:
        return

    # Save to .env
    try:
        # Read existing .env content
        if env_path.exists():
            env_content = env_path.read_text(encoding="utf-8")
        else:
            env_content = ""

        # Check if EDITOR_SECRET already exists
        lines = env_content.splitlines()
        found = False
        for i, line in enumerate(lines):
            if line.startswith("EDITOR_SECRET="):
                lines[i] = f"EDITOR_SECRET={secret}"
                found = True
                break

        if not found:
            lines.append(f"EDITOR_SECRET={secret}")

        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Also set in current process so it takes effect immediately
        os.environ["EDITOR_SECRET"] = secret

        print_text(f"\n  ✓ EDITOR_SECRET saved to {env_path}", style="green")
        print_text("  Web editor is now available.\n", style="green")

    except Exception as e:
        print_text(f"\n  ✗ Failed to save: {e}\n", style="red")


def _get_starter_template(prompt_type: str, filename: str) -> str:
    """Get a starter template for a new prompt file."""
    if prompt_type == "classification":
        return """<!--
This prompt tells the AI how to triage incoming emails.

Template variables (filled in automatically):
  {user_email}     - The user's email address
  {workspace}      - The workspace name
  {from_addr}      - Sender's email address
  {to_addr}        - Recipient(s)
  {subject}        - Email subject line
  {date}           - Email date
  {body}           - Email body (truncated to 1000 chars)
  {thread_context} - Previous thread context

The AI must return a JSON object with these fields:
  pertains_to_me (bool)       - Is this relevant to the user?
  is_spam (bool)              - Is this spam/promotional?
  addressed_to_user (bool|"ambiguous") - Is it directly addressed to the user?
  requires_response (bool)    - Does it need a response?
  reasoning (string)          - Explanation of the classification

Example: to auto-skip shipping notifications, add a rule like:
  "Emails from shipping carriers (UPS, FedEx, USPS) or order confirmations
   should be classified as not requiring a response."
-->
You are an email classifier for {user_email} in the {workspace} workspace.

Analyze the following email and return a JSON object with classification results.

From: {from_addr}
To: {to_addr}
Subject: {subject}
Date: {date}

Body:
{body}

Thread context:
{thread_context}

Return ONLY a JSON object with these fields:
- pertains_to_me (boolean)
- is_spam (boolean)
- addressed_to_user (boolean or "ambiguous")
- requires_response (boolean)
- reasoning (string explaining your classification)
"""
    else:  # generation
        return """<!--
This prompt defines the AI's persona when drafting email replies.

Template variables (filled in automatically):
  {today_date}    - Current date (e.g., "March 17, 2026")
  {current_time}  - Current time (e.g., "02:30 PM UTC")

The AI will also receive:
  - Relevant context from your knowledge base (vector search results)
  - Related email threads
  - The full email thread conversation
  - The specific email being replied to

Tips:
  - Describe the user's role, communication style, and preferences
  - Include any standard signatures or sign-offs
  - Mention specific handling for common email types (invoices, etc.)
-->
You are a helpful email assistant drafting replies on behalf of the user.

Today's date is {today_date}. The current time is {current_time}.

Write concise, professional email responses. Match the tone of the
incoming email — formal if they're formal, casual if they're casual.

Do not include a subject line in your response (it's added automatically).
Write only the email body.
"""


async def _handle_add_gmail(workspace: str):
    """Sub-flow for adding a Gmail database to a workspace."""
    from prompt_toolkit import PromptSession
    from promaia.auth.flow import configure_credential
    from promaia.auth.registry import get_integration
    from promaia.config.databases import get_database_manager

    session = PromptSession()
    google_int = get_integration("google")

    # Build inline selector for account choice
    authed = google_int.list_authenticated_accounts()

    rows: List = [
        "",
        f"  \033[1m\033[96mAdd Gmail database to {workspace}\033[0m",
        "",
    ]

    if authed:
        rows.append("  \033[2mAuthenticated accounts:\033[0m")
        for acct in authed:
            rows.append({
                "label": acct,
                "indent": 2,
                "action": "select_account",
                "email": acct,
            })

    rows.append({
        "label": "New account...",
        "indent": 2,
        "action": "new_account",
    })
    rows.append({
        "label": "Cancel",
        "indent": 0,
        "action": "quit",
    })
    rows.append("")

    selected = await _inline_select(rows)

    if not selected or selected.get("action") == "quit":
        return

    if selected["action"] == "select_account":
        email = selected["email"]
    else:
        # New account — prompt for address
        email = await session.prompt_async("  Gmail address: ")
        email = email.strip()

    if not email or "@" not in email:
        print_text("  ✗ Invalid email address\n", style="red")
        return

    # Check if already a database in this workspace
    db_manager = get_database_manager()
    existing = [
        db for db in db_manager.get_workspace_databases(workspace)
        if db.source_type == "gmail" and db.database_id == email
    ]
    if existing:
        print_text(f"  ✗ {email} is already configured in {workspace}\n", style="yellow")
        return

    # Check auth
    has_token, status = _get_google_auth_status(email)
    if not has_token:
        print_text(f"\n  {email} needs authentication.", style="yellow")
        print_text("  Starting Google OAuth flow...\n", style="cyan")
        success = await configure_credential(google_int, account=email)
        if not success:
            print_text("  ✗ Authentication failed\n", style="red")
            return

    # Add database
    config = {
        "source_type": "gmail",
        "database_id": email,
        "workspace": workspace,
        "sync_enabled": True,
    }
    db_manager.add_database("gmail", config, workspace)
    print_text(f"\n  ✓ Added {email} to {workspace}\n", style="green")


async def _handle_reauth(email: str):
    """Sub-flow for re-authenticating a Google account."""
    from promaia.auth.flow import configure_credential
    from promaia.auth.registry import get_integration

    google_int = get_integration("google")
    print_text(f"\n  Re-authenticating {email}...\n", style="cyan")
    success = await configure_credential(google_int, account=email)
    if success:
        print_text(f"  ✓ {email} re-authenticated\n", style="green")
    else:
        print_text(f"  ✗ Re-authentication failed\n", style="red")


async def _handle_add_gmail_account():
    """Sub-flow for authenticating a new Gmail account (standalone, no workspace)."""
    from prompt_toolkit import PromptSession
    from promaia.auth.flow import configure_credential
    from promaia.auth.registry import get_integration

    session = PromptSession()
    google_int = get_integration("google")

    print_text("\n  Add Gmail Account\n", style="bold cyan")

    email = await session.prompt_async("  Gmail address: ")
    email = email.strip()

    if not email or "@" not in email:
        print_text("  ✗ Invalid email address\n", style="red")
        return

    has_token, status = _get_google_auth_status(email)
    if has_token:
        print_text(f"  ✓ {email} is already authenticated ({status})\n", style="green")
        return

    print_text(f"  Starting Google OAuth flow for {email}...\n", style="cyan")
    success = await configure_credential(google_int, account=email)
    if success:
        print_text(f"\n  ✓ {email} authenticated\n", style="green")
    else:
        print_text(f"\n  ✗ Authentication failed\n", style="red")


async def _handle_add_workspace():
    """Sub-flow for adding a new workspace."""
    from prompt_toolkit import PromptSession
    from promaia.config.workspaces import get_workspace_manager

    session = PromptSession()
    ws_manager = get_workspace_manager()

    print_text("\n  Add Workspace\n", style="bold cyan")

    name = await session.prompt_async("  Workspace name: ")
    name = name.strip().lower()

    if not name:
        print_text("  ✗ Name required\n", style="red")
        return

    if name in ws_manager.list_workspaces(include_archived=True):
        print_text(f"  ✗ Workspace '{name}' already exists\n", style="yellow")
        return

    description = await session.prompt_async("  Description (optional): ")
    description = description.strip()

    ws_manager.add_workspace(name, description or "")
    print_text(f"\n  ✓ Workspace '{name}' created\n", style="green")


async def _handle_manage_gmail(workspace: str, email: str):
    """Sub-flow for managing a Gmail database (remove, re-auth)."""
    has_token, status = _get_google_auth_status(email)

    rows: List = [
        "",
        f"  \033[1m\033[96m📧 {email}\033[0m",
        f"  \033[2mWorkspace: {workspace}  •  {status}\033[0m",
        "",
    ]

    rows.append({
        "label": "Remove from workspace",
        "indent": 0,
        "action": "remove",
    })

    if not has_token:
        rows.append({
            "label": "Re-authenticate",
            "indent": 0,
            "action": "reauth",
        })

    rows.append({
        "label": "Back",
        "indent": 0,
        "action": "back",
    })
    rows.append("")

    selected = await _inline_select(rows)

    if not selected or selected.get("action") in ("quit", "back"):
        return

    if selected["action"] == "remove":
        from promaia.config.databases import get_database_manager
        db_manager = get_database_manager()
        removed = db_manager.remove_database(email, workspace=workspace)
        if removed:
            print_text(f"\n  ✓ Removed {email} from {workspace}\n", style="green")
        else:
            print_text(f"\n  ✗ Could not remove {email}\n", style="red")

    elif selected["action"] == "reauth":
        await _handle_reauth(email)


def _handle_toggle_mail(workspace: str):
    """Toggle mail_enabled for a workspace."""
    from promaia.config.workspaces import get_workspace_manager

    ws_manager = get_workspace_manager()
    ws_config = ws_manager.get_workspace(workspace)
    if not ws_config:
        print_text(f"  ✗ Workspace '{workspace}' not found\n", style="red")
        return

    ws_config.mail_enabled = not ws_config.mail_enabled
    ws_manager.save_config()

    state = "enabled" if ws_config.mail_enabled else "disabled"
    print_text(f"\n  ✓ Mail processing {state} for {workspace}\n", style="green")


async def launch_setup():
    """Main entry point for maia mail setup."""
    while True:
        rows = _build_overview_rows()
        selected = await _inline_select(rows)

        if not selected or selected.get('action') == 'quit':
            break

        action = selected["action"]

        if action == "add_gmail_account":
            await _handle_add_gmail_account()
            input("  Press Enter to continue...")

        elif action == "edit_prompt":
            await _edit_prompt_file(selected["filename"], selected["prompt_type"])
            input("  Press Enter to continue...")

        elif action == "add_gmail":
            await _handle_add_gmail(selected["workspace"])
            input("  Press Enter to continue...")

        elif action == "manage_gmail":
            await _handle_manage_gmail(selected["workspace"], selected["email"])
            input("  Press Enter to continue...")

        elif action == "toggle_mail":
            _handle_toggle_mail(selected["workspace"])

        elif action == "reauth":
            await _handle_reauth(selected["email"])
            input("  Press Enter to continue...")

        elif action == "add_workspace":
            await _handle_add_workspace()
            input("  Press Enter to continue...")

    print_text("\n  Done.\n", style="dim")
