"""文件解析与清洗（演示层）。

产品层（ekb/）的入库契约只接受 text（1..200_000 字符），所以「把文件变成正文」
这件事发生在产品之外——这里就是那一段，产品一行不改。

三条清洗原则都来自实测的坏样本：

1. PDF 的换行是排版产物，不是语义换行。中文行尾到行首直接拼接，
   英文按连字符断行（recom-\nmend）复原成 recommend。不处理的话
   检索会被「行尾碎片」污染，向量也会被切断的句子带偏。
2. 页眉页脚在每页重复。出现于 >=60% 页面的短行（<=40 字）视为装饰，删掉；
   否则「某某公司 内部资料 第 N 页」会成为每篇文档里最高频的短语。
3. 表格是知识库最容易检索不到的内容。一律摊平成「列名：值；列名：值」，
   让 BM25 与向量都能命中，而不是留一堆竖线。

解析器只用 pypdf / python-docx / openpyxl 三个纯 Python 依赖。
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

MAX_CHARS = 190_000  # 产品字段上限 200_000 字符
# 产品还有一个 250_000 字节的请求体上限（ekb/api.py BodyLimit），
# 中文一个字 3 字节，所以字符数与字节数都得管：谁先到就按谁截。
MAX_BYTES = 200_000
MIN_CHARS = 20


def truncate(text: str, warnings: list[str]) -> str:
    original = len(text)
    if original > MAX_CHARS:
        text = text[:MAX_CHARS]
    raw = text.encode("utf-8")
    if len(raw) > MAX_BYTES:
        text = raw[:MAX_BYTES].decode("utf-8", "ignore")
    if len(text) != original:
        warnings.append(
            f"正文 {original} 字 / {len(text.encode('utf-8'))} 字节，超出单篇入库上限，"
            f"已截断到 {len(text)} 字（字符 {MAX_CHARS} / 字节 {MAX_BYTES}）"
        )
    return text

KIND_BY_SUFFIX = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
}

# 明确告诉用户「另存为」比假装解析成功诚实：这两种是二进制老格式，pypdf/openpyxl 都读不了。
UNSUPPORTED_HINT = {
    ".doc": "旧版 .doc 是二进制格式，请另存为 .docx 再导入",
    ".xls": "旧版 .xls 是二进制格式，请另存为 .xlsx 再导入",
    ".wps": "WPS 自有格式，请另存为 .docx 再导入",
    ".et": "WPS 表格自有格式，请另存为 .xlsx 再导入",
}

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff]")
_SENT_END = "。！？；：、，）】》”’!?;:,)"


class UnsupportedFile(ValueError):
    """用户给了一个本版本读不了的格式——消息直接可展示。"""


@dataclass
class Parsed:
    title: str
    text: str
    kind: str
    pages: int = 0
    tables: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.text)

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "kind": self.kind,
            "chars": self.chars,
            "pages": self.pages,
            "tables": self.tables,
            "warnings": list(self.warnings),
        }


def detect_kind(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in UNSUPPORTED_HINT:
        raise UnsupportedFile(UNSUPPORTED_HINT[suffix])
    kind = KIND_BY_SUFFIX.get(suffix)
    if kind is None:
        raise UnsupportedFile(
            f"不支持 {suffix or '无扩展名'}；可导入 " + " / ".join(sorted(KIND_BY_SUFFIX))
        )
    return kind


def decode_bytes(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace"), "utf-8(replace)"


def _joinable(prev: str, cur: str) -> bool:
    """PDF 换行拼接：上一行还没说完一句，下一行接着同一句话。"""
    if not prev or not cur:
        return False
    if prev[-1] in _SENT_END:
        return False
    if cur[0] in "•-·*#|>0123456789（(【[":
        return False
    tail_cjk = bool(_CJK.match(prev[-1]))
    head_cjk = bool(_CJK.match(cur[0]))
    if tail_cjk and head_cjk:
        return True
    if tail_cjk and cur[0].isascii() and cur[0].isalnum() and len(cur) < 40:
        return cur[0].isupper() is False and prev[-1] not in "，,"
    return False


def clean_text(raw: str) -> str:
    """归一换行、去控制字符、拼回被排版切断的句子、压空白、去重复段落。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ").replace("\u00a0", " ")
    text = _CTRL.sub("", text)
    text = re.sub(r"([A-Za-z])-\n([a-z])", r"\1\2", text)

    lines: list[str] = []
    for line in text.split("\n"):
        cur = line.strip()
        if not cur:
            lines.append("")
            continue
        if lines and lines[-1] and _joinable(lines[-1], cur):
            lines[-1] = lines[-1] + cur
        else:
            lines.append(cur)

    text = "\n".join(lines)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return dedupe_blocks(text).strip()


