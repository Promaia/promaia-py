import os
from typing import Any

def get_notion_database_id(nickname: str) -> str:
    """
    Retrieves the Notion database ID for a given nickname.
    First tries the new DatabaseManager configuration system, then falls back to environment variables.
    
    Args:
        nickname: The nickname of the database (e.g., "journal", "cms", "stories").

    Returns:
        The Notion database ID.

    Raises:
        ValueError: If the database configuration is not found or database ID is empty.
    """
    # Try the new configuration system first
    try:
        from promaia.config.databases import get_database_config
        db_config = get_database_config(nickname)
        if db_config and db_config.database_id:
            return db_config.database_id
    except ImportError:
        # Fall back to old system if new config module not available
        pass
    except Exception as e:
        # Log the error but continue to fallback
        print(f"Warning: Error accessing new config system: {e}")
    
    # Fallback to environment variables (legacy support)
    env_var_name = f"NOTION_{nickname.upper()}_DATABASE_ID"
    db_id = os.getenv(env_var_name)

    if db_id:
        # Placeholder check (optional, but good practice)
        if db_id.startswith("YOUR_"):
            print(
                f"Warning: Database ID for '{nickname}' from '{env_var_name}' "
                f"appears to be a placeholder ('{db_id}'). Please use the actual ID."
            )
        return db_id

    # If neither config system nor environment variable found
    raise ValueError(
        f"Database configuration for nickname '{nickname}' not found. "
        f"Please configure it in promaia.config.json or set environment variable '{env_var_name}'."
    )

# Example usage (can be removed or kept for testing)
if __name__ == "__main__":
    # Set dummy environment variables for testing
    os.environ["NOTION_JOURNAL_DATABASE_ID"] = "dummy_journal_id_env"
    os.environ["NOTION_CMS_DATABASE_ID"] = "dummy_cms_id_env"
    os.environ["NOTION_TEST_DATABASE_ID"] = "YOUR_PROMPTS_DATABASE_ID_ENV"

    try:
        print(f"Journal DB ID: {get_notion_database_id('journal')}")
        print(f"CMS DB ID: {get_notion_database_id('cms')}")
        # Test placeholder warning
        print(f"Test DB ID: {get_notion_database_id('test')}")

        # Test not found
        try:
            get_notion_database_id("nonexistent")
        except ValueError as e:
            print(f"Error (expected): {e}")

    finally:
        # Clean up dummy environment variables
        del os.environ["NOTION_JOURNAL_DATABASE_ID"]
        del os.environ["NOTION_CMS_DATABASE_ID"]
        del os.environ["NOTION_TEST_DATABASE_ID"]
        print("Test complete. Environment variables were set/unset for this test.")
        print("Please ensure your actual database configurations are set in promaia.config.json or environment variables.") 