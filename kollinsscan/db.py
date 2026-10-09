"""SQLite storage for login sessions, books and their pages. Page photos are
stored as files next to the database (see app.py)."""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    -- JSON lists: words the spell check should accept (character names...)
    -- and LanguageTool rules switched off for this book.
    dictionary TEXT NOT NULL DEFAULT '[]',
    ignored_rules TEXT NOT NULL DEFAULT '[]',
    -- Running headers seen on this book's pages, so they can be dropped.
    headers TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    seq REAL NOT NULL,          -- reading order
    label TEXT NOT NULL,        -- the printed page number
    html TEXT NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS pages_by_book ON pages (book_id, seq);
"""


class DB:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)

    def query(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def execute(self, sql: str, args: tuple = ()) -> int:
        """Returns the new row id for an INSERT, else the changed row count."""
        with self._lock, self._conn:
            cur = self._conn.execute(sql, args)
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount
