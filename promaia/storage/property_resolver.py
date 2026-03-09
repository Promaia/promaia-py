"""
Property Resolver - ID-based property and option resolution for Notion properties.

This module provides utilities to resolve between property/option names and their
stable Notion IDs. This allows the system to be resilient to property renames.
"""
import sqlite3
import logging
from typing import Optional, List, Dict, Any, Tuple
from functools import lru_cache

logger = logging.getLogger(__name__)


class PropertyResolver:
    """Resolves between property/option names and IDs using the local database."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            from promaia.utils.env_writer import get_db_path
            db_path = str(get_db_path())
        self.db_path = db_path

    @lru_cache(maxsize=1000)
    def get_property_id(self, database_id: str, property_name: str) -> Optional[str]:
        """
        Get the property ID for a given property name.

        Args:
            database_id: The Notion database ID
            property_name: The property name

        Returns:
            The property ID if found, None otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT property_id
                    FROM notion_property_schema
                    WHERE database_id = ? AND property_name = ? AND is_active = TRUE
                """, (database_id, property_name))

                result = cursor.fetchone()
                return result[0] if result else None

        except Exception as e:
            logger.error(f"Failed to get property ID for {property_name}: {e}")
            return None

    @lru_cache(maxsize=1000)
    def get_property_name(self, database_id: str, property_id: str) -> Optional[str]:
        """
        Get the current property name for a given property ID.

        Args:
            database_id: The Notion database ID
            property_id: The property ID

        Returns:
            The property name if found, None otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT property_name
                    FROM notion_property_schema
                    WHERE database_id = ? AND property_id = ? AND is_active = TRUE
                """, (database_id, property_id))

                result = cursor.fetchone()
                return result[0] if result else None

        except Exception as e:
            logger.error(f"Failed to get property name for {property_id}: {e}")
            return None

    @lru_cache(maxsize=2000)
    def get_option_id(self, database_id: str, property_id: str, option_name: str) -> Optional[str]:
        """
        Get the option ID for a given option name within a property.

        Args:
            database_id: The Notion database ID
            property_id: The property ID
            option_name: The option name

        Returns:
            The option ID if found, None otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT option_id
                    FROM notion_select_options
                    WHERE database_id = ? AND property_id = ? AND option_name = ? AND is_active = TRUE
                """, (database_id, property_id, option_name))

                result = cursor.fetchone()
                return result[0] if result else None

        except Exception as e:
            logger.error(f"Failed to get option ID for {option_name}: {e}")
            return None

    @lru_cache(maxsize=2000)
    def get_option_name(self, database_id: str, property_id: str, option_id: str) -> Optional[str]:
        """
        Get the current option name for a given option ID.

        Args:
            database_id: The Notion database ID
            property_id: The property ID
            option_id: The option ID

        Returns:
            The option name if found, None otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT option_name
                    FROM notion_select_options
                    WHERE database_id = ? AND property_id = ? AND option_id = ? AND is_active = TRUE
                """, (database_id, property_id, option_id))

                result = cursor.fetchone()
                return result[0] if result else None

        except Exception as e:
            logger.error(f"Failed to get option name for {option_id}: {e}")
            return None

    def get_property_type(self, database_id: str, property_id: str) -> Optional[str]:
        """
        Get the Notion type for a property.

        Args:
            database_id: The Notion database ID
            property_id: The property ID

        Returns:
            The notion_type if found, None otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT notion_type
                    FROM notion_property_schema
                    WHERE database_id = ? AND property_id = ? AND is_active = TRUE
                """, (database_id, property_id))

                result = cursor.fetchone()
                return result[0] if result else None

        except Exception as e:
            logger.error(f"Failed to get property type for {property_id}: {e}")
            return None

    def resolve_filter_value(
        self,
        database_id: str,
        property_id: str,
        value: Any
    ) -> Tuple[Optional[str], Optional[Any]]:
        """
        Resolve a filter value from ID to name.

        For select/multi-select/status properties, resolves option IDs to current names.
        For other properties, returns the value unchanged.

        Args:
            database_id: The Notion database ID
            property_id: The property ID
            value: The filter value (option ID for select properties, or direct value)

        Returns:
            Tuple of (property_name, resolved_value) if successful, (None, None) otherwise
        """
        try:
            property_name = self.get_property_name(database_id, property_id)
            if not property_name:
                logger.warning(f"Could not resolve property ID {property_id}")
                return None, None

            property_type = self.get_property_type(database_id, property_id)

            # For select/multi-select/status, resolve option IDs to names
            if property_type in ('select', 'multi_select', 'status'):
                if isinstance(value, list):
                    # Multiple values - resolve each
                    resolved_values = []
                    for v in value:
                        option_name = self.get_option_name(database_id, property_id, v)
                        if option_name:
                            resolved_values.append(option_name)
                        else:
                            logger.warning(f"Could not resolve option ID {v}")
                    return property_name, resolved_values if resolved_values else None
                else:
                    # Single value
                    option_name = self.get_option_name(database_id, property_id, value)
                    if option_name:
                        return property_name, option_name
                    else:
                        logger.warning(f"Could not resolve option ID {value}")
                        return None, None
            else:
                # For non-select properties, return value unchanged
                return property_name, value

        except Exception as e:
            logger.error(f"Failed to resolve filter value: {e}")
            return None, None

    def resolve_property_name_to_id(
        self,
        database_id: str,
        property_name: str,
        value: Any
    ) -> Tuple[Optional[str], Optional[Any]]:
        """
        Resolve a property name and value to property ID and option IDs.

        This is the inverse of resolve_filter_value, used for converting
        name-based configs to ID-based internal representation.

        Args:
            database_id: The Notion database ID
            property_name: The property name
            value: The filter value (option name for select properties, or direct value)

        Returns:
            Tuple of (property_id, resolved_value) if successful, (None, None) otherwise
        """
        try:
            property_id = self.get_property_id(database_id, property_name)
            if not property_id:
                logger.warning(f"Could not resolve property name {property_name}")
                return None, None

            property_type = self.get_property_type(database_id, property_id)

            # For select/multi-select/status, resolve option names to IDs
            if property_type in ('select', 'multi_select', 'status'):
                if isinstance(value, list):
                    # Multiple values - resolve each
                    resolved_values = []
                    for v in value:
                        option_id = self.get_option_id(database_id, property_id, v)
                        if option_id:
                            resolved_values.append(option_id)
                        else:
                            logger.warning(f"Could not resolve option name {v}")
                    return property_id, resolved_values if resolved_values else None
                else:
                    # Single value
                    option_id = self.get_option_id(database_id, property_id, value)
                    if option_id:
                        return property_id, option_id
                    else:
                        logger.warning(f"Could not resolve option name {value}")
                        return None, None
            else:
                # For non-select properties, return value unchanged
                return property_id, value

        except Exception as e:
            logger.error(f"Failed to resolve property name to ID: {e}")
            return None, None

    def clear_cache(self):
        """Clear the LRU cache. Call this after schema updates."""
        self.get_property_id.cache_clear()
        self.get_property_name.cache_clear()
        self.get_option_id.cache_clear()
        self.get_option_name.cache_clear()

    def get_all_properties(self, database_id: str) -> List[Dict[str, Any]]:
        """
        Get all properties for a database.

        Args:
            database_id: The Notion database ID

        Returns:
            List of property dictionaries with id, name, and type
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT property_id, property_name, notion_type
                    FROM notion_property_schema
                    WHERE database_id = ? AND is_active = TRUE
                """, (database_id,))

                properties = []
                for row in cursor.fetchall():
                    properties.append({
                        'id': row[0],
                        'name': row[1],
                        'type': row[2]
                    })
                return properties

        except Exception as e:
            logger.error(f"Failed to get all properties: {e}")
            return []

    def get_all_options(self, database_id: str, property_id: str) -> List[Dict[str, Any]]:
        """
        Get all options for a select/multi-select/status property.

        Args:
            database_id: The Notion database ID
            property_id: The property ID

        Returns:
            List of option dictionaries with id, name, and color
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT option_id, option_name, option_color
                    FROM notion_select_options
                    WHERE database_id = ? AND property_id = ? AND is_active = TRUE
                """, (database_id, property_id))

                options = []
                for row in cursor.fetchall():
                    options.append({
                        'id': row[0],
                        'name': row[1],
                        'color': row[2]
                    })
                return options

        except Exception as e:
            logger.error(f"Failed to get all options: {e}")
            return []
