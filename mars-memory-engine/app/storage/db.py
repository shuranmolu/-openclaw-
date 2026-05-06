"""
SQLite database connection and schema management for MARS Memory Engine.

Provides connection pooling, schema initialization, and transaction management.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


class Database:
    """SQLite database manager with connection pooling and transaction support."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db_path: Optional[str] = None):
        """Singleton pattern to ensure only one database instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path: Optional[str] = None):
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file. If None, uses default.
        """
        if self._initialized:
            return

        if db_path is None:
            # Default path: memory_store/mars.db
            project_root = Path(__file__).parent.parent.parent
            db_path = project_root / "memory_store" / "mars.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Local thread storage for connections
        self._local = threading.local()

        self._initialized = True

    def get_connection(self) -> sqlite3.Connection:
        """Get a thread-local database connection.

        Returns:
            SQLite connection object.
        """
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            # Ensure parent directory exists
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

            # Use absolute path for SQLite
            db_path_str = str(self.db_path.resolve())

            self._local.conn = sqlite3.connect(
                db_path_str,
                check_same_thread=False,
                isolation_level=None  # Autocommit mode
            )
            # Enable foreign keys
            self._local.conn.execute("PRAGMA foreign_keys = ON")
            # Use WAL mode for better concurrency
            self._local.conn.execute("PRAGMA journal_mode = WAL")
        return self._local.conn

    @contextmanager
    def transaction(self):
        """Context manager for transactions.

        Yields:
            Cursor for executing queries.

        Example:
            with db.transaction() as cur:
                cur.execute("INSERT INTO ...")
        """
        conn = self.get_connection()
        try:
            conn.execute("BEGIN")
            cur = conn.cursor()
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close(self):
        """Close the database connection for current thread."""
        if hasattr(self._local, 'conn') and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

    def initialize_schema(self, schema_path: Optional[str] = None) -> bool:
        """Initialize database schema from SQL file.

        Args:
            schema_path: Path to schema SQL file. If None, uses default.

        Returns:
            True if schema was initialized successfully.
        """
        if schema_path is None:
            schema_path = Path(__file__).parent.parent / "migrations" / "schema.sql"
        else:
            schema_path = Path(schema_path)

        if not schema_path.exists():
            raise FileNotFoundError(f"Schema file not found: {schema_path}")

        with open(schema_path, 'r', encoding='utf-8') as f:
            schema_sql = f.read()

        with self.transaction() as cur:
            cur.executescript(schema_sql)

        return True

    def is_initialized(self) -> bool:
        """Check if database schema has been initialized.

        Returns:
            True if schema_metadata table exists.
        """
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_metadata'")
            return cur.fetchone() is not None
        except sqlite3.Error:
            return False

    def get_schema_version(self) -> Optional[str]:
        """Get the current schema version.

        Returns:
            Schema version string or None if not initialized.
        """
        if not self.is_initialized():
            return None

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM schema_metadata WHERE key = 'schema_version'")
            result = cur.fetchone()
            return result[0] if result else None


# Global database instance
_db: Optional[Database] = None


def get_db(db_path: Optional[str] = None) -> Database:
    """Get the global database instance.

    Args:
        db_path: Optional path to database file.

    Returns:
        Database instance.
    """
    global _db
    if _db is None:
        _db = Database(db_path)
    return _db


def init_db(db_path: Optional[str] = None, force: bool = False) -> Database:
    """Initialize database with schema.

    Args:
        db_path: Optional path to database file.
        force: If True, delete and recreate database with fresh schema.

    Returns:
        Database instance.
    """
    # If force, reset global instance and delete existing database
    if force:
        global _db
        _db = None
        Database._instance = None
        Database._initialized = False

        # Delete existing database file if it exists
        if db_path:
            db_file = Path(db_path)
        else:
            project_root = Path(__file__).parent.parent.parent
            db_file = project_root / "memory_store" / "mars.db"

        if db_file.exists():
            db_file.unlink()

    db = get_db(db_path)

    if force or not db.is_initialized():
        db.initialize_schema()

    return db