def dedupe_blocks(text: str, min_len: int = 30) -> str:
    """同一段落在文档里出现多次（PDF 常见于分页重复）只留第一份。"""
    seen: set[str] = set()
    kept: list[str] = []
    for block in text.split("\n\n"):
        body = block.strip()
        if len(body) >= min_len:
            key = hashlib.sha1(body.encode("utf-8")).hexdigest()
            if key in seen:
                continue
            seen.add(key)
        kept.append(block)
    return "\n\n".join(kept)


def strip_running_heads(pages: list[str]) -> str:
    """删掉在多数页面重复出现的短行（页眉、页脚、页码信封）。"""
    counts: Counter[str] = Counter()
    for page in pages:
        lines = [line.strip() for line in page.split("\n") if line.strip()]
        if not lines:
            continue
        for line in set(lines[:2] + lines[-2:]):
            if 0 < len(line) <= 40:
                counts[line] += 1
    if len(pages) < 2:
        return "\n\n".join(pages)
    limit = max(2, int(len(pages) * 0.6))
    junk = {line for line, count in counts.items() if count >= limit}
    cleaned = []
    for page in pages:
        kept = [line for line in page.split("\n") if line.strip() not in junk]
        cleaned.append("\n".join(kept))
    return "\n\n".join(cleaned)


def table_lines(rows: list[list[str]]) -> list[str]:
    """表格摊平成可检索句子：列名：值；列名：值。"""
    cleaned = [[(cell or "").strip() for cell in row] for row in rows]
    cleaned = [row for row in cleaned if any(row)]
    if not cleaned:
        return []
    if len(cleaned) == 1:
        pairs = [f"第{i + 1}列：{value}" for i, value in enumerate(cleaned[0]) if value]
        return ["；".join(pairs)] if pairs else []
    header = cleaned[0]
    out: list[str] = []
    for row in cleaned[1:]:
        pairs = []
        for index, value in enumerate(row):
            if not value:
                continue
            name = header[index] if index < len(header) and header[index] else f"第{index + 1}列"
            pairs.append(f"{name}：{value}")
        if pairs:
            out.append("；".join(pairs))
    return out


_OCR_ENGINE = None
_OCR_FAILED = ""


def _ocr_engine():
    """惰性加载 OCR 引擎：首次调用要加载 onnx 模型（约 1-2 秒）。"""
    global _OCR_ENGINE, _OCR_FAILED
    if _OCR_ENGINE is not None or _OCR_FAILED:
        return _OCR_ENGINE
    try:
        from rapidocr_onnxruntime import RapidOCR
        _OCR_ENGINE = RapidOCR()
    except Exception as exc:  # noqa: BLE001 - 缺引擎/模型不可用都降级成警告，不打断导入
        _OCR_FAILED = (str(exc)[:120] or exc.__class__.__name__)
    return _OCR_ENGINE


def _ocr_image(data: bytes, warnings: list[str]) -> str:
    """对一张图片做 OCR；引擎缺失时只给警告，不抛异常。"""
    if not data:
        return ""
    engine = _ocr_engine()
    if engine is None:
        warnings.append("OCR 引擎不可用（" + (_OCR_FAILED or "未安装 rapidocr-onnxruntime") + "），扫描件只报告页数")
        return ""
    try:
        import numpy as np
        from PIL import Image

        image = Image.open(io.BytesIO(data)).convert("RGB")
        result, _ = engine(np.array(image))
    except Exception as exc:  # noqa: BLE001
        warnings.append("OCR 识别失败：" + str(exc)[:80])
        return ""
    lines = [str(item[1]).strip() for item in (result or [])
             if len(item) > 1 and str(item[1]).strip()]
    return "\n".join(lines)


def _pdf_tables(data: bytes, warnings: list[str]) -> tuple[list[str], int]:
    """用 pdfplumber 还原 PDF 里有线框的表格；没装库或没表格时返回空。"""
    try:
        import pdfplumber
    except ModuleNotFoundError:
        return [], 0
    parts: list[str] = []
    count = 0
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    rows = [["" if cell is None else str(cell).strip() for cell in row] for row in table]
                    rows = [row for row in rows if any(row)]
                    if len(rows) < 2:
                        continue
                    count += 1
                    parts.append("")
                    parts.extend(table_lines(rows))
                    parts.append("")
    except Exception as exc:  # noqa: BLE001 - 表格提取失败不影响正文
        warnings.append("PDF 表格提取失败：" + str(exc)[:80])
    return parts, count


