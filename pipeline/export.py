"""导出（演示层）：正文出去，格式按用途选。

导出的内容来自产品库里真实存的版本正文（versions.source），不是前端缓存，
所以屏幕上看到的和导出得到的，是同一份数据。

- Markdown：给人读、给别的系统吃
- Word：给需要走审批/盖章流程的场合（python-docx，保留标题层级）
- 整库 zip：每篇一个 .md 加一份 manifest.json（迁移/备份用）
- CSV：审计流水，带 UTF-8 BOM，Excel 直接双击能打开
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import datetime

ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(name: str, fallback: str = "document") -> str:
    cleaned = ILLEGAL.sub("_", (name or "").strip()).strip(" ._")
    return (cleaned or fallback)[:80]


def md_bytes(title: str, text: str, meta: dict) -> bytes:
    header = [
        "# " + title,
        "",
        "> " + " · ".join(f"{key}：{value}" for key, value in meta.items() if value not in (None, "")),
        "",
        "",
    ]
    return ("\n".join(header) + text.strip() + "\n").encode("utf-8")


def docx_bytes(title: str, text: str, meta: dict) -> bytes:
    from docx import Document
    from docx.shared import Pt

    document = Document()
    document.add_heading(title, level=0)
    note = document.add_paragraph()
    run = note.add_run(" · ".join(f"{k}：{v}" for k, v in meta.items() if v not in (None, "")))
    run.font.size = Pt(9)
    run.italic = True

    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            paragraph = "\n".join(buffer).strip()
            if paragraph:
                document.add_paragraph(paragraph)
            buffer.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith("#"):
            flush()
            level = min(len(stripped) - len(stripped.lstrip("#")), 3) or 1
            document.add_heading(stripped.lstrip("#").strip(), level=level)
            continue
        if stripped.startswith(("- ", "* ")):
            flush()
            document.add_paragraph(stripped[2:].strip(), style="List Bullet")
            continue
        buffer.append(stripped)
    flush()

    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def zip_bytes(docs: list[dict]) -> bytes:
    out = io.BytesIO()
    manifest = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(docs),
        "documents": [],
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for doc in docs:
            name = f"{doc['id']:04d}-{safe_name(doc['title'])}.md"
            meta = {
                "文档 ID": doc["id"],
                "版本": "v" + str(doc.get("version")),
                "可见范围": doc.get("audience"),
                "状态": doc.get("status"),
                "字符数": len(doc.get("text") or ""),
            }
            archive.writestr(name, md_bytes(doc["title"], doc.get("text") or "", meta))
            manifest["documents"].append({
                "file": name,
                "id": doc["id"],
                "title": doc["title"],
                "version": doc.get("version"),
                "audience": doc.get("audience"),
                "status": doc.get("status"),
                "chars": len(doc.get("text") or ""),
            })
        archive.writestr("manifest.json",
                         json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return out.getvalue()


def csv_bytes(rows: list[dict], columns: list[str] | None = None) -> bytes:
    buffer = io.StringIO()
    if not rows:
        buffer.write("(empty)\n")
        return buffer.getvalue().encode("utf-8-sig")
    columns = columns or list(rows[0].keys())
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key) for key in columns})
    return buffer.getvalue().encode("utf-8-sig")
