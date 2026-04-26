# src/ots_shared/history.py

"""Command history tracking for OTS CLI tools.

Logs mutating commands to a SQLite database in the current directory
(the environment directory). The database can be serialized to SQL
for git-friendly diffing.

Usage::

    from ots_shared.history import log_command, serialize_to_sql

    log_command(
        "lots", "cloudinit generate", {"role": "db", "hostname": "eu-db-01"}
    )
    serialize_to_sql()  # writes history.sql alongside history.db
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

_DB_NAME = "history.db"
_SQL_NAME = "history.sql"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS command_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    tool        TEXT NOT NULL,
    command     TEXT NOT NULL,
    args_json   TEXT,
    cwd         TEXT NOT NULL,
    result      TEXT
);
"""


def _get_db(directory: Path | None = None) -> sqlite3.Connection:
    """Open (or create) the history database in *directory*."""
    base = directory or Path.cwd()
    db_path = base / _DB_NAME
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def log_command(
    tool: str,
    command: str,
    args: dict | None = None,
    result: str | None = None,
    directory: Path | None = None,
) -> int:
    """Record a command execution. Returns the row id."""
    conn = _get_db(directory)
    try:
        cur = conn.execute(
            """INSERT INTO command_log (timestamp, tool, command, args_json, cwd, result)
               VALUES (?, ?, ?, ?, ?, ?)""",  # noqa: E501
            (
                datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                tool,
                command,
                json.dumps(args) if args else None,
                str(directory or Path.cwd()),
                result,
            ),
        )
        conn.commit()
        assert cur.lastrowid is not None  # INSERT always sets it
        return cur.lastrowid
    finally:
        conn.close()


def serialize_to_sql(directory: Path | None = None) -> Path:
    """Dump the history database to a SQL file for git commit.

    Returns the path to the generated .sql file.
    """
    base = directory or Path.cwd()
    conn = _get_db(directory)
    try:
        rows = conn.execute("SELECT * FROM command_log ORDER BY id").fetchall()

        lines = [
            "-- Command history (auto-generated, do not edit)",
            (
                "-- Regenerate: python -c 'from ots_shared.history import "
                "serialize_to_sql; serialize_to_sql()'"
            ),
            "",
        ]

        for row in rows:
            args_val = f"'{_sql_escape(row['args_json'])}'" if row["args_json"] else "NULL"
            result_val = f"'{_sql_escape(row['result'])}'" if row["result"] else "NULL"
            lines.append(
                f"INSERT INTO command_log "
                f"(id, timestamp, tool, command, args_json, cwd, result) VALUES "
                f"({row['id']}, '{row['timestamp']}', "
                f"'{_sql_escape(row['tool'])}', "
                f"'{_sql_escape(row['command'])}', {args_val}, "
                f"'{_sql_escape(row['cwd'])}', {result_val});"
            )

        sql_path = base / _SQL_NAME
        sql_path.write_text("\n".join(lines) + "\n")
        return sql_path
    finally:
        conn.close()


def _sql_escape(s: str) -> str:
    """Escape single quotes for SQL literals."""
    return s.replace("'", "''")
