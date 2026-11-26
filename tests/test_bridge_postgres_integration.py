import os
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "bridges"))
from storage import DocumentStore, ConflictError
from sql_storage import PostgresConnection

DSN = os.getenv("TEST_POSTGRES_DSN")


@unittest.skipUnless(DSN, "TEST_POSTGRES_DSN not configured")
class PostgreSQLIntegration(unittest.TestCase):
    def test_document_revision(self):
        store = DocumentStore(DSN)
        store.initialize()
        key = uuid.uuid4().hex
        try:
            self.assertEqual(store.read("test", key), (None, 0))
            self.assertEqual(store.write("test", key, {"name": "Тест"}, 0), 1)
            self.assertEqual(store.read("test", key), ({"name": "Тест"}, 1))
            with self.assertRaises(ConflictError):
                store.write("test", key, {}, 0)
            self.assertEqual(store.write("test", key, {"ok": True}, 1), 2)
            with self.assertRaises(ConflictError):
                store.write("test", key, {}, 1)
        finally:
            with store.transaction() as cursor:
                cursor.execute("DELETE FROM bridge_documents WHERE namespace=%s AND key=%s", ("test", key))

    def test_relational_adapter(self):
        db = PostgresConnection(DSN)
        try:
            db.executescript("CREATE TEMP TABLE meta(key TEXT PRIMARY KEY,value TEXT); CREATE TEMP TABLE messages(id TEXT PRIMARY KEY, created REAL);")
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("a", "first"))
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("a", "second"))
            db.execute("INSERT OR IGNORE INTO messages VALUES(?,?)", ("m", 1234567890.125))
            db.execute("INSERT OR IGNORE INTO messages VALUES(?,?)", ("m", 0))
            db.commit()
            row = db.execute("SELECT value FROM meta WHERE key=?", ("a",)).fetchone()
            self.assertEqual(row[0], "second")
            self.assertEqual(row["value"], "second")
            self.assertEqual(db.execute("SELECT created FROM messages").fetchone()[0], 1234567890.125)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
