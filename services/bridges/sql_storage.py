"""Compatibility layer for the fixed SQL statements in WeChat and IMO.

Not a general SQLite SQL translator. Existing SQLite files are never silently
ignored when PostgreSQL is selected.
"""
import os
import re
import sqlite3


def connect(path):
    dsn = os.getenv("BRIDGE_SQL_DSN") or os.getenv("BRIDGE_STORAGE_DSN")
    if not dsn:
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db
    if path.exists():
        raise RuntimeError("Existing SQLite database requires explicit migration")
    schema = imo_schema(path) if os.getenv('IMO_DATA') and path.name == 'bridge.sqlite3' else None
    return PostgresConnection(dsn, schema=schema)


def imo_schema(path):
    from pathlib import Path
    import hashlib
    relative = Path(path).resolve().parent.relative_to(Path(os.environ['IMO_DATA']).resolve())
    return 'imo_' + hashlib.sha256(str(relative).encode()).hexdigest()[:48]


def archive_imo_schema(path):
    """Retain inactive history without reusing its tables for another login."""
    import secrets
    import psycopg2
    from psycopg2 import sql
    from contextlib import closing
    source = imo_schema(path)
    archive = 'imo_archive_' + secrets.token_hex(16)
    with closing(psycopg2.connect(os.environ['BRIDGE_SQL_DSN'], connect_timeout=5,
                                  options="-c statement_timeout=10000 -c lock_timeout=5000")) as connection:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(
                    sql.Identifier(source), sql.Identifier(archive)))
    return archive


class PostgresConnection:
    def __init__(self, dsn, schema=None):
        import psycopg2
        from psycopg2.extras import DictCursor
        self.connection = psycopg2.connect(
            dsn, connect_timeout=5, cursor_factory=DictCursor,
            options="-c statement_timeout=10000 -c lock_timeout=5000")
        if schema is not None:
            from psycopg2 import sql
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
                    cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
                self.connection.commit()
            except Exception:
                self.connection.close()
                raise

    def execute(self, sql, args=()):
        sql = sql.strip().rstrip(";")
        pragma = re.fullmatch(r"PRAGMA table_info\((\w+)\)", sql)
        if pragma:
            sql = ("SELECT ordinal_position, column_name AS name FROM information_schema.columns "
                   "WHERE table_schema=current_schema() AND table_name=%s")
            args = (pragma.group(1),)
        else:
            if sql.startswith("INSERT OR IGNORE INTO "):
                sql = sql.replace("INSERT OR IGNORE INTO ", "INSERT INTO ", 1)
                sql += " ON CONFLICT DO NOTHING"
            elif sql.startswith("INSERT OR REPLACE INTO "):
                replacements = {
                    "INSERT OR REPLACE INTO cursors VALUES(?,?,?)":
                        "INSERT INTO cursors VALUES(?,?,?) ON CONFLICT(slot,cid) DO UPDATE SET value=excluded.value",
                    "INSERT OR REPLACE INTO meta VALUES(?,?)":
                        "INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    "INSERT OR REPLACE INTO users VALUES(?,?)":
                        "INSERT INTO users VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET name=excluded.name",
                }
                sql = replacements[sql]
            sql = sql.replace("substr(buid,-1)", "right(buid,1)")
            sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)
            sql = sql.replace("?", "%s")
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, args)
            return cursor
        except Exception:
            cursor.close()
            self.connection.rollback()
            raise

    def executescript(self, script):
        # Current schemas contain a quoted semicolon in the IMO group test.
        statements = re.split(r";(?=(?:[^']*'[^']*')*[^']*$)", script)
        for statement in statements:
            statement = statement.strip()
            if statement and not statement.startswith("PRAGMA "):
                self.execute(statement).close()

    def executemany(self, sql, rows):
        for args in rows:
            self.execute(sql, args).close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.connection.rollback()
        return False

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()
