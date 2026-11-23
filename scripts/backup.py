#!/usr/bin/env python
"""知识库备份：在线快照（VACUUM INTO）+ 校验 + 轮转 + 清单。

    python scripts/backup.py --data-dir runtime/console-live --out-dir D:/backup/ekb --keep 7

为什么要 VACUUM INTO 而不是直接 copy：SQLite 在写入中直接复制可能拿到撕裂的页；
VACUUM INTO 走事务读、产出的是重新压实的完整库，且不阻塞读。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

TABLES = ("documents", "versions", "chunks", "audit")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def counts(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        out = {}
        for table in TABLES:
            try:
                out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                out[table] = None
        out["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        return out
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="back up an EKB knowledge.db")
    parser.add_argument("--data-dir", required=True, help="含 knowledge.db 的目录")
    parser.add_argument("--out-dir", required=True, help="备份落盘目录")
    parser.add_argument("--keep", type=int, default=7, help="保留最近几份，默认 7")
    parser.add_argument("--with-accounts", action="store_true",
                        help="同时快照 accounts.json（账号库，恢复时要用）")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    source = data_dir / "knowledge.db"
    if not source.exists():
        print(json.dumps({"error": "knowledge.db 不存在", "path": str(source)}, ensure_ascii=False))
        return 1

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = out_dir / f"knowledge-{stamp}.db"
    if target.exists():
        print(json.dumps({"error": "目标已存在", "path": str(target)}, ensure_ascii=False))
        return 1

    conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
    finally:
        conn.close()

    if not target.exists() or target.stat().st_size == 0:
        print(json.dumps({"error": "备份文件为空"}, ensure_ascii=False))
        return 1

    meta = {
        "created_utc": stamp,
        "source": str(source),
        "file": target.name,
        "bytes": target.stat().st_size,
        "sha256": sha256(target),
        "tables": counts(target),
        "accounts": None,
    }
    if args.with_accounts:
        accounts = data_dir / "accounts.json"
        if accounts.exists():
            copy = out_dir / f"accounts-{stamp}.json"
            copy.write_bytes(accounts.read_bytes())
            meta["accounts"] = {"file": copy.name, "sha256": sha256(copy)}
    (out_dir / f"knowledge-{stamp}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    removed = []
    if args.keep > 0:
        archives = sorted(out_dir.glob("knowledge-*.db"))
        for stale in archives[: max(0, len(archives) - args.keep)]:
            stale.unlink(missing_ok=True)
            (out_dir / (stale.stem + ".json")).unlink(missing_ok=True)
            removed.append(stale.name)

    print(json.dumps({**meta, "removed": removed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
