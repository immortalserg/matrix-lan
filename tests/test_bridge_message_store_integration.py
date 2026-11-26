import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "services" / "bridges"))
DSN = os.getenv("TEST_POSTGRES_DSN")


@unittest.skipUnless(DSN, "TEST_POSTGRES_DSN not configured")
class MessageStoreIntegration(unittest.TestCase):
    def test_message_stores(self):
        import psycopg2
        from psycopg2 import sql
        from psycopg2.extensions import make_dsn
        import uuid
        for family in ("social", "threads", "shopping"):
            with self.subTest(family=family):
                schema = "test_" + uuid.uuid4().hex
                admin = psycopg2.connect(DSN)
                admin.autocommit = True
                try:
                    with admin.cursor() as cur:
                        cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                    dsn = make_dsn(DSN, options="-c search_path=" + schema)
                    # Adapter supplies its own timeout options; use a connection
                    # factory patch to retain the isolated test search path.
                    original = psycopg2.connect
                    def isolated(*args, **kwargs):
                        kwargs["options"] = kwargs.get("options", "") + " -c search_path=" + schema
                        return original(*args, **kwargs)
                    spec = importlib.util.spec_from_file_location(
                        "store_" + family, ROOT / "services" / family / "message_store.py")
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"BRIDGE_STORAGE_DSN": dsn, "BRIDGE_SQL_DSN": ""}), patch.object(psycopg2, "connect", side_effect=isolated):
                        path = Path(directory) / "messages.sqlite"
                        store = module.MessageStore(path)
                        try:
                            store.link_chat("slot", "chat", "Chat", "!room:test")
                            message = {"id": "m1", "body": "Привет", "own": False, "timestamp": 1790000000123}
                            store.ingest("slot", "chat", [message], "1")
                            store.ingest("slot", "chat", [message], "2")
                            self.assertEqual(len(store.pending_incoming()), 1)
                            self.assertEqual(store.cursor("slot", "chat"), "2")
                            store.mark_incoming("slot", "chat", "m1", "$event")
                            self.assertEqual(store.pending_incoming(), [])
                        finally:
                            store.close()
                        store = module.MessageStore(path)
                        try:
                            self.assertEqual(store.cursor("slot", "chat"), "2")
                            self.assertEqual(store.pending_incoming(), [])
                            self.assertFalse(path.exists())
                        finally:
                            store.close()
                finally:
                    with admin.cursor() as cur:
                        cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
                    admin.close()
