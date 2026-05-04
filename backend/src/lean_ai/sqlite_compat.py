"""Compatibility layer for SQLite connections in async code.

`aiosqlite` normally uses a worker thread per connection and wakes the
event loop via `call_soon_threadsafe`. In some environments that
cross-thread wakeup path is unavailable or broken, which causes
`await aiosqlite.connect(...)` to hang forever.

When that capability probe fails, this module falls back to a
threadless wrapper around `sqlite3` that exposes the small async API
surface this codebase uses. The fallback blocks the event loop during
database calls, but the operations here are short local SQLite queries
and the behavior is preferable to a total hang.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from threading import Thread
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

SQLITE_ROW_FACTORY = sqlite3.Row

_THREADSAFE_CALLBACKS_SUPPORTED: bool | None = None


class SyncCursor:
    """Minimal async wrapper over `sqlite3.Cursor`."""

    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    async def fetchone(self):
        return self._cursor.fetchone()

    async def fetchall(self):
        return self._cursor.fetchall()

    async def close(self) -> None:
        self._cursor.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class SyncConnection:
    """Threadless async facade over `sqlite3.Connection`."""

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    async def execute(self, sql: str, parameters: Any = None) -> SyncCursor:
        if parameters is None:
            parameters = []
        return SyncCursor(self._connection.execute(sql, parameters))

    async def executemany(self, sql: str, parameters: Any) -> SyncCursor:
        return SyncCursor(self._connection.executemany(sql, parameters))

    async def executescript(self, sql_script: str) -> SyncCursor:
        return SyncCursor(self._connection.executescript(sql_script))

    async def commit(self) -> None:
        self._connection.commit()

    async def rollback(self) -> None:
        self._connection.rollback()

    async def close(self) -> None:
        self._connection.close()

    @property
    def row_factory(self):
        return self._connection.row_factory

    @row_factory.setter
    def row_factory(self, factory) -> None:
        self._connection.row_factory = factory

    @property
    def text_factory(self):
        return self._connection.text_factory

    @text_factory.setter
    def text_factory(self, factory) -> None:
        self._connection.text_factory = factory

    @property
    def total_changes(self) -> int:
        return self._connection.total_changes

    @property
    def in_transaction(self) -> bool:
        return self._connection.in_transaction

    @property
    def isolation_level(self):
        return self._connection.isolation_level

    @isolation_level.setter
    def isolation_level(self, value) -> None:
        self._connection.isolation_level = value

    def interrupt(self) -> None:
        self._connection.interrupt()

    async def create_function(
        self,
        name: str,
        num_params: int,
        func,
        deterministic: bool = False,
    ) -> None:
        self._connection.create_function(
            name,
            num_params,
            func,
            deterministic=deterministic,
        )

    async def set_progress_handler(self, handler, n: int) -> None:
        self._connection.set_progress_handler(handler, n)

    async def set_trace_callback(self, handler) -> None:
        self._connection.set_trace_callback(handler)

    async def set_authorizer(self, authorizer_callback) -> None:
        self._connection.set_authorizer(authorizer_callback)

    async def enable_load_extension(self, value: bool) -> None:
        self._connection.enable_load_extension(value)

    async def load_extension(self, path: str) -> None:
        self._connection.load_extension(path)


SQLiteConnection = aiosqlite.Connection | SyncConnection


def _sync_connect(database: str, **kwargs: Any) -> SyncConnection:
    connection = sqlite3.connect(database, **kwargs)
    return SyncConnection(connection)


async def _detect_threadsafe_callbacks() -> bool:
    """Return whether loop thread wakeups from another thread work."""
    global _THREADSAFE_CALLBACKS_SUPPORTED
    if _THREADSAFE_CALLBACKS_SUPPORTED is not None:
        return _THREADSAFE_CALLBACKS_SUPPORTED

    mode = os.getenv("LEAN_AI_SQLITE_MODE", "").strip().lower()
    if mode == "sync":
        _THREADSAFE_CALLBACKS_SUPPORTED = False
        return False
    if mode == "aiosqlite":
        _THREADSAFE_CALLBACKS_SUPPORTED = True
        return True

    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def _poke_loop() -> None:
        try:
            # Delay slightly so the event loop is already sleeping in select()
            # when the cross-thread wakeup is attempted. That matches the
            # aiosqlite failure mode we want to detect.
            time.sleep(0.05)
            loop.call_soon_threadsafe(future.set_result, True)
        except Exception as exc:
            logger.debug("SQLite wakeup probe failed to schedule result: %s", exc)

    Thread(target=_poke_loop, daemon=True).start()

    try:
        await asyncio.wait_for(future, timeout=0.2)
        _THREADSAFE_CALLBACKS_SUPPORTED = True
    except asyncio.TimeoutError:
        logger.warning(
            "Thread-to-event-loop callbacks appear unavailable; "
            "falling back to synchronous sqlite compatibility mode."
        )
        _THREADSAFE_CALLBACKS_SUPPORTED = False

    return _THREADSAFE_CALLBACKS_SUPPORTED


async def connect(database: str, **kwargs: Any) -> SQLiteConnection:
    """Open a SQLite connection with automatic fallback when needed."""
    if await _detect_threadsafe_callbacks():
        return await aiosqlite.connect(database, **kwargs)
    return _sync_connect(database, **kwargs)
