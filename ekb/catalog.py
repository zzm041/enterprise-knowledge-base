import json
import math
import re
import time
from pathlib import Path

from ekb.auth import Principal
from ekb.errors import KBError
from ekb.store import Store
from ekb.text import chunks, digest, normalize


class Catalog:
    def __init__(self, path: Path):
        self.store = Store(path)
        self.path = self.store.path

    @staticmethod
    def _audit(conn, actor, action, document_id, request_id):
        conn.execute(
            "INSERT INTO audit(tenant,actor,action,document_id,request_id) VALUES(?,?,?,?,?)",
            (actor.tenant, actor.subject, action, document_id, request_id),
        )

    @staticmethod
    def _document(conn, actor, document_id, edit=False):
        d = conn.execute(
            "SELECT * FROM documents WHERE id=? AND tenant=?",
            (document_id, actor.tenant),
        ).fetchone()
        if (
            d is None
            or d["workspace"] not in actor.workspaces
            or d["audience"] not in actor.audiences
        ):
            raise KBError("KB.NOT_FOUND", 404)
        actor.require(d["workspace"], edit)
        return dict(d)

    def create(
        self,
        actor: Principal,
        workspace: str,
        title: str,
        text: str,
        audience: str = "all",
        filename: str = "source.md",
        request_id: str = "local",
    ) -> dict:
        actor.require(workspace, True)
        if audience not in ("all", "internal") or audience not in actor.audiences:
            raise KBError("KB.FORBIDDEN", 403)
        if (
            not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", workspace)
            or not title.strip()
            or len(title) > 200
        ):
            raise KBError("KB.INVALID_INPUT", 422)
        source = normalize(filename, text)
        with self.store.connection(True) as c:
            did = c.execute(
                "INSERT INTO documents(tenant,workspace,title,audience,status,latest_version) VALUES(?,?,?,?,'DRAFT',1)",
                (actor.tenant, workspace, title.strip(), audience),
            ).lastrowid
            c.execute(
                "INSERT INTO versions(document_id,version,source,content_hash,status) VALUES(?,1,?,?,'PENDING')",
                (did, source, digest(source)),
            )
            self._audit(c, actor, "created", did, request_id)
        return self.detail(actor, did)

    def detail(self, actor, document_id):
        with self.store.connection() as c:
            d = self._document(c, actor, document_id)
            d["versions"] = [
                dict(r)
                for r in c.execute(
                    "SELECT version,content_hash,status,attempt,error_code,fingerprint FROM versions WHERE document_id=? ORDER BY version",
                    (document_id,),
                )
            ]
            return d

    def list_documents(self, actor, workspace):
        actor.require(workspace)
        with self.store.connection() as c:
            rows = c.execute(
                "SELECT * FROM documents WHERE tenant=? AND workspace=? ORDER BY id LIMIT 500",
                (actor.tenant, workspace),
            )
            return [dict(r) for r in rows if r["audience"] in actor.audiences]

    def add_version(
        self,
        actor,
        document_id,
        text,
        expected_version,
        filename="source.md",
        request_id="local",
    ):
        source = normalize(filename, text)
        with self.store.connection(True) as c:
            d = self._document(c, actor, document_id, True)
            if d["latest_version"] != expected_version:
                raise KBError("KB.CONFLICT", 409)
            version = expected_version + 1
            c.execute(
                "INSERT INTO versions(document_id,version,source,content_hash,status) VALUES(?,?,?,?,'PENDING')",
                (document_id, version, source, digest(source)),
            )
            c.execute(
                "UPDATE documents SET latest_version=?,epoch=epoch+1,status=CASE WHEN status='ARCHIVED' THEN 'DRAFT' ELSE status END WHERE id=?",
                (version, document_id),
            )
            self._audit(c, actor, "version_created", document_id, request_id)
        return self.detail(actor, document_id)

    def archive(self, actor, document_id, expected_version, request_id="local"):
        with self.store.connection(True) as c:
            d = self._document(c, actor, document_id, True)
            if d["latest_version"] != expected_version:
                raise KBError("KB.CONFLICT", 409)
            if d["status"] != "ARCHIVED":
                c.execute(
                    "UPDATE documents SET status='ARCHIVED',epoch=epoch+1 WHERE id=?",
                    (document_id,),
                )
                self._audit(c, actor, "archived", document_id, request_id)
        return self.detail(actor, document_id)

    def ingest(self, actor, document_id, version, embedder=None, request_id="local"):
        with self.store.connection(True) as c:
            d = self._document(c, actor, document_id, True)
            v = c.execute(
                "SELECT * FROM versions WHERE document_id=? AND version=?",
                (document_id, version),
            ).fetchone()
            if v is None:
                raise KBError("KB.NOT_FOUND", 404)
            if d["status"] == "ARCHIVED" or version != d["latest_version"]:
                raise KBError("KB.CONFLICT", 409)
            if v["status"] == "ACTIVE":
                return {"status": "ACTIVE", "version": version}
            if v["status"] == "PROCESSING" and time.time() - v["started"] < 120:
                raise KBError("KB.INGEST_BUSY", 409)
            attempt, epoch = v["attempt"] + 1, d["epoch"]
            c.execute(
                "UPDATE versions SET status='PROCESSING',attempt=?,started=?,error_code=NULL WHERE document_id=? AND version=?",
                (attempt, time.time(), document_id, version),
            )
            source = v["source"]
        try:
            pieces = chunks(source)
            if len(pieces) > 5000:
                raise KBError("KB.CORPUS_LIMIT", 409)
            vectors = []
            for start in range(0, len(pieces), 8):
                batch = pieces[start : start + 8]
                values = (
                    embedder.embed([piece["text"] for piece in batch])
                    if embedder
                    else [None] * len(batch)
                )
                if len(values) != len(batch):
                    raise KBError("KB.INVALID_PROVIDER_OUTPUT", 502)
                vectors.extend(values)
            if len(vectors) != len(pieces):
                raise KBError("KB.INVALID_PROVIDER_OUTPUT", 502)
            if embedder and (
                not vectors
                or not vectors[0]
                or any(
                    len(v) != len(vectors[0])
                    or any(
                        not isinstance(x, (int, float))
                        or isinstance(x, bool)
                        or not math.isfinite(x)
                        for x in v
                    )
                    or not any(v)
                    for v in vectors
                )
            ):
                raise KBError("KB.INVALID_PROVIDER_OUTPUT", 502)
            with self.store.connection(True) as c:
                current = self._document(c, actor, document_id, True)
                row = c.execute(
                    "SELECT attempt FROM versions WHERE document_id=? AND version=?",
                    (document_id, version),
                ).fetchone()
                if (
                    current["epoch"] != epoch
                    or current["latest_version"] != version
                    or current["status"] == "ARCHIVED"
                    or row["attempt"] != attempt
                ):
                    raise KBError("KB.CONFLICT", 409)
                active_count = c.execute(
                    "SELECT count(*) FROM chunks c JOIN documents d ON d.id=c.document_id "
                    "WHERE d.tenant=? AND d.workspace=? AND d.status='ACTIVE' "
                    "AND c.version=d.current_version AND d.id<>?",
                    (current["tenant"], current["workspace"], document_id),
                ).fetchone()[0]
                if active_count + len(pieces) > 5000:
                    raise KBError("KB.CORPUS_LIMIT", 409)
                c.execute(
                    "DELETE FROM chunks WHERE document_id=? AND version=?",
                    (document_id, version),
                )
                for i, (p, vector) in enumerate(zip(pieces, vectors, strict=True), 1):
                    c.execute(
                        "INSERT INTO chunks(document_id,version,ordinal,text,start,end,vector) VALUES(?,?,?,?,?,?,?)",
                        (
                            document_id,
                            version,
                            i,
                            p["text"],
                            p["start"],
                            p["end"],
                            json.dumps(vector) if vector is not None else None,
                        ),
                    )
                c.execute(
                    "UPDATE versions SET status='SUPERSEDED' WHERE document_id=? AND status='ACTIVE'",
                    (document_id,),
                )
                c.execute(
                    "UPDATE versions SET status='ACTIVE',fingerprint=?,error_code=NULL WHERE document_id=? AND version=?",
                    (embedder.fingerprint if embedder else None, document_id, version),
                )
                c.execute(
                    "UPDATE documents SET current_version=?,status='ACTIVE' WHERE id=?",
                    (version, document_id),
                )
                self._audit(c, actor, "published", document_id, request_id)
        except Exception as exc:
            code = exc.code if isinstance(exc, KBError) else "KB.INGEST_FAILED"
            with self.store.connection(True) as c:
                c.execute(
                    "UPDATE versions SET status='FAILED',error_code=? WHERE document_id=? AND version=? AND attempt=? AND status='PROCESSING'",
                    (code, document_id, version, attempt),
                )
                self._audit(c, actor, "ingest_failed", document_id, request_id)
            if isinstance(exc, KBError):
                raise
            raise KBError(code, 503) from None
        return {"status": "ACTIVE", "version": version}

    def eligible(self, actor, workspace):
        actor.require(workspace)
        audiences = tuple(x for x in actor.audiences if x in ("all", "internal"))
        if not audiences:
            return []
        marks = ",".join("?" for _ in audiences)
        with self.store.connection() as c:
            rows = c.execute(
                f"""
                SELECT c.*,d.title,d.tenant,d.workspace,d.audience,v.fingerprint
                FROM chunks c JOIN documents d ON d.id=c.document_id
                JOIN versions v ON v.document_id=c.document_id AND v.version=c.version
                WHERE d.tenant=? AND d.workspace=? AND d.audience IN ({marks})
                AND d.status='ACTIVE' AND c.version=d.current_version AND v.status='ACTIVE'
                ORDER BY c.id
                """,
                (actor.tenant, workspace, *audiences),
            ).fetchall()
        if len(rows) > 5000:
            raise KBError("KB.CORPUS_LIMIT", 409)
        return [dict(row) for row in rows]
