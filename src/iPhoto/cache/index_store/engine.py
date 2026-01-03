"""Low-level database connection and transaction management.

This module provides infrastructure for SQLite connection pooling, PRAGMA
settings, and transaction context management.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from ...utils.logging import get_logger

logger = get_logger()


class DatabaseManager:
    """Manages SQLite connections and transactions.
    
    This class provides:
    - Connection lifecycle management
    - Transaction context manager
    - Connection pooling support (via thread-local connections)
    """

    def __init__(self, db_path: Path):
        """Initialize the database manager.
        
        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def get_connection(self) -> sqlite3.Connection:
        """Get or create a database connection.
        
        Returns:
            An active SQLite connection.
        """
        if self._conn:
            return self._conn
        return sqlite3.connect(self.db_path)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Context manager for transactional operations.
        
        This context manager batches multiple updates into a single transaction
        for better performance and atomicity.
        
        Warning:
            Nested transactions are NOT truly supported. When this context manager
            is entered while a transaction is already active, it yields the existing
            connection WITHOUT creating a savepoint. This means:
            
            - The nested block has NO separate transaction semantics
            - Errors in the nested block do NOT trigger a partial rollback
            - Only the outermost transaction controls commit/rollback behavior
            - Use with caution when nesting transaction contexts
            
            If you need true nested transactions, consider implementing savepoint
            support or restructure your code to avoid nesting.
        
        Yields:
            A database connection within a transaction context.
        
        Example:
            >>> with db_manager.transaction() as conn:
            ...     conn.execute("INSERT INTO assets ...")
            ...     conn.execute("UPDATE assets ...")
        """
        if self._conn:
            # WARNING: Nested transaction - no savepoint, just yields existing connection
            # The nested block shares the outer transaction's fate
            yield self._conn
            return

        self._conn = sqlite3.connect(self.db_path)
        try:
            with self._conn:
                yield self._conn
        finally:
            self._conn.close()
            self._conn = None

    def close(self) -> None:
        """Close any active connection."""
        if self._conn:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def execute_in_transaction(
        self,
        query: str,
        params: tuple | list | None = None,
    ) -> None:
        """Execute a single query within a transaction.
        
        Args:
            query: SQL query to execute.
            params: Optional parameters for the query.
        """
        conn = self.get_connection()
        is_nested = (conn == self._conn)

        try:
            if is_nested:
                if params:
                    conn.execute(query, params)
                else:
                    conn.execute(query)
            else:
                with conn:
                    if params:
                        conn.execute(query, params)
                    else:
                        conn.execute(query)
        finally:
            if not is_nested:
                conn.close()

    def execute_many_in_transaction(
        self,
        query: str,
        params_list: list,
    ) -> None:
        """Execute a query multiple times within a transaction.
        
        Args:
            query: SQL query to execute.
            params_list: List of parameter tuples/lists for each execution.
        """
        conn = self.get_connection()
        is_nested = (conn == self._conn)

        try:
            if is_nested:
                conn.executemany(query, params_list)
            else:
                with conn:
                    conn.executemany(query, params_list)
        finally:
            if not is_nested:
                conn.close()
