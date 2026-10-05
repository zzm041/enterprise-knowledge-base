import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
 id INTEGER PRIMARY KEY, tenant TEXT NOT NULL, workspace TEXT NOT NULL,
 title TEXT NOT NULL, audience TEXT NOT NULL CHECK(audience IN ('all','internal')),
 status TEXT NOT NULL CHECK(status IN ('DRAFT','ACTIVE','ARCHIVED')),
 latest_version INTEGER NOT NULL, current_version INTEGER, epoch INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS versions (
 document_id INTEGER NOT NULL REFERENCES documents(id), version INTEGER NOT NULL,
 source TEXT NOT NULL, content_hash TEXT NOT NULL, status TEXT NOT NULL,
 attempt INTEGER NOT NULL DEFAULT 0, started REAL, fingerprint TEXT,
 error_code TEXT, PRIMARY KEY(document_id,version)
);
CREATE TABLE IF NOT EXISTS chunks (
 id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL, version INTEGER NOT NULL,
 ordinal INTEGER NOT NULL, text TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
 vector TEXT, UNIQUE(document_id,version,ordinal),
 FOREIGN KEY(document_id,version) REFERENCES versions(document_id,version)
);
CREATE INDEX IF NOT EXISTS doc_scope ON documents(tenant,workspace,status);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
 document_id INTEGER NOT NULL, request_id TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connection(self, write=False):
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except BaseException:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()
