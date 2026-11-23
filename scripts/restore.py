#!/usr/bin/env python
"""知识库恢复：校验清单 → 完整性检查 → 原子替换 → 旧库留档。

    python scripts/restore.py --backup D:/backup/ekb/knowledge-20261124T010203Z.db --data-dir runtime/console-live

运行中的服务必须先停：SQLite 被别的进程持有写锁时，替换会失败或让对端读到空库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="restore an EKB knowledge.db from a backup")
    parser.add_argument("--backup", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--force", action="store_true", help="清单缺失或哈希不符时仍然恢复")
    args = parser.parse_args()

    backup = Path(args.backup).resolve()
    data_dir = Path(args.data_dir).resolve()
    if not backup.exists():
        print(json.dumps({"error": "备份不存在", "path": str(backup)}, ensure_ascii=False))
        return 1
    if not data_dir.exists():
        print(json.dumps({"error": "data-dir 不存在", "path": str(data_dir)}, ensure_ascii=False))
        return 1

    manifest_path = backup.with_suffix(".json")
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = manifest.get("sha256")
        actual = sha256(backup)
        if expected and expected != actual and not args.force:
            print(json.dumps({"error": "哈希与清单不符", "expected": expected, "actual": actual}, ensure_ascii=False))
            return 1

    conn = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {}
        for table in ("documents", "versions", "chunks", "audit"):
            try:
                tables[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                tables[table] = None
    finally:
        conn.close()
    if integrity != "ok" and not args.force:
        print(json.dumps({"error": "备份文件未通过 integrity_check", "result": integrity}, ensure_ascii=False))
        return 1

    target = data_dir / "knowledge.db"
    for sidecar in ("knowledge.db-wal", "knowledge.db-shm"):
        hint = data_dir / sidecar
        if hint.exists():
            print(json.dumps({"error": "发现运行期残留文件，请先停服务", "path": str(hint)}, ensure_ascii=False))
            return 1

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    previous = None
    if target.exists():
        previous = data_dir / f"knowledge.db.pre-restore-{stamp}"
        shutil.copy2(target, previous)

    staging = data_dir / f".knowledge.db.restore-{stamp}"
    shutil.copy2(backup, staging)
    os.replace(staging, target)

    print(json.dumps({
        "restored": str(target),
        "from": str(backup),
        "bytes": target.stat().st_size,
        "sha256": sha256(target),
        "integrity": integrity,
        "tables": tables,
        "previous": str(previous) if previous else None,
        "manifest_created_utc": manifest.get("created_utc"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