def _parse_pdf(data: bytes, warnings: list[str]) -> tuple[str, int, int, str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001 - 加密 PDF 只能如实报告
            warnings.append("PDF 已加密，提取不到正文")
            return "", len(reader.pages), 0, ""
    pages = [(page.extract_text() or "") for page in reader.pages]
    total = sum(len(page) for page in pages)

    body: list[str] = []
    if total >= 50:
        body.append(strip_running_heads(pages))
    else:
        # 文字层几乎没有 ⇒ 按扫描件处理：把页面里的图片取出来做 OCR
        warnings.append("PDF 没有文字层，按扫描件走 OCR")
        ocr_parts: list[str] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                images = list(page.images or [])
            except Exception:  # noqa: BLE001 - 个别 PDF 的图片表坏掉
                images = []
            for image in images:
                text = _ocr_image(getattr(image, "data", b"") or b"", warnings)
                if text:
                    ocr_parts.append("## 第 %d 页（OCR）" % index)
                    ocr_parts.append(text)
        body.extend(ocr_parts)
        if not ocr_parts:
            warnings.append("OCR 也没有取到文字：可能整页是图片且分辨率过低")

    table_parts, tables = _pdf_tables(data, warnings)
    if table_parts:
        body.extend(table_parts)

    title = ""
    try:
        title = str((reader.metadata or {}).get("/Title") or "").strip()
    except Exception:  # noqa: BLE001 - metadata 坏了不影响正文
        title = ""
    return "\n".join(part for part in body if part), len(reader.pages), tables, title


def _docx_blocks(document):
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def _parse_docx(data: bytes, warnings: list[str]) -> tuple[str, int, int, str]:
    from docx import Document

    document = Document(io.BytesIO(data))
    parts: list[str] = []
    tables = 0
    for block in _docx_blocks(document):
        if block.__class__.__name__ == "Table":
            rows = [[cell.text for cell in row.cells] for row in block.rows]
            lines = table_lines(rows)
            if lines:
                tables += 1
                parts.append("")
                parts.extend(lines)
                parts.append("")
        else:
            style = (block.style.name or "").lower() if block.style is not None else ""
            text = block.text.strip()
            if not text:
                continue
            if style.startswith("heading") or style.startswith("标题"):
                parts.append("")
                parts.append("# " + text)
                parts.append("")
            else:
                parts.append(text)
    title = ""
    try:
        title = (document.core_properties.title or "").strip()
    except Exception:  # noqa: BLE001
        title = ""
    return "\n".join(parts), 0, tables, title


def _parse_xlsx(data: bytes, warnings: list[str]) -> tuple[str, int, int, str]:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    tables = 0
    for sheet in workbook.worksheets:
        rows = []
        for row in sheet.iter_rows(values_only=True):
            rows.append(["" if value is None else str(value).strip() for value in row])
        rows = [row for row in rows if any(row)]
        if not rows:
            continue
        tables += 1
        parts.append(f"## {sheet.title}")
        parts.extend(table_lines(rows))
        parts.append("")
    workbook.close()
    if tables == 0:
        warnings.append("工作簿里没有读到任何非空单元格")
    return "\n".join(parts), 0, tables, ""


def _parse_delimited(data: bytes, filename: str, warnings: list[str]) -> tuple[str, int, int, str]:
    text, encoding = decode_bytes(data)
    delimiter = "\t" if filename.lower().endswith(".tsv") else ","
    if delimiter == "," and text.count(";") > text.count(",") * 3:
        delimiter = ";"  # 欧洲区导出的 CSV 常见分号
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    lines = table_lines(rows)
    if not lines:
        warnings.append("表格是空的或只有表头")
    if encoding not in ("utf-8", "utf-8-sig"):
        warnings.append(f"按 {encoding} 解码，若出现乱码请另存为 UTF-8")
    return "\n".join(lines), 0, 1 if lines else 0, ""


def parse_bytes(filename: str, data: bytes) -> Parsed:
    """把一份文件解析成可入库正文；不写盘、不联网。"""
    kind = detect_kind(filename)
    warnings: list[str] = []
    if not data:
        raise UnsupportedFile("文件是空的")

    if kind == "pdf":
        body, pages, tables, meta_title = _parse_pdf(data, warnings)
    elif kind == "docx":
        body, pages, tables, meta_title = _parse_docx(data, warnings)
    elif kind == "xlsx":
        body, pages, tables, meta_title = _parse_xlsx(data, warnings)
    elif kind == "csv":
        body, pages, tables, meta_title = _parse_delimited(data, filename, warnings)
    else:
        text, encoding = decode_bytes(data)
        if encoding not in ("utf-8", "utf-8-sig"):
            warnings.append(f"按 {encoding} 解码，若出现乱码请另存为 UTF-8")
        body, pages, tables, meta_title = text, 0, 0, ""

    body = truncate(clean_text(body), warnings)
    if len(body) < MIN_CHARS:
        warnings.append("清洗后正文太短，可能只有标题或图片")

    title = (meta_title or Path(filename).stem).strip()[:200] or "未命名文档"
    return Parsed(title=title, text=body, kind=kind, pages=pages, tables=tables, warnings=warnings)


def parse_path(path: Path) -> Parsed:
    return parse_bytes(path.name, path.read_bytes())
