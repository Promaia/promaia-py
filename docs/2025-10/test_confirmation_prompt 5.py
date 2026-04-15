#!/usr/bin/env python3
"""
Test the new confirmation prompt options.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from promaia.utils.config import load_environment
load_environment()

from promaia.ai.nl_orchestrator import AgenticNLQueryProcessor
from promaia.utils.display import print_text

def test_option(option, description):
    print_text(f"\n{'='*70}", style="bold cyan")
    print_text(f"TEST: {description}", style="bold cyan")
    print_text(f"Simulating user input: '{option}'", style="yellow")
    print_text(f"{'='*70}", style="cyan")
    
    processor = AgenticNLQueryProcessor(debug=False)
    
    # Mock the input
    import builtins
    original_input = builtins.input
    builtins.input = lambda _: option
    
    try:
        result = processor.process_query("gmail with term test", max_retries=0)
        
        if result['success']:
            print_text(f"\n✅ Query successful", style="green")
            print_text(f"Pattern saved: {result.get('learned', False)}", style="white")
        else:
            print_text(f"\n❌ Query failed", style="red")
    finally:
        builtins.input = original_input


def main():
    print_text("\n" + "="*70, style="bold magenta")
    print_text("🧪 CONFIRMATION PROMPT TEST", style="bold magenta")
    print_text("="*70, style="bold magenta")
    
    print_text("\nTesting the three options:", style="white")
    print_text("  1. Enter (accept and save)", style="dim")
    print_text("  2. 'm' (modify - not yet implemented)", style="dim")
    print_text("  3. 'q' (quit/skip)", style="dim")
    
    test_option("", "Pressing Enter (accept)")
    test_option("m", "Typing 'm' (modify)")
    test_option("q", "Typing 'q' (skip)")
    
    print_text("\n" + "="*70, style="bold magenta")
    print_text("SUMMARY", style="bold yellow")
    print_text("="*70, style="yellow")
    print_text("\nThe new prompt gives users three clear options:", style="white")
    print_text("  • Enter → Quick accept (saves pattern)", style="green")
    print_text("  • m → Modify query (future feature)", style="yellow")
    print_text("  • q → Skip saving (doesn't save pattern)", style="red")
    print_text("\nMuch better than Y/n!", style="dim")
    print_text("="*70 + "\n", style="dim")


if __name__ == "__main__":
    main()

