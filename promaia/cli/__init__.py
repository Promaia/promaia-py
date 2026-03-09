"""
CLI module for Maia.
"""
from .database_commands import add_database_commands
from .conversion_commands import add_conversion_commands
# from .edit_commands import edit  # Using argparse handlers in main CLI instead

def extract_database_names_from_sources(sources):
    """Extract qualified database names from source selections (e.g., 'acme.journal:7' -> 'acme.journal')."""
    database_names = []
    for source in sources:
        # Remove any day filters (e.g., ':7', ':all') first
        clean_source = source.split(':')[0]
        
        # Skip Discord channels (contain '#')
        if '#' in clean_source:
            continue
            
        # Keep the full qualified name (workspace.database) for precise targeting
        database_names.append(clean_source)
    return list(set(database_names))  # Remove duplicates

__all__ = ['add_database_commands', 'add_conversion_commands', 'extract_database_names_from_sources']