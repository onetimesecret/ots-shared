# packages/ots-shared/tests/test_history.py

"""Tests for ots_shared.history — command logging and SQL serialization."""

import json
import sqlite3

from ots_shared.history import (
    _DB_NAME,
    _SQL_NAME,
    log_command,
    serialize_to_sql,
)


class TestLogCommand:
    """log_command records entries in the SQLite database."""

    def test_basic_log(self, tmp_path):
        row_id = log_command("lots", "cloudinit generate", directory=tmp_path)
        assert row_id == 1
        assert (tmp_path / _DB_NAME).exists()

    def test_log_with_args(self, tmp_path):
        args = {"role": "db", "hostname": "eu-db-01"}
        log_command("pots", "host add", args=args, directory=tmp_path)

        conn = sqlite3.connect(str(tmp_path / _DB_NAME))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM command_log WHERE id = 1").fetchone()
        conn.close()

        assert row["tool"] == "pots"
        assert row["command"] == "host add"
        assert json.loads(row["args_json"]) == args

    def test_log_with_result(self, tmp_path):
        log_command(
            "lots",
            "hcloud server create",
            result="created eu-db-01",
            directory=tmp_path,
        )

        conn = sqlite3.connect(str(tmp_path / _DB_NAME))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM command_log WHERE id = 1").fetchone()
        conn.close()

        assert row["result"] == "created eu-db-01"

    def test_log_records_timestamp(self, tmp_path):
        log_command("lots", "test", directory=tmp_path)

        conn = sqlite3.connect(str(tmp_path / _DB_NAME))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM command_log WHERE id = 1").fetchone()
        conn.close()

        assert row["timestamp"].endswith("Z")
        assert "T" in row["timestamp"]

    def test_multiple_entries(self, tmp_path):
        log_command("lots", "cmd1", directory=tmp_path)
        log_command("pots", "cmd2", directory=tmp_path)
        row_id = log_command("rots", "cmd3", directory=tmp_path)
        assert row_id == 3


class TestSerializeToSql:
    """serialize_to_sql dumps the database to a text file."""

    def test_creates_sql_file(self, tmp_path):
        log_command("lots", "test", directory=tmp_path)
        sql_path = serialize_to_sql(directory=tmp_path)
        assert sql_path == tmp_path / _SQL_NAME
        assert sql_path.exists()

    def test_sql_contains_insert_statements(self, tmp_path):
        log_command(
            "lots",
            "cloudinit generate",
            args={"role": "db"},
            directory=tmp_path,
        )
        serialize_to_sql(directory=tmp_path)
        content = (tmp_path / _SQL_NAME).read_text()
        assert "INSERT INTO command_log" in content
        assert "lots" in content
        assert "cloudinit generate" in content

    def test_sql_escapes_quotes(self, tmp_path):
        log_command("lots", "test", result="it's done", directory=tmp_path)
        serialize_to_sql(directory=tmp_path)
        content = (tmp_path / _SQL_NAME).read_text()
        assert "it''s done" in content

    def test_empty_database_produces_header_only(self, tmp_path):
        # Create the db without logging anything
        serialize_to_sql(directory=tmp_path)
        content = (tmp_path / _SQL_NAME).read_text()
        assert "INSERT" not in content
        assert "auto-generated" in content

    def test_roundtrip_preserves_order(self, tmp_path):
        log_command("a", "first", directory=tmp_path)
        log_command("b", "second", directory=tmp_path)
        log_command("c", "third", directory=tmp_path)
        serialize_to_sql(directory=tmp_path)
        content = (tmp_path / _SQL_NAME).read_text()
        lines = [line for line in content.splitlines() if line.startswith("INSERT")]
        assert len(lines) == 3
        assert "'first'" in lines[0]
        assert "'third'" in lines[2]
