"""原件存档（演示层自建表，不碰产品库）。

产品库（ekb）存的是「正文 → 版本 → 分块」，没有「当初上传的是哪个文件」这个概念。
审计与回溯需要原件：谁传的、多大、sha256 是多少、解析出多少字、落到了哪个文档。
这些放在本次运行目录的 files.db 里，原件落在 uploads/ 下，按 sha256 命名——
同一份文件传两次不会存两份，也不会再建一篇重复文档。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
    sha256      TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    chars       INTEGER NOT NULL DEFAULT 0,
    pages       INTEGER NOT NULL DEFAULT 0,
    tables      INTEGER NOT NULL DEFAULT 0,
    title       TEXT,
    document_id INTEGER,
    workspace   TEXT NOT NULL DEFAULT 'handbook',
    source      TEXT NOT NULL DEFAULT 'upload',
    status      TEXT NOT NULL DEFAULT 'imported',
    note        TEXT,
    warnings    TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_document ON files(document_id);

CREATE TABLE IF NOT EXISTS runs(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    workspace   TEXT NOT NULL DEFAULT 'handbook',
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    fetched     INTEGER NOT NULL DEFAULT 0,
    created     INTEGER NOT NULL DEFAULT 0,
    updated     INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS sources(
    url          TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    category     TEXT NOT NULL DEFAULT '',
    workspace    TEXT NOT NULL DEFAULT 'handbook',
    audience     TEXT NOT NULL DEFAULT 'all',
    kind         TEXT NOT NULL DEFAULT 'file',
    document_id  INTEGER,
    last_sha     TEXT,
    last_run     TEXT,
    last_status  TEXT NOT NULL DEFAULT 'new',
    note         TEXT
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class FileStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.uploads = self.root / "uploads"
        self.uploads.mkdir(exist_ok=True)
        self.db = self.root / "files.db"
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        # 给早期建好的 files.db 补 workspace 列（老数据按企业库归属）。
        for table in ("files", "runs"):
            cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "workspace" not in cols:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN workspace TEXT NOT NULL DEFAULT 'handbook'"
                )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def sha(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def blob_path(self, sha256: str, filename: str) -> Path:
        return self.uploads / (sha256 + (Path(filename).suffix.lower() or ".bin"))

    def find(self, sha256: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM files WHERE sha256=?", (sha256,)).fetchone()
        return dict(row) if row else None

    def by_document(self, document_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM files WHERE document_id=? ORDER BY created_at DESC LIMIT 1",
                (document_id,),
            ).fetchone()
        return dict(row) if row else None

    def save(
        self,
        *,
        filename: str,
        data: bytes,
        kind: str,
        title: str,
        chars: int,
        pages: int = 0,
        tables: int = 0,
        warnings: list[str] | None = None,
        document_id: int | None = None,
        workspace: str = "handbook",
        source: str = "upload",
        status: str = "imported",
        note: str = "",
    ) -> dict:
        sha = self.sha(data)
        path = self.blob_path(sha, filename)
        if not path.exists():
            path.write_bytes(data)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO files(sha256,filename,kind,size,chars,pages,tables,title,"
                "document_id,workspace,source,status,note,warnings,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(sha256) DO UPDATE SET document_id=COALESCE(excluded.document_id,files.document_id),"
                "workspace=excluded.workspace,status=excluded.status,note=excluded.note,"
                "chars=excluded.chars,warnings=excluded.warnings",
                (
                    sha, filename, kind, len(data), chars, pages, tables, title,
                    document_id, workspace, source, status, note,
                    json.dumps(warnings or [], ensure_ascii=False), now(),
                ),
            )
        return self.find(sha) or {}

    def link(self, sha256: str, document_id: int, status: str = "indexed", note: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE files SET document_id=?,status=?,note=? WHERE sha256=?",
                (document_id, status, note, sha256),
            )

    def list(self, limit: int = 200, workspace: str | None = None) -> list[dict]:
        # workspace=None 表示不过滤（运维视角看全部）；页面按当前库过滤。
        sql = "SELECT * FROM files"
        args: list = []
        if workspace:
            sql += " WHERE workspace=?"
            args.append(workspace)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 500)))
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["warnings"] = json.loads(item.get("warnings") or "[]")
            out.append(item)
        return out

    def forget_document(self, document_id: int) -> int:
        """文档被物理删除时，把台账里的指针断掉（原件保留，sha 还在）。"""
        with self._conn() as conn:
            return conn.execute(
                "UPDATE files SET document_id=NULL,status='orphaned' WHERE document_id=?",
                (document_id,),
            ).rowcount

    def stats(self, workspace: str | None = None) -> dict:
        where = " WHERE workspace=?" if workspace else ""
        tail = " AND" if where else " WHERE"
        args = (workspace,) if workspace else ()
        with self._conn() as conn:
            files = conn.execute(
                "SELECT COUNT(*) c, COALESCE(SUM(size),0) s FROM files" + where, args).fetchone()
            linked = conn.execute(
                "SELECT COUNT(*) c FROM files" + where + tail + " document_id IS NOT NULL", args).fetchone()
            failed = conn.execute(
                "SELECT COUNT(*) c FROM files" + where + tail + " status LIKE 'fail%'", args).fetchone()
        return {
            "files": files["c"],
            "bytes": files["s"],
            "linked": linked["c"],
            "failed": failed["c"],
        }

    def start_run(self, source: str, workspace: str = "handbook") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO runs(source,workspace,started_at) VALUES(?,?,?)",
                (source, workspace, now()),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, fetched: int, created: int, updated: int,
                   skipped: int, failed: int, note: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE runs SET finished_at=?,fetched=?,created=?,updated=?,skipped=?,failed=?,note=? "
                "WHERE id=?",
                (now(), fetched, created, updated, skipped, failed, note, run_id),
            )

    def runs(self, limit: int = 20, workspace: str | None = None) -> list[dict]:
        sql = "SELECT * FROM runs"
        args: list = []
        if workspace:
            sql += " WHERE workspace=?"
            args.append(workspace)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(limit, 100)))
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    # ---- 采集来源台账 -------------------------------------------------
    # 一个 URL / 一份本地文件只对应一篇文档：靠 url 这一列保证幂等，
    # last_sha 是上次抓回来的内容指纹，指纹不变就不升版本。

    def source(self, url: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM sources WHERE url=?", (url,)).fetchone()
        return dict(row) if row else None

    def touch_source(self, url: str, *, name: str = "", category: str = "",
                     workspace: str = "", audience: str = "", kind: str = "",
                     document_id: int | None = None, sha: str = "",
                     status: str = "", note: str = "") -> dict:
        existing = self.source(url) or {}
        merged = {
            "name": name or existing.get("name") or url,
            "category": category or existing.get("category") or "",
            "workspace": workspace or existing.get("workspace") or "handbook",
            "audience": audience or existing.get("audience") or "all",
            "kind": kind or existing.get("kind") or "file",
            "document_id": document_id if document_id is not None else existing.get("document_id"),
            "last_sha": sha or existing.get("last_sha"),
            "last_run": now(),
            "last_status": status or existing.get("last_status") or "new",
            "note": note or existing.get("note") or "",
        }
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sources(url,name,category,workspace,audience,kind,document_id,"
                "last_sha,last_run,last_status,note) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET name=excluded.name,category=excluded.category,"
                "workspace=excluded.workspace,audience=excluded.audience,kind=excluded.kind,"
                "document_id=excluded.document_id,last_sha=excluded.last_sha,"
                "last_run=excluded.last_run,last_status=excluded.last_status,note=excluded.note",
                (url, merged["name"], merged["category"], merged["workspace"],
                 merged["audience"], merged["kind"], merged["document_id"],
                 merged["last_sha"], merged["last_run"], merged["last_status"], merged["note"]),
            )
        return self.source(url) or {}

    def sources(self, limit: int = 100) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sources ORDER BY last_run DESC LIMIT ?", (max(1, min(limit, 300)),)
            ).fetchall()
        return [dict(row) for row in rows]
