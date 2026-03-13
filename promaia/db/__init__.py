"""
Database abstraction layer for Promaia.

Supports both SQLite (development) and PostgreSQL (production) with a unified interface.

Usage:
    from promaia.db import get_connection

    # Works with both SQLite and PostgreSQL
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks")
        results = cursor.fetchall()
"""

import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional, Any, Generator
import logging

from .config import load_config, DatabaseConfig

logger = logging.getLogger(__name__)

# Global configuration
_config: Optional[DatabaseConfig] = None
_config_lock = threading.Lock()

# PostgreSQL connection pool (lazy-loaded)
_pg_pool: Optional[Any] = None
_pg_pool_lock = threading.Lock()


def get_config() -> DatabaseConfig:
    """
    Get database configuration (thread-safe).

    Returns:
        DatabaseConfig object
    """
    global _config

    if _config is None:
        with _config_lock:
            if _config is None:  # Double-check locking
                _config = load_config()
                logger.info(f"Loaded database config: {_config.db_type}")

    return _config


def reload_config():
    """
    Reload database configuration from file.

    Useful after changing db_config.json.
    """
    global _config, _pg_pool

    with _config_lock:
        _config = None
        _config = load_config()

    # Close existing pool if switching databases
    with _pg_pool_lock:
        if _pg_pool is not None:
            try:
                _pg_pool.closeall()
            except Exception as e:
                logger.warning(f"Error closing connection pool: {e}")
            _pg_pool = None


def _get_pg_pool():
    """
    Get PostgreSQL connection pool (lazy-loaded, thread-safe).

    Returns:
        psycopg2 connection pool
    """
    global _pg_pool

    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:  # Double-check locking
                try:
                    import psycopg2
                    from psycopg2 import pool
                except ImportError:
                    raise RuntimeError(
                        "psycopg2 not installed. Install with: pip install psycopg2-binary"
                    )

                config = get_config()

                if config.db_type != "postgresql":
                    raise RuntimeError("PostgreSQL pool requested but config is not PostgreSQL")

                logger.info("Creating PostgreSQL connection pool")
                _pg_pool = pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=config.pool_size + config.max_overflow,
                    host=config.pg_host,
                    port=config.pg_port,
                    database=config.pg_database,
                    user=config.pg_user,
                    password=config.pg_password
                )
                logger.info("PostgreSQL connection pool created")

    return _pg_pool


@contextmanager
def get_connection() -> Generator[Any, None, None]:
    """
    Get database connection (context manager).

    Returns a connection that works with both SQLite and PostgreSQL.
    Automatically commits on success, rolls back on error.

    Usage:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks")
            results = cursor.fetchall()

    Yields:
        Database connection object
    """
    config = get_config()

    if config.db_type == "sqlite":
        # SQLite connection
        conn = sqlite3.connect(config.sqlite_path)
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    elif config.db_type == "postgresql":
        # PostgreSQL connection from pool
        pool = _get_pg_pool()
        conn = pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            pool.putconn(conn)

    else:
        raise ValueError(f"Unsupported database type: {config.db_type}")


def execute_script(script: str, params: Optional[dict] = None):
    """
    Execute a SQL script (multiple statements).

    Args:
        script: SQL script with multiple statements
        params: Optional parameters for parameterized queries

    Note: Commits after each statement.
    """
    config = get_config()

    with get_connection() as conn:
        cursor = conn.cursor()

        if config.db_type == "sqlite":
            # SQLite supports executescript
            if params:
                logger.warning("Parameters ignored for SQLite executescript")
            cursor.executescript(script)

        elif config.db_type == "postgresql":
            # PostgreSQL: split and execute statements
            statements = [s.strip() for s in script.split(';') if s.strip()]
            for statement in statements:
                if params:
                    cursor.execute(statement, params)
                else:
                    cursor.execute(statement)

        conn.commit()


def test_connection() -> bool:
    """
    Test database connection.

    Returns:
        True if connection successful, False otherwise
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        return False


def get_db_type() -> str:
    """
    Get current database type.

    Returns:
        "sqlite" or "postgresql"
    """
    return get_config().db_type


def is_sqlite() -> bool:
    """Check if using SQLite."""
    return get_db_type() == "sqlite"


def is_postgresql() -> bool:
    """Check if using PostgreSQL."""
    return get_db_type() == "postgresql"


__all__ = [
    'get_connection',
    'get_config',
    'reload_config',
    'execute_script',
    'test_connection',
    'get_db_type',
    'is_sqlite',
    'is_postgresql',
]
