"""Local SQLite report and conversation history. Source repositories stay read-only."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3

DB_PATH = Path(__file__).resolve().parent / ".runtime" / "history.sqlite3"


@contextmanager
def _connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS scans (
            key TEXT PRIMARY KEY, name TEXT, root TEXT, scan_id TEXT,
            scanned_at TEXT, findings INTEGER, report TEXT NOT NULL,
            messages TEXT NOT NULL DEFAULT '[]')""")
        yield connection
        connection.commit()
    finally:
        connection.close()


def save_scan(report: dict) -> str:
    project = report["project"]
    key = hashlib.sha256((project["root"] + project["scan_id"]).encode()).hexdigest()[:24]
    with _connection() as connection:
        connection.execute("""INSERT INTO scans
            (key,name,root,scan_id,scanned_at,findings,report) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET scanned_at=excluded.scanned_at,
            findings=excluded.findings,report=excluded.report,messages='[]'""",
            (key, project["name"], project["root"], project["scan_id"],
             project["scanned_at"], len(report["findings"]),
             json.dumps(report, ensure_ascii=False)))
        # Keep history bounded because each evidence snapshot includes source text.
        connection.execute("""DELETE FROM scans WHERE key NOT IN
            (SELECT key FROM scans ORDER BY scanned_at DESC LIMIT 10)""")
    return key


def list_scans() -> list[dict]:
    with _connection() as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            "SELECT key,name,root,scan_id,scanned_at,findings FROM scans ORDER BY scanned_at DESC")]


def load_scan(key: str) -> dict | None:
    with _connection() as connection:
        row = connection.execute("SELECT report FROM scans WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None


def save_messages(key: str, messages: list) -> None:
    with _connection() as connection:
        connection.execute("UPDATE scans SET messages=? WHERE key=?",
                           (json.dumps(messages[-40:], ensure_ascii=False), key))


def load_messages(key: str) -> list:
    with _connection() as connection:
        row = connection.execute("SELECT messages FROM scans WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else []
