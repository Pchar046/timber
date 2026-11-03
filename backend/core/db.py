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
CREATE INDEX IF NOT EXISTS episodes_start ON episodes (start);

CREATE TABLE IF NOT EXISTS alerts (
    id      INTEGER PRIMARY KEY,
    episode TEXT,
    level   TEXT NOT NULL,
    sent_to TEXT NOT NULL,
    message TEXT NOT NULL,
    status  TEXT NOT NULL,
    t       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_t ON alerts (t);

CREATE TABLE IF NOT EXISTS spoken (
    id        INTEGER PRIMARY KEY,
    episode   TEXT,
    heard     TEXT NOT NULL,
    said      TEXT NOT NULL,
    sentences TEXT NOT NULL,
    dropped   TEXT NOT NULL,
    status    TEXT NOT NULL,
    t         REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS reflections (
    id       INTEGER PRIMARY KEY,
    day      TEXT NOT NULL,
    summary  TEXT NOT NULL,
    patterns TEXT NOT NULL,
    baseline TEXT NOT NULL,
    status   TEXT NOT NULL,
    ms       INTEGER NOT NULL,
    t        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
    id         INTEGER PRIMARY KEY,
    reflection INTEGER NOT NULL,
    change     TEXT NOT NULL,
    rule       TEXT NOT NULL,
    why        TEXT NOT NULL,
    evidence   TEXT NOT NULL,
    status     TEXT NOT NULL,
    t          REAL NOT NULL,
    decided    REAL
);

CREATE TABLE IF NOT EXISTS memories (
    id      TEXT PRIMARY KEY,
    t       REAL NOT NULL,
    who     TEXT NOT NULL,
    text    TEXT NOT NULL,
    source  TEXT NOT NULL,
    kind    TEXT NOT NULL,
    key     INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    links   TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS memories_t ON memories (t);

CREATE TABLE IF NOT EXISTS activities (
    id          TEXT PRIMARY KEY,
    record      TEXT NOT NULL,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    card_prompt TEXT NOT NULL,
    card        TEXT,
    stage       INTEGER NOT NULL,
    due         REAL NOT NULL,
    created     REAL NOT NULL,
    last        REAL,
    last_result TEXT,
    status      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_nodes (
    id   TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    UNIQUE (type, name)
);
"""

# Columns added after a table first shipped: (table, column, type).
MIGRATIONS = [
    ("episodes", "peaks", "TEXT NOT NULL DEFAULT '{}'"),
    ("episodes", "mood", "TEXT"),
    ("episodes", "helped", "TEXT NOT NULL DEFAULT '[]'"),
    ("episodes", "note", "TEXT"),
    ("episodes", "estimate_source", "TEXT"),
    ("memories", "photo", "TEXT"),
]

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
        for table, column, kind in MIGRATIONS:
            existing = {row["name"] for row in _conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                _conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
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
