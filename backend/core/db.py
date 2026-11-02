"""SQLite storage. One connection shared across threads, guarded by a lock."""

import json
import os
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DB_PATH = Path(os.getenv("TIMBER_DB", DATA_DIR / "timber.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY,
    type   TEXT NOT NULL,
    source TEXT NOT NULL,
    data   TEXT NOT NULL,
    t      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_t ON events (t);

CREATE TABLE IF NOT EXISTS baseline (
    signal TEXT PRIMARY KEY,
    n      INTEGER NOT NULL,
    mean   REAL NOT NULL,
    m2     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id     INTEGER PRIMARY KEY,
    action TEXT NOT NULL,
    agent  TEXT NOT NULL,
    level  TEXT NOT NULL,
    reason TEXT NOT NULL,
    data   TEXT NOT NULL,
    t      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_t ON decisions (t);

CREATE TABLE IF NOT EXISTS episodes (
    id         TEXT PRIMARY KEY,
    start      REAL NOT NULL,
    end        REAL,
    peak_level TEXT NOT NULL,
    peak_score INTEGER NOT NULL,
    trigger    TEXT NOT NULL,
    actions    TEXT NOT NULL
);
"""

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
    return _conn


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def execute(sql: str, params: tuple = ()) -> None:
    with _lock:
        connect().execute(sql, params)


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(row) for row in connect().execute(sql, params)]


def save_event(event) -> None:
    """Bus listener: persist every event."""
    execute(
        "INSERT INTO events (type, source, data, t) VALUES (?, ?, ?, ?)",
        (event.type, event.source, json.dumps(event.data), event.t),
    )
