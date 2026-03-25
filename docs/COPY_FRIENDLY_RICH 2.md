# Copy-Friendly Rich Display

The `promaia.utils.display` module provides Rich markdown rendering that maintains beautiful terminal output while ensuring content copies cleanly without formatting artifacts.

## Quick Start

```python
from promaia.utils.display import print_markdown, print_code, print_text, print_separator

# Display markdown content (copies cleanly!)
print_markdown(content, title="AI Response")

# Display code with syntax highlighting
print_code(code, language="python", title="Generated Code")

# Display styled text
print_text("Success!", style="bold green")

# Display clean separators
print_separator()  # Simple line
print_separator("Section Title")  # Line with text
```

## Configuration

Display settings are configured in `promaia.config.json` under `global.display`:

```json
{
  "global": {
    "display": {
      "plain_output": false,
      "copy_friendly_mode": true
    }
  }
}
```

### Display Options

- **`plain_output`** (boolean): If `true`, uses plain text output with no colors or formatting
- **`copy_friendly_mode`** (boolean): Enables copy-optimized formatting (default: true)

## Environment Override

For temporary testing, you can still override via environment variable:

```bash
MAIA_PLAIN_OUTPUT=true maia chat  # Force plain text mode
```

## Key Benefits

✅ **No box characters** - Eliminates `┏━━━┓` artifacts that break copy/paste
✅ **Dynamic width** - Automatically adapts to terminal window size
✅ **Clean separators** - Uses simple dashes instead of complex borders
✅ **Preserved structure** - Maintains markdown hierarchy when copied
✅ **Configuration-driven** - Easy to customize via config file

## Usage Examples

### Markdown Display
```python
# Rich mode (default) - beautiful colors, copy-friendly
print_markdown("# Hello World\nThis **copies** cleanly!")

# Plain mode - guaranteed plain text
# Set "plain_output": true in config or use MAIA_PLAIN_OUTPUT=true
```

### Code Display  
```python
# Syntax highlighted code that copies perfectly
code = "def hello():\n    return 'world'"
print_code(code, language="python", title="Example Function")
```

### Text Display
```python
# Styled text for status messages
print_text("✅ Operation completed successfully!", style="bold green")
print_text("⚠️ Warning: Check configuration", style="bold yellow")
```

### Separator Display
```python
# Clean section separators
print_separator()  # Simple horizontal line
print_separator("Section Title")  # Line with centered text

# Use separators to organize output without boxes
print_text("Inbound Message", style="bold")
print_separator()
print_text(email_content)
print_separator()
```

## Integration

Replace existing Rich usage in your code:

```python
# OLD (problematic)
from rich.console import Console
from rich.panel import Panel

console = Console()
console.print(Panel(content, title="Response"))  # Creates boxes!

# NEW (copy-friendly)  
from promaia.utils.display import print_markdown

print_markdown(content, title="Response")  # No boxes, copies clean!
```

## Real-World Examples

### Maia Mail

The Maia Mail feature (`promaia/mail/`) demonstrates the copy-friendly approach throughout:

**Email Draft Review UI** (`review_ui.py`):
- Clean status bars without box characters
- Simple separators using dashes
- Minimal, scannable interface for reviewing email drafts

**Draft Chat Interface** (`draft_chat.py`):
- Artifact rendering without boxes (just clean separators)
- Inbound message display with simple headers
- All content is easily copy/paste-able

**Key Techniques Used**:
```python
# Simple status information
print(f"Progress: [{bar}] {resolved}/{total} resolved ({percent}%)")
print(f"Status: ✅ {sent} sent  •  🗄️ {archived} archived  •  ⏳ {pending} pending")

# Section headers with separators
print("INBOUND MESSAGE")
print_separator()
print(message_content)
print_separator()

# Clean draft artifacts
print(f"Draft #{number}")
print("─" * 65)
print(draft_content)
print("─" * 65)
```

The entire mail interface maintains beautiful formatting while ensuring everything copies cleanly - no box artifacts, no formatting breaks. 