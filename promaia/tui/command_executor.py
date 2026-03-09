"""Inline command executor - runs maia commands and captures output."""

import io
import sys
import inspect
import asyncio
from contextlib import redirect_stdout, redirect_stderr
from rich.text import Text
from typing import Optional
import argparse


class CommandExecutor:
    """
    Executes maia commands inline and captures output.

    Just like Claude Code! Any /command runs as `maia command` and
    shows output inline in the TUI.
    """

    def __init__(self):
        self.parser = None  # Will be lazy-loaded

    def _get_parser(self) -> argparse.ArgumentParser:
        """Get the CLI parser (lazy load to avoid circular imports)."""
        if self.parser is None:
            # Import from promaia.cli module (the file, not the package)
            # We need to import the module directly to avoid package/module naming conflict
            import importlib.util
            import sys
            from pathlib import Path

            # Get the path to cli.py
            cli_path = Path(__file__).parent.parent / 'cli.py'

            # Load the module
            spec = importlib.util.spec_from_file_location("promaia_cli_module", cli_path)
            cli_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cli_module)

            # Get the parser
            self.parser = cli_module.create_parser()
        return self.parser

    async def execute(self, command: str) -> Text:
        """
        Execute a maia command and return formatted output.

        Args:
            command: The command string (without leading /, e.g., "agent list")

        Returns:
            Rich Text object with formatted output
        """
        result = Text()
        result.append(f"$ maia {command}\n", style="bold dim")

        try:
            # Parse command
            args = self._parse_command(command)

            if args is None:
                result.append("❌ Unknown command\n", style="bold red")
                return result

            # Check if command has a handler
            if not hasattr(args, 'func'):
                result.append("❌ No handler for this command\n", style="bold red")
                result.append("Try /help to see available commands\n", style="dim")
                return result

            # Capture output
            captured_out = io.StringIO()
            captured_err = io.StringIO()

            with redirect_stdout(captured_out), redirect_stderr(captured_err):
                try:
                    # Execute command (handle both sync and async)
                    if inspect.iscoroutinefunction(args.func):
                        await args.func(args)
                    else:
                        args.func(args)
                except Exception as e:
                    captured_err.write(f"Error: {e}\n")

            # Format output
            stdout = captured_out.getvalue()
            stderr = captured_err.getvalue()

            if stdout:
                result.append(stdout)

            if stderr:
                result.append(stderr, style="red")

            if not stdout and not stderr:
                result.append("✅ Command completed\n", style="green")

        except SystemExit:
            # Argparse calls sys.exit on errors, catch it
            result.append("❌ Command parsing failed\n", style="bold red")
            result.append("Try /help or check command syntax\n", style="dim")
        except Exception as e:
            result.append(f"❌ Error: {e}\n", style="bold red")

        return result

    def _parse_command(self, command: str) -> Optional[argparse.Namespace]:
        """
        Parse command string into args object.

        Args:
            command: Command string (e.g., "agent list")

        Returns:
            Parsed args or None if parsing failed
        """
        try:
            parser = self._get_parser()

            # Split command respecting quotes
            import shlex
            args_list = shlex.split(command)

            # Parse
            return parser.parse_args(args_list)

        except SystemExit:
            # Argparse calls sys.exit on errors
            return None
        except Exception as e:
            return None
