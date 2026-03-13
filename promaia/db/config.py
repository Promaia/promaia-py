"""
Database configuration management.

Supports both SQLite (development) and PostgreSQL (production).
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class DatabaseConfig:
    """Database configuration."""
    db_type: str  # "sqlite" or "postgresql"

    # SQLite config
    sqlite_path: Optional[str] = None

    # PostgreSQL config
    pg_host: Optional[str] = None
    pg_port: Optional[int] = None
    pg_database: Optional[str] = None
    pg_user: Optional[str] = None
    pg_password: Optional[str] = None

    # Connection pooling
    pool_size: int = 5
    max_overflow: int = 10


def get_config_path() -> Path:
    """Get path to database config file."""
    from promaia.utils.env_writer import get_data_dir
    return get_data_dir() / "db_config.json"


def get_default_sqlite_path() -> Path:
    """Get default SQLite database path."""
    from promaia.utils.env_writer import get_data_dir
    return get_data_dir() / "promaia.db"


def load_config() -> DatabaseConfig:
    """
    Load database configuration from file.

    Returns:
        DatabaseConfig object

    If no config file exists, defaults to SQLite.
    """
    config_path = get_config_path()

    if not config_path.exists():
        # Default to SQLite
        return DatabaseConfig(
            db_type="sqlite",
            sqlite_path=str(get_default_sqlite_path())
        )

    try:
        with open(config_path, 'r') as f:
            data = json.load(f)

        db_type = data.get("type", "sqlite")

        if db_type == "sqlite":
            sqlite_path = data.get("path")
            if not sqlite_path:
                sqlite_path = str(get_default_sqlite_path())

            # Expand ~ to home directory
            sqlite_path = os.path.expanduser(sqlite_path)

            return DatabaseConfig(
                db_type="sqlite",
                sqlite_path=sqlite_path
            )

        elif db_type == "postgresql":
            conn = data.get("connection", {})
            return DatabaseConfig(
                db_type="postgresql",
                pg_host=conn.get("host", "localhost"),
                pg_port=conn.get("port", 5432),
                pg_database=conn.get("database", "promaia"),
                pg_user=conn.get("user"),
                pg_password=conn.get("password"),
                pool_size=data.get("pool_size", 5),
                max_overflow=data.get("max_overflow", 10)
            )

        else:
            raise ValueError(f"Unknown database type: {db_type}")

    except Exception as e:
        raise RuntimeError(f"Failed to load database config: {e}")


def save_config(config: DatabaseConfig):
    """
    Save database configuration to file.

    Args:
        config: DatabaseConfig object to save
    """
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config.db_type == "sqlite":
        data = {
            "type": "sqlite",
            "path": config.sqlite_path
        }
    elif config.db_type == "postgresql":
        data = {
            "type": "postgresql",
            "connection": {
                "host": config.pg_host,
                "port": config.pg_port,
                "database": config.pg_database,
                "user": config.pg_user,
                "password": config.pg_password
            },
            "pool_size": config.pool_size,
            "max_overflow": config.max_overflow
        }
    else:
        raise ValueError(f"Unknown database type: {config.db_type}")

    with open(config_path, 'w') as f:
        json.dump(data, f, indent=2)


def create_sqlite_config(path: Optional[str] = None) -> DatabaseConfig:
    """
    Create SQLite configuration.

    Args:
        path: Optional path to SQLite database file

    Returns:
        DatabaseConfig for SQLite
    """
    if not path:
        path = str(get_default_sqlite_path())

    return DatabaseConfig(
        db_type="sqlite",
        sqlite_path=os.path.expanduser(path)
    )


def create_postgresql_config(
    host: str = "localhost",
    port: int = 5432,
    database: str = "promaia",
    user: str = "promaia",
    password: str = "",
    pool_size: int = 5,
    max_overflow: int = 10
) -> DatabaseConfig:
    """
    Create PostgreSQL configuration.

    Args:
        host: PostgreSQL host
        port: PostgreSQL port
        database: Database name
        user: Database user
        password: Database password
        pool_size: Connection pool size
        max_overflow: Max overflow connections

    Returns:
        DatabaseConfig for PostgreSQL
    """
    return DatabaseConfig(
        db_type="postgresql",
        pg_host=host,
        pg_port=port,
        pg_database=database,
        pg_user=user,
        pg_password=password,
        pool_size=pool_size,
        max_overflow=max_overflow
    )
