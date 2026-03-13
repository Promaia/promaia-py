"""
Configuration management for Maia.
"""

from .databases import (
    DatabaseConfig,
    DatabaseManager,
    get_database_manager,
    get_database_config,
    list_databases,
    add_database
)

__all__ = [
    'DatabaseConfig',
    'DatabaseManager', 
    'get_database_manager',
    'get_database_config',
    'list_databases',
    'add_database'
] 