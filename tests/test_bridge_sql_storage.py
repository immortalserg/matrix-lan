import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "bridges"))
from sql_storage import PostgresConnection, connect


class SQLStorageTests(unittest.TestCase):
    def setUp(self):
        self.db = PostgresConnection.__new__(PostgresConnection)
        self.db.connection = Mock()
        self.cursor = self.db.connection.cursor.return_value

    def test_ignore(self):
        self.db.execute("INSERT OR IGNORE INTO messages VALUES(?,?,?)", ("a", "b", "c"))
        self.cursor.execute.assert_called_once_with(
            "INSERT INTO messages VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            ("a", "b", "c"))

    def test_replace(self):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("a", "b"))
        self.cursor.execute.assert_called_once_with(
            "INSERT INTO meta VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("a", "b"))

    def test_script_quoted_semicolon(self):
        self.db.executescript("PRAGMA journal_mode=WAL; INSERT OR IGNORE INTO portal_info SELECT buid,CASE WHEN substr(buid,-1)=';' THEN 'group' ELSE 'dm' END FROM portals;")
        self.cursor.execute.assert_called_once_with(
            "INSERT INTO portal_info SELECT buid,CASE WHEN right(buid,1)=';' THEN 'group' ELSE 'dm' END FROM portals ON CONFLICT DO NOTHING", ())

    def test_rollback(self):
        self.cursor.execute.side_effect = RuntimeError("failed")
        with self.assertRaises(RuntimeError):
            self.db.execute("SELECT 1")
        self.db.connection.rollback.assert_called_once()

    def test_existing_sqlite_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge.sqlite3"
            path.touch()
            with patch.dict(os.environ, {"BRIDGE_SQL_DSN": "postgresql://unused"}):
                with self.assertRaisesRegex(RuntimeError, "explicit migration"):
                    connect(path)

    def test_legacy_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"BRIDGE_SQL_DSN": ""}):
                db = connect(Path(directory) / "bridge.sqlite3")
                try:
                    self.assertEqual(db.execute("SELECT 1 AS n").fetchone()["n"], 1)
                finally:
                    db.close()


if __name__ == "__main__":
    unittest.main()
