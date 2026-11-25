"""Shared PostgreSQL storage for bridge-owned JSON documents.

Each bridge must use its own database credentials. No implicit SQLite
migration or fallback is performed.
"""
import json
from contextlib import contextmanager, closing

import psycopg2
from psycopg2.extras import Json


class ConflictError(RuntimeError):
    """The document was changed by another writer."""


class DocumentStore:
    def __init__(self, dsn):
        if not dsn:
            raise ValueError("A bridge-specific PostgreSQL DSN is required")
        self.dsn = dsn

    @contextmanager
    def transaction(self):
        with closing(psycopg2.connect(
            self.dsn, connect_timeout=5,
            options="-c statement_timeout=10000 -c lock_timeout=5000"
        )) as connection:
            with connection:
                with connection.cursor() as cursor:
                    yield cursor

    def initialize(self):
        with self.transaction() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bridge_documents (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value JSONB NOT NULL,
                    revision BIGINT NOT NULL DEFAULT 1,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (namespace, key)
                )
            """)

    def read(self, namespace, key):
        """Return (value, revision), or (None, 0) for a missing document."""
        with self.transaction() as cursor:
            cursor.execute(
                "SELECT value, revision FROM bridge_documents "
                "WHERE namespace=%s AND key=%s", (namespace, key)
            )
            return cursor.fetchone() or (None, 0)

    def write(self, namespace, key, value, expected_revision):
        """Compare-and-swap; revision zero means create only."""
        payload = Json(value, dumps=lambda obj: json.dumps(obj, allow_nan=False))
        with self.transaction() as cursor:
            if expected_revision == 0:
                cursor.execute("""
                    INSERT INTO bridge_documents(namespace,key,value)
                    VALUES (%s,%s,%s) ON CONFLICT DO NOTHING
                    RETURNING revision
                """, (namespace, key, payload))
            else:
                cursor.execute("""
                    UPDATE bridge_documents
                    SET value=%s, revision=revision+1, updated_at=now()
                    WHERE namespace=%s AND key=%s AND revision=%s
                    RETURNING revision
                """, (payload, namespace, key, expected_revision))
            row = cursor.fetchone()
            if row is None:
                raise ConflictError("Document revision conflict")
            return row[0]


class AccountRegistry:
    """Opt-in PostgreSQL registry with one-time import of a legacy JSON file."""
    def __init__(self, path, dsn):
        self.path = path
        self.store = DocumentStore(dsn)
        self.store.initialize()
        value, self.revision = self.store.read("registry", "accounts")
        if self.revision == 0:
            value = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(value, dict):
                raise ValueError("Invalid legacy account registry")
            try:
                self.revision = self.store.write("registry", "accounts", value, 0)
            except ConflictError:
                value, self.revision = self.store.read("registry", "accounts")
        if not isinstance(value, dict):
            raise ValueError("Invalid PostgreSQL account registry")
        self.value = value

    def save(self, value):
        self.revision = self.store.write(
            "registry", "accounts", value, self.revision)
