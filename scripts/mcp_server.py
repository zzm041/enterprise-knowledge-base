#!/usr/bin/env python
"""EKB MCP server（只读）——把知识库暴露给 agent，权限跟着身份走。

    ./.venv/Scripts/python.exe scripts/mcp_server.py --data-dir runtime/console-live --identity reader

- 协议：JSON-RPC 2.0 over stdio（MCP spec，支持 2026-07-28 / 2025-11-25 / 2025-06-18 协商）
- 零新依赖：只用标准库 + 项目内 ekb 包，不额外装 SDK
- 只读：不提供任何写入工具；ACL 复用 ekb 的 tenant x workspace x audience x role
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ekb.auth import Principal          # noqa: E402
from ekb.catalog import Catalog         # noqa: E402
from ekb.errors import KBError          # noqa: E402
from ekb.retrieval import bm25          # noqa: E402

SERVER_NAME = "ekb-knowledge"
SERVER_VERSION = "1.0.0"
PROTOCOL_LATEST = "2026-07-28"
PROTOCOL_SUPPORTED = ("2026-07-28", "2025-11-25", "2025-06-18")

TOOLS = [
    {
        "name": "list_workspaces",
        "description": "列出当前身份可见的 workspace、每个库的文档数，以及这些文档的可见范围分布。用来判断“这个 agent 现在能看到什么”。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "search_documents",
        "description": "在指定 workspace 里按词法相关度（BM25）检索片段，返回文档 id、标题、片段序号和字符偏移。只返回当前身份有权查看的 ACTIVE 文档；命中为空就是真的没有，不要据此推断库里没有别的说法。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索词或问题，中文直接写"},
                "workspace": {"type": "string", "description": "库名，默认 handbook"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回条数，默认 8"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "read_document",
        "description": "按文档 id 读回全文（当前有效版本）。返回的字符偏移与检索结果一致，可用于核对引用是否指向原文。",
        "inputSchema": {
            "type": "object",
            "properties": {"document_id": {"type": "integer"}},
            "required": ["document_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "read_chunk",
        "description": "读回某篇文档的单个片段原文，用于把一条检索结果回溯到具体句子。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "integer"},
                "ordinal": {"type": "integer", "description": "片段序号，从 0 开始"},
            },
            "required": ["document_id", "ordinal"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
]


def load_principal(data_dir: Path, identity: str) -> Principal:
    path = data_dir / "accounts.json"
    if not path.exists():
        raise KBError("MCP.NO_ACCOUNTS", 500)
    blob = json.loads(path.read_text(encoding="utf-8"))
    rows = blob.get("accounts") if isinstance(blob, dict) else blob
    for row in rows or []:
        if str(row.get("account", "")).lower() == identity.lower():
            return Principal(
                subject=row["account"],
                tenant=row.get("tenant", "acme"),
                workspaces=row.get("workspaces") or {},
                audiences=tuple(row.get("audiences") or ("all",)),
            )
    raise KBError("MCP.UNKNOWN_IDENTITY", 400)


class Toolbox:
    def __init__(self, data_dir: Path, identity: str):
        self.catalog = Catalog(data_dir / "knowledge.db")
        self.actor = load_principal(data_dir, identity)

    def list_workspaces(self, args):
        out = []
        for workspace in sorted(self.actor.workspaces):
            try:
                docs = self.catalog.list_documents(self.actor, workspace)
            except KBError:
                continue
            audiences = {}
            for d in docs:
                audiences[d["audience"]] = audiences.get(d["audience"], 0) + 1
            out.append({
                "workspace": workspace,
                "role": self.actor.workspaces[workspace],
                "documents": len(docs),
                "audiences": audiences,
            })
        return {"identity": self.actor.subject, "tenant": self.actor.tenant,
                "audiences": list(self.actor.audiences), "workspaces": out}

    def search_documents(self, args):
        query = str(args.get("query") or "").strip()
        if not query:
            raise KBError("MCP.EMPTY_QUERY", 400)
        workspace = str(args.get("workspace") or "handbook")
        limit = int(args.get("limit") or 8)
        rows = self.catalog.eligible(self.actor, workspace)
        if not rows:
            return {"query": query, "workspace": workspace, "hits": [],
                    "note": "当前身份在这个库里没有可检索的片段"}
        scores = bm25(query, [r["text"] for r in rows])
        ranked = sorted(zip(scores, rows), key=lambda pair: pair[0], reverse=True)
        hits = []
        for score, row in ranked:
            if score <= 0 or len(hits) >= limit:
                break
            hits.append({
                "document_id": row["document_id"],
                "title": row["title"],
                "version": row["version"],
                "ordinal": row["ordinal"],
                "start": row["start"],
                "end": row["end"],
                "excerpt": row["text"][:200],
                "score": round(float(score), 3),
            })
        return {"query": query, "workspace": workspace, "scanned_chunks": len(rows), "hits": hits}

    def read_document(self, args):
        document_id = int(args["document_id"])
        doc = self.catalog.detail(self.actor, document_id)
        version = doc.get("current_version")
        with self.catalog.store.connection() as conn:
            rows = conn.execute(
                "SELECT ordinal,text,start,end FROM chunks WHERE document_id=? AND version=? ORDER BY ordinal",
                (document_id, version),
            ).fetchall()
        text = "".join(r["text"] for r in rows)
        return {
            "document_id": document_id,
            "title": doc.get("title"),
            "workspace": doc.get("workspace"),
            "audience": doc.get("audience"),
            "status": doc.get("status"),
            "version": version,
            "chars": len(text),
            "chunks": len(rows),
            "text": text,
        }

    def read_chunk(self, args):
        document_id = int(args["document_id"])
        ordinal = int(args["ordinal"])
        doc = self.catalog.detail(self.actor, document_id)
        version = doc.get("current_version")
        with self.catalog.store.connection() as conn:
            row = conn.execute(
                "SELECT ordinal,text,start,end FROM chunks WHERE document_id=? AND version=? AND ordinal=?",
                (document_id, version, ordinal),
            ).fetchone()
        if row is None:
            raise KBError("MCP.NO_SUCH_CHUNK", 404)
        return {"document_id": document_id, "title": doc.get("title"), "version": version,
                "ordinal": row["ordinal"], "start": row["start"], "end": row["end"], "text": row["text"]}

    def call(self, name, args):
        handler = {
            "list_workspaces": self.list_workspaces,
            "search_documents": self.search_documents,
            "read_document": self.read_document,
            "read_chunk": self.read_chunk,
        }.get(name)
        if handler is None:
            raise KBError("MCP.UNKNOWN_TOOL", 404)
        return handler(args or {})


def reply(msg_id, result=None, error=None):
    payload = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(toolbox, msg):
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        wanted = str(params.get("protocolVersion") or "")
        version = wanted if wanted in PROTOCOL_SUPPORTED else PROTOCOL_LATEST
        return reply(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": "这是企业内部知识库的只读入口。回答必须带 document_id 与字符偏移；"
                            "检索为空就说检索不到，不要补写库里没有的内容。",
        })
    if method == "notifications/initialized":
        return
    if method == "ping":
        return reply(msg_id, {})
    if method == "tools/list":
        return reply(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        try:
            data = toolbox.call(name, params.get("arguments") or {})
        except KBError as exc:
            return reply(msg_id, {
                "content": [{"type": "text", "text": json.dumps({"error": exc.code}, ensure_ascii=False)}],
                "isError": True,
            })
        except Exception as exc:  # noqa: BLE001
            return reply(msg_id, {
                "content": [{"type": "text", "text": json.dumps({"error": type(exc).__name__, "detail": str(exc)[:300]}, ensure_ascii=False)}],
                "isError": True,
            })
        return reply(msg_id, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": False})
    if msg_id is not None:
        return reply(msg_id, error={"code": -32601, "message": f"method not found: {method}"})


def main():
    parser = argparse.ArgumentParser(description="EKB MCP server (read-only, stdio)")
    parser.add_argument("--data-dir", default="runtime/console-live")
    parser.add_argument("--identity", default="reader")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    toolbox = Toolbox(Path(args.data_dir), args.identity)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply(None, error={"code": -32700, "message": "parse error"})
            continue
        try:
            handle(toolbox, msg)
        except Exception as exc:  # noqa: BLE001
            reply(msg.get("id"), error={"code": -32603, "message": f"internal error: {type(exc).__name__}"})


if __name__ == "__main__":
    main()
