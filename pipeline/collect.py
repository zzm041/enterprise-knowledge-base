"""资料自采集与自更新（演示层）。

三件事要分清，混在一起就会做成「每次点一下多一堆重复文档」：

1. 来源台账：一个 URL（或一份本地文件）只对应一篇知识库文档，记在 sources 表里。
2. 内容指纹：抓回来的正文算 sha256，与上次一致就什么都不做（skipped）；
   不一致就在同一篇文档上写新版本（旧版本留在版本表里，可回溯、可回滚）。
3. 采集历史：每次运行记 runs 表，抓了几个、新建/更新/跳过/失败各多少。

来源类型：
- file：本地文件（离线就能验证，自检走这条）
- http：单个网页，正文抽取后入库
- rss：RSS/Atom 源，取条目链接逐个抓正文

HTML 正文抽取只做块级去噪（扔掉 script/style/nav/footer/header/form），
不做 Readability 那套打分 —— 制度类页面结构比新闻页规整得多。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from pipeline.parse import clean_text

BLOCK_TAGS = {
    "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "section",
    "article", "td", "th", "blockquote", "pre", "dd", "dt", "ul", "ol", "table",
}
SKIP_TAGS = {
    "script", "style", "nav", "footer", "header", "form", "noscript", "iframe",
    "svg", "aside", "button", "select", "option",
}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text + " ")


def html_to_text(html: str) -> tuple[str, str]:
    parser = _Extractor()
    parser.feed(html)
    parser.close()
    raw = "".join(parser.parts)
    return clean_text(raw), parser.title.strip()


def parse_feed(xml_text: str) -> list[dict]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    items: list[dict] = []
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        entry = {"title": "", "link": "", "summary": ""}
        for child in node.iter():
            name = child.tag.split("}")[-1]
            text = (child.text or "").strip()
            if name == "title" and not entry["title"]:
                entry["title"] = text
            elif name == "link" and not entry["link"]:
                entry["link"] = text or (child.attrib.get("href") or "")
            elif name in ("description", "summary", "content") and not entry["summary"]:
                entry["summary"] = text
        if entry["link"]:
            items.append(entry)
    return items


def fetch(url: str, timeout: float = 20.0) -> bytes:
    request = Request(url, headers={
        "User-Agent": "EKB-collector/1.0 (+local demo)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def default_sources_file() -> Path:
    return Path(__file__).resolve().parents[1] / "demo" / "sources.json"


DEFAULT_SOURCES = [
    {
        "name": "本地制度·差旅标准（采集样例）",
        "kind": "file",
        "path": "demo/zh-docs/handbook/差旅标准与预订流程.md",
        "workspace": "handbook",
        "audience": "all",
        "category": "行政·差旅",
        "title": "差旅标准与预订流程（采集副本）",
    },
    {
        "name": "本地制度·信息安全事件上报（采集样例）",
        "kind": "file",
        "path": "demo/zh-docs/handbook/信息安全事件上报.md",
        "workspace": "handbook",
        "audience": "all",
        "category": "IT·安全",
        "title": "信息安全事件上报（采集副本）",
    },
]


def load_sources(path: Path | None = None) -> list[dict]:
    path = path or default_sources_file()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_SOURCES, ensure_ascii=False, indent=2), encoding="utf-8")
        return [dict(item) for item in DEFAULT_SOURCES]
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("sources") or []
    return [item for item in data if isinstance(item, dict) and item.get("name")]


def _read_source(source: dict, root: Path) -> tuple[str, str]:
    """返回 (正文, 标题)；失败抛异常，由调用方记成 failed。"""
    kind = (source.get("kind") or "file").lower()
    if kind in ("file", "local"):
        path = Path(source.get("path") or "")
        if not path.is_absolute():
            path = root / path
        if not path.exists():
            raise FileNotFoundError("本地来源文件不存在：" + str(path))
        raw = path.read_bytes()
        suffix = path.suffix.lower()
        if suffix in (".html", ".htm"):
            text, page_title = html_to_text(raw.decode("utf-8", "replace"))
            return text, source.get("title") or page_title or path.stem
        if suffix in (".md", ".txt", ".markdown"):
            from pipeline.parse import decode_bytes
            body, _ = decode_bytes(raw)
            return clean_text(body), source.get("title") or path.stem
        from pipeline.parse import parse_bytes
        parsed = parse_bytes(path.name, raw)
        return parsed.text, source.get("title") or parsed.title

    if kind in ("http", "https", "web", "url"):
        url = source.get("url") or ""
        if not url:
            raise ValueError("http 来源缺少 url")
        raw = fetch(url, float(source.get("timeout") or 20))
        text, page_title = html_to_text(raw.decode("utf-8", "replace"))
        return text, source.get("title") or page_title or urlparse(url).netloc

    if kind in ("rss", "atom", "feed"):
        url = source.get("url") or ""
        if not url:
            raise ValueError("rss 来源缺少 url")
        items = parse_feed(fetch(url, float(source.get("timeout") or 20)).decode("utf-8", "replace"))
        limit = int(source.get("limit") or 5)
        chunks = []
        for entry in items[:limit]:
            link = urljoin(url, entry["link"])
            try:
                page, _ = html_to_text(
                    fetch(link, float(source.get("timeout") or 20)).decode("utf-8", "replace")
                )
            except (HTTPError, URLError, TimeoutError):
                page = clean_text(entry.get("summary") or "")
            if page:
                chunks.append("## " + entry["title"] + "\n\n" + page)
        if not chunks:
            raise ValueError("RSS 里没有可用条目")
        return "\n\n".join(chunks), source.get("title") or urlparse(url).netloc

    raise ValueError("未知来源类型：" + kind)


def run_source(source: dict, *, root: Path, store, api_call, dry_run: bool = False,
               workspace: str | None = None) -> dict:
    """抓一份来源，按内容指纹决定 新建 / 升版本 / 跳过。"""
    name = source.get("name") or "unnamed"
    key = source.get("url") or source.get("path") or name
    # 页面上的「当前库」优先：个人库里点采集，落的就是个人库，不写回企业库。
    workspace = workspace or source.get("workspace") or "handbook"
    audience = source.get("audience") or "all"
    result = {"source": name, "key": key, "status": "skipped", "detail": ""}
    try:
        text, title = _read_source(source, root)
    except Exception as exc:  # noqa: BLE001 - 单个来源失败不影响整批
        result.update(status="failed", detail=type(exc).__name__ + ": " + str(exc))
        store.touch_source(key, name=name, status="failed", note=result["detail"])
        return result

    text = clean_text(text)
    if len(text) < 20:
        result.update(status="failed", detail="抓到的正文太短（正文抽取失败，或页面是脚本渲染）")
        store.touch_source(key, name=name, status="failed", note=result["detail"])
        return result

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    row = store.source(key)
    result["chars"] = len(text)
    result["sha256"] = digest[:12]
    if row and row.get("last_sha") == digest:
        result.update(status="skipped", detail="内容指纹未变", document_id=row.get("document_id"))
        store.touch_source(key, name=name, status="unchanged", sha=digest)
        return result
    if dry_run:
        result.update(status="parsed", title=title,
                      detail=("将升版本" if row and row.get("document_id") else "将新建"))
        return result

    document_id = row.get("document_id") if row else None
    if document_id:
        status, detail = api_call("GET", "/documents/" + str(document_id))
        if status != 200:
            document_id = None
    if document_id:
        latest = int(detail.get("latest_version") or 1)
        status, resp = api_call("POST", "/documents/" + str(document_id) + "/versions",
                                {"text": text, "expected_version": latest,
                                 "filename": "collector.md"})
        if status != 200:
            result.update(status="failed", detail="写新版本失败：" + str(status),
                          document_id=document_id)
            store.touch_source(key, name=name, status="failed", note=result["detail"])
            return result
        version = latest + 1
        action = "updated"
    else:
        status, resp = api_call("POST", "/documents", {
            "workspace": workspace, "title": title[:200], "text": text,
            "audience": audience, "filename": "collector.md",
        })
        if status not in (200, 201):
            result.update(status="failed", detail="新建文档失败：" + str(status))
            store.touch_source(key, name=name, status="failed", note=result["detail"])
            return result
        document_id = int(resp["id"])
        version = 1
        action = "created"

    ingest_status, _ = api_call("POST", "/documents/" + str(document_id) + "/versions/"
                                + str(version) + "/ingest")
    indexed = ingest_status == 200
    result.update(status=action, document_id=document_id, version=version, title=title,
                  indexed=indexed,
                  detail=("已索引" if indexed else "入库成功但索引失败：" + str(ingest_status)))
    store.touch_source(key, name=name, status=action, sha=digest, document_id=document_id,
                       workspace=workspace, audience=audience,
                       category=source.get("category") or "")
    return result


def run_all(*, root: Path, store, api_call, only: str | None = None,
            dry_run: bool = False, path: Path | None = None,
            workspace: str | None = None) -> dict:
    sources = load_sources(path)
    if only and only not in ("all", ""):
        sources = [item for item in sources if item.get("name") == only]
    run_id = store.start_run(only or "all", workspace or "handbook")
    started = time.perf_counter()
    items = [run_source(item, root=root, store=store, api_call=api_call, dry_run=dry_run,
                        workspace=workspace)
             for item in sources]
    counts = {
        "fetched": len(items),
        "created": sum(1 for item in items if item["status"] == "created"),
        "updated": sum(1 for item in items if item["status"] == "updated"),
        "skipped": sum(1 for item in items if item["status"] == "skipped"),
        "failed": sum(1 for item in items if item["status"] == "failed"),
    }
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    if not dry_run:
        store.finish_run(run_id, **counts, note=str(elapsed) + " ms")
    return {"run_id": run_id, "dry_run": dry_run, "elapsed_ms": elapsed,
            "summary": counts, "items": items,
            "sources_file": str(default_sources_file())}
