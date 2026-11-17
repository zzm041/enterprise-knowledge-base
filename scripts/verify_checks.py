"""Replay the console's interface checks without a browser.

demo/console.html 的「运行 N 项」和这个脚本跑的是同一组断言：
前者给人点，后者给 CI / 无浏览器环境用。任何一项不通过，退出码非 0。

用法：
    ./.venv/Scripts/python.exe scripts/verify_checks.py --base http://127.0.0.1:8806
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import random
import sys
import time
import urllib.error
import urllib.request
import zipfile


def minimal_docx(paragraphs) -> bytes:
    """现造一份最小 .docx，免得自检依赖 Office 或第三方库。

    解析路径与真实 Word 文件相同（pipeline/parse.py 用 python-docx 读 word/document.xml），
    所以能验证「Word 导入」这条链路，而不是只验证纯文本。
    """
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument'
        '.wordprocessingml.document.main+xml"/></Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships'
        '/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    body = "".join(
        '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
        % str(p).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        for p in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + body + "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
    return buf.getvalue()


class Console:
    """走真实会话的客户端：先登录拿 ekb_sid Cookie，再带上它发请求。

    早先用 X-Identity 头「自称身份」，那是被 PA-228 判掉的旧口径 —— 自检要是
    还走那条路，就等于在测一条已经关掉的后门。这里默认用 /__dev-login 拿会话
    （演示开关，写进审计），给了 --password 时还会额外验一次口令登录。
    """

    def __init__(self, base: str, timeout: float = 180.0, password: str = ""):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.password = password
        self.sids: dict[str, str] = {}

    @staticmethod
    def _sid_from(headers) -> str:
        for part in (headers.get("Set-Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "ekb_sid":
                return value
        return ""

    def raw(self, path: str, *, method: str = "GET", body=None, cookie: str = ""):
        data = None if body is None else json.dumps(body).encode("utf8")
        request = urllib.request.Request(self.base + "/api" + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if cookie:
            request.add_header("Cookie", "ekb_sid=" + cookie)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf8")
                try:
                    parsed = json.loads(raw) if raw.strip() else None
                except json.JSONDecodeError:
                    parsed = raw
                return response.status, parsed, dict(response.headers)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf8")
            try:
                parsed = json.loads(raw) if raw.strip() else None
            except json.JSONDecodeError:
                parsed = raw
            return exc.code, parsed, dict(exc.headers or {})

    def session(self, identity: str) -> str:
        if not identity or identity == "anon":
            return ""
        if identity in self.sids:
            return self.sids[identity]
        sid = ""
        try:
            status, _, headers = self.raw("/__dev-login", method="POST",
                                          body={"account": identity})
            if status == 200:
                sid = self._sid_from(headers)
        except OSError:
            sid = ""
        if not sid and self.password:
            status, _, headers = self.raw("/__login", method="POST",
                                          body={"account": identity, "password": self.password})
            if status == 200:
                sid = self._sid_from(headers)
        self.sids[identity] = sid
        return sid

    def api(self, path: str, *, method: str = "GET", identity: str = "reader", body=None):
        status, payload, _ = self.raw(path, method=method, body=body,
                                      cookie=self.session(identity))
        return status, payload

    def blob(self, path: str, *, identity: str = "reader"):
        """取二进制（导出件）——api() 只解 JSON。"""
        request = urllib.request.Request(self.base + "/api" + path)
        cookie = self.session(identity)
        if cookie:
            request.add_header("Cookie", "ekb_sid=" + cookie)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers or {})

    def wait_healthy(self, tries: int = 100) -> bool:
        for _ in range(tries):
            try:
                status, payload = self.api("/__meta", identity="anon")
            except OSError:
                status, payload = 0, None
            if status == 200 and isinstance(payload, dict):
                health = payload.get("api_health") or {}
                if health.get("status") == "ok":
                    return True
            time.sleep(0.2)
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8806", help="console 代理端口")
    parser.add_argument("--json", default=None, help="把结果写成 JSON")
    parser.add_argument("--password", default="",
                        help="给了就用账号口令登录（否则走 /__dev-login 演示开关）")
    args = parser.parse_args(argv)

    cli = Console(args.base, password=args.password)
    results = []

    def check(name: str, ok: bool, note: str = "") -> bool:
        results.append({"name": name, "ok": bool(ok), "note": note})
        print(("PASS " if ok else "FAIL ") + name + (("  " + note) if note else ""), flush=True)
        return ok
    status, _ = cli.api("/health", identity="anon")
    check("health", status == 200, f"HTTP {status}")

    status, _ = cli.api("/documents?workspace=handbook", identity="anon")
    check("anon", status == 401, f"HTTP {status}")

    # ---- 账号登录（会话 Cookie）----
    nologin_status, _ = cli.api("/__source?document_id=1", identity="anon")
    check("session required", nologin_status == 401, f"无会话读正文 HTTP {nologin_status}")

    probe = "probe-nobody"
    wrong_status = 0
    for _ in range(6):
        wrong_status, _ = cli.raw("/__login", method="POST",
                                  body={"account": probe, "password": "wrong-password"})[0:2]
    check("login locked", wrong_status == 429, f"连续错 6 次 HTTP {wrong_status}")

    if args.password:
        ok_status, _, ok_headers = cli.raw("/__login", method="POST",
                                           body={"account": "editor", "password": args.password})
        sid = cli._sid_from(ok_headers)
        me_status, me, _ = cli.raw("/__me", cookie=sid)
        bad_status, _, _ = cli.raw("/__login", method="POST",
                                   body={"account": "editor", "password": args.password + "-x"})
        check("login ok", ok_status == 200 and me_status == 200
              and (me or {}).get("account") == "editor" and (me or {}).get("role") == "editor",
              f"login HTTP {ok_status} me HTTP {me_status} account={(me or {}).get('account')}")
        check("login wrong", bad_status == 401, f"错口令 HTTP {bad_status}")
        check("session ttl", bool((me or {}).get("expires_at")) and (me or {}).get("expires_at") > time.time(),
              f"expires_at={(me or {}).get('expires_at')}")
    else:
        for name in ("login ok", "login wrong", "session ttl"):
            check(name, False, "skipped: 没给 --password")

    dev_status, _, dev_headers = cli.raw("/__dev-login", method="POST", body={"account": "staff"})
    dev_sid = cli._sid_from(dev_headers)
    _, dev_me, _ = cli.raw("/__me", cookie=dev_sid)
    check("dev switch", dev_status == 200 and (dev_me or {}).get("account") == "staff",
          f"HTTP {dev_status} account={(dev_me or {}).get('account')}")

    cli.raw("/__logout", method="POST", cookie=dev_sid)
    after_status, _, _ = cli.raw("/__me", cookie=dev_sid)
    check("logout", after_status == 401, f"登出后 /__me HTTP {after_status}")

    reply_status, reply_body = cli.api("/__replies", identity="editor")
    reply_keys = list((reply_body or {}).get("keys") or [])
    check("replies read", reply_status == 200 and len(reply_keys) == 4,
          f"HTTP {reply_status} keys={reply_keys} overridden={(reply_body or {}).get('overridden')}")
    deny_status, _ = cli.api("/__replies", method="POST", identity="reader",
                             body={"replies": {"refusal": "不该生效"}})
    check("replies forbidden", deny_status == 403, f"reader 改兜底话术 HTTP {deny_status}")

    marker = f"verify{random.randrange(0x100000, 0x1000000):x}"
    status_c, created = cli.api(
        "/documents",
        method="POST",
        identity="editor",
        body={"workspace": "handbook", "title": f"校验临时文档 {marker}",
              "text": f"{marker} 仅用于接口自检，自检结束自动归档。", "audience": "all"},
    )
    doc_id = (created or {}).get("id")
    status_i, _ = cli.api(f"/documents/{doc_id}/versions/1/ingest", method="POST", identity="editor", body={})
    check("create+ingest", status_c == 201 and status_i == 200, f"HTTP {status_c}/{status_i} id={doc_id}")

    # 问句要带上下文：裸 marker 是硬造词，rerank 分低于 0.12 门槛会（正确地）拒答（PA-225）
    _, answered = cli.api("/query", method="POST", body={"workspace": "handbook",
                                                         "question": marker + " 接口自检", "mode": "auto"})
    answered = answered or {}
    ans_cites = {c.get("document_id") for c in (answered.get("citations") or [])}
    check("citation", answered.get("status") == "answered" and doc_id in ans_cites,
          f"status={answered.get('status')} cited={sorted(x for x in ans_cites if x is not None)}")

    status, _ = cli.api(f"/documents/{doc_id}", identity="other-reader")
    check("cross-tenant", status == 404, f"HTTP {status}")

    status, _ = cli.api("/documents", method="POST", identity="reader",
                        body={"workspace": "handbook", "title": "x", "text": "y", "audience": "all"})
    check("reader write", status == 403, f"HTTP {status}")

    status, hi = cli.api("/assistant/chat", method="POST", identity="reader", body={"text": "你好"})
    hi = hi or {}
    check("assistant hello",
          status == 200 and hi.get("answer_status") == "smalltalk" and hi.get("intent") == "greeting"
          and len(hi.get("reply") or "") > 10,
          f"intent={hi.get('intent')} status={hi.get('answer_status')}")

    session_id = hi.get("session_id")
    _, q1 = cli.api("/assistant/chat", method="POST", identity="reader",
                    body={"text": "入职当天在哪里领取办公电脑", "session_id": session_id})
    _, q2 = cli.api("/assistant/chat", method="POST", identity="reader",
                    body={"text": "要带什么", "session_id": (q1 or {}).get("session_id") or session_id})
    q2 = q2 or {}
    standalone = q2.get("standalone_question") or ""
    check("assistant followup",
          q2.get("intent") == "followup" and bool(standalone) and bool(q2.get("citations")),
          f"standalone={standalone!r} cites={len(q2.get('citations') or [])}")

    status, star = cli.api("/assistant/chat", method="POST", identity="reader",
                           body={"text": "员工购房无息贷款怎么申请"})
    star = star or {}
    check("assistant refusal",
          status == 200 and star.get("answer_status") == "insufficient_knowledge"
          and not (star.get("citations") or []),
          f"status={star.get('answer_status')} cites={len(star.get('citations') or [])}")

    _, para = cli.api("/query", method="POST", body={"workspace": "handbook",
                                                     "question": "I forgot my login secret; where can I regain access?",
                                                     "mode": "auto"})
    para = para or {}
    cites = para.get("citations") or []
    check("paraphrase", (para.get("status") == "answered") == bool(cites),
          f"status={para.get('status')} cites={len(cites)}")

    _, refused = cli.api("/query", method="POST", body={"workspace": "handbook",
                                                        "question": "员工购房无息贷款怎么申请", "mode": "auto"})
    refused = refused or {}
    check("refusal", refused.get("status") == "insufficient_knowledge" and not (refused.get("citations") or []),
          f"status={refused.get('status')}")

    status, _ = cli.api("/documents", method="POST", identity="editor",
                        body={"workspace": "handbook", "title": "bad", "text": "z",
                              "audience": "all", "filename": "x.pdf"})
    check("format guard", status == 422, f"HTTP {status}")

    status, _ = cli.api("/__restart", method="POST", identity="editor", body={})
    healthy = cli.wait_healthy()
    back_status, back = cli.api(f"/documents/{doc_id}", identity="editor")
    for _ in range(4):
        if back_status == 200:
            break
        time.sleep(0.2)
        back_status, back = cli.api(f"/documents/{doc_id}", identity="editor")
    latest = (back or {}).get("latest_version")
    check("restart persistence",
          status == 200 and healthy and back_status == 200 and latest == 1,
          f"restart HTTP {status} healthy={healthy} doc HTTP {back_status} latest_version={latest}")

    archive_status, archived = cli.api(f"/documents/{doc_id}/archive", method="POST",
                                       identity="editor", body={"expected_version": 1})
    _, gone = cli.api("/query", method="POST", body={"workspace": "handbook",
                                                     "question": marker + " 接口自检", "mode": "auto"})
    # 归档的硬契约是「这篇不再被召回」，而不是「生成模型必须拒答」：换到量化档后，
    # 对库里没有的随机 marker 偶尔会答一句并引用别的文档（24 次里 1 次），那是生成侧行为，
    # 由下面 refusal 那项单独把关。（PA-222）
    gone_cites = (gone or {}).get("citations") or []
    check("archive exclusion",
          archive_status == 200 and (archived or {}).get("status") == "ARCHIVED"
          and not any((c or {}).get("document_id") == doc_id for c in gone_cites),
          f"archive HTTP {archive_status} doc={(archived or {}).get('status')} "
          f"query={(gone or {}).get('status')} cites={[(c or {}).get('document_id') for c in gone_cites]}")

    # 资料管理三件：改版（写新版本 + 重新索引）、权限（reader 碰管理端点即 403）、硬删除。
    status_v, revised = cli.api(f"/documents/{doc_id}/versions", method="POST", identity="editor",
                               body={"text": f"{marker} 第二版：改版自检。", "expected_version": 1})
    ingest_v, _ = cli.api(f"/documents/{doc_id}/versions/2/ingest", method="POST", identity="editor", body={})
    # 断言落在「改版真的生效」而不是「模型对一串随机 marker 给不给答案」：
    # 后者由生成模型判断，同一输入也可能一次答一次拒，属于模型侧抖动，不是改版失败。
    src_ok, src = cli.api(f"/__source?document_id={doc_id}", identity="editor")
    _, after_edit = cli.api(f"/documents/{doc_id}", identity="editor")
    after_edit = after_edit or {}
    active_v2 = [v for v in after_edit.get("versions", []) if v.get("version") == 2 and v.get("status") == "ACTIVE"]
    check("edit version",
          status_v == 200 and after_edit.get("latest_version") == 2 and after_edit.get("current_version") == 2
          and after_edit.get("status") == "ACTIVE" and ingest_v == 200 and bool(active_v2)
          and src_ok == 200 and "第二版" in ((src or {}).get("text") or ""),
          f"HTTP {status_v}/{ingest_v} latest={after_edit.get('latest_version')} "
          f"current={after_edit.get('current_version')} status={after_edit.get('status')} "
          f"v2_active={bool(active_v2)} source_len={len((src or {}).get('text') or '')}")

    # 读正文按 audience 判：读者能读 audience=all 的正文（产品 API 没有读原文入口，正文只能走 /__source），
    # internal 才是越权。旧断言要求 reader 对 /__source 一律 403，等于把产品缺口写成权限边界（PA-221）。
    src_status, src_body = cli.api(f"/__source?document_id={doc_id}", identity="reader")
    src_text = ((src_body or {}).get("text") or "")
    check("source read", src_status == 200 and len(src_text) > 0,
          f"HTTP {src_status} chars={len(src_text)}")

    _, listing = cli.api("/documents?workspace=handbook", identity="editor")
    int_doc = next((d.get("id") for d in (listing or {}).get("items", [])
                    if d.get("audience") == "internal"), None)
    int_status = None
    if int_doc:
        int_status, _ = cli.api(f"/__source?document_id={int_doc}", identity="reader")
    check("source internal 403", int_doc is not None and int_status == 403,
          f"doc={int_doc} reader HTTP {int_status}")

    purge_status, _ = cli.api("/__purge", method="POST", identity="reader",
                              body={"document_id": doc_id, "confirm_title": "x"})
    check("reader cannot write", purge_status == 403, f"purge HTTP {purge_status}")

    purge_ok, purged = cli.api("/__purge", method="POST", identity="editor",
                               body={"document_id": doc_id, "confirm_title": f"校验临时文档 {marker}"})
    gone_status, _ = cli.api(f"/documents/{doc_id}", identity="editor")
    _, audit = cli.api("/__audit?limit=20", identity="editor")
    actions = [row.get("action") for row in (audit or {}).get("items", [])]
    check("purge",
          purge_ok == 200 and ((purged or {}).get("deleted") or {}).get("chunks", 0) > 0
          and gone_status == 404 and "purged" in actions,
          f"HTTP {purge_ok} deleted={(purged or {}).get('deleted')} "
          f"get={gone_status} audit_has_purged={'purged' in actions}")


    # 入库流水线：Word 导入 → 建文档 → 建索引 → 可检索；重复文件跳过；只读拦截；
    # 导出（单篇 md/docx、整库 zip、审计 csv）；采集源 dry run；数据质量看板。
    docx = minimal_docx([
        f"自检导入件 {marker}",
        f"{marker} 住宿上限：一线城市每晚 600 元，仅用于接口自检。",
    ])
    payload = base64.b64encode(docx).decode("ascii")
    status, imported = cli.api("/__import", method="POST", identity="editor",
                              body={"files": [{"filename": "selfcheck.docx", "data": payload}]})
    imported = imported or {}
    summary = imported.get("summary") or {}
    first = (imported.get("items") or [{}])[0]
    imported_id = first.get("document_id")
    check("import docx",
          status == 200 and summary.get("imported") == 1 and first.get("kind") == "docx"
          and bool(first.get("indexed")),
          f"HTTP {status} summary={summary} kind={first.get('kind')} doc={imported_id} "
          f"chars={first.get('chars')}")

    status, again = cli.api("/__import", method="POST", identity="editor",
                            body={"files": [{"filename": "selfcheck.docx", "data": payload}]})
    again_summary = (again or {}).get("summary") or {}
    check("import dedupe", status == 200 and again_summary.get("skipped") == 1,
          f"HTTP {status} summary={again_summary}")

    status, _ = cli.api("/__import", method="POST", identity="reader",
                        body={"files": [{"filename": "x.txt",
                                         "data": base64.b64encode(b"plain").decode("ascii")}]})
    check("import forbidden", status == 403, f"HTTP {status}")
    # 压缩包批量导入：一个 .zip 里两个子目录的 md → 解包后逐篇入库，目录名进标题前缀，隐藏文件跳过
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("综合部/" + marker + "-a.md", "# 综合部制度\n\n" + marker + " 综合部办公用品按月申领。")
        zf.writestr("技术部/" + marker + "-b.md", "# 技术部制度\n\n" + marker + " 技术部值班按周轮换。")
        zf.writestr("综合部/.DS_Store", "junk")
    zip_b64 = base64.b64encode(zbuf.getvalue()).decode("ascii")
    zstatus, zres = cli.api("/__import", method="POST", identity="editor",
                            body={"workspace": "handbook", "audience": "all",
                                  "files": [{"filename": "制度包-" + marker + ".zip", "data": zip_b64}]})
    zres = zres or {}
    zsummary = zres.get("summary") or {}
    zitems = zres.get("items") or []
    ztitles = [str(it.get("title") or "") for it in zitems]
    check("zip import",
          zstatus == 200 and zsummary.get("imported") == 2 and zsummary.get("archives") == 1
          and len(ztitles) == 2 and all(t.startswith(("综合部", "技术部")) for t in ztitles),
          f"HTTP {zstatus} summary={zsummary} titles={ztitles}")
    zstatus2, _ = cli.api("/__import", method="POST", identity="reader",
                          body={"workspace": "handbook",
                                "files": [{"filename": "x.zip", "data": zip_b64}]})
    check("zip forbidden", zstatus2 == 403, f"HTTP {zstatus2}")

    # 上限提醒：301 个文件一次提交，超出上限的那个必须留痕，不能静默丢掉
    many_files = [{"filename": f"{marker}-many-{i}.md",
                   "data": base64.b64encode(f"# {marker} {i}\n\nbody placeholder.".encode()).decode()}
                  for i in range(301)]
    mstatus, mres = cli.api("/__import", method="POST", identity="editor",
                            body={"workspace": "handbook", "dry_run": True, "files": many_files})
    mres = mres or {}
    msummary = mres.get("summary") or {}
    mfail = [it for it in (mres.get("items") or []) if it.get("status") == "failed"]
    check("import limit",
          mstatus == 200 and msummary.get("parsed") == 300 and len(mfail) == 1
          and "300" in str((mfail[0] or {}).get("error") or ""),
          f"HTTP {mstatus} summary={msummary}")

    # 测的是「刚导入的内容能被检索到」。随机 marker 是硬造词，量化档生成侧偶尔判成
    # insufficient_knowledge（同 PA-222 那类抖动），与检索无关，故同一问最多问两次。
    hit = {}
    cited = set()
    for _ in range(2):
        _, hit = cli.api("/query", method="POST", body={"workspace": "handbook",
                                                   "question": marker + " 一线城市住宿上限", "mode": "auto"})
        hit = hit or {}
        cited = {c.get("document_id") for c in (hit.get("citations") or [])}
        if hit.get("status") == "answered" and imported_id in cited:
            break
    check("import searchable",
          hit.get("status") == "answered" and imported_id in cited,
          f"status={hit.get('status')} cited={sorted(x for x in cited if x)} imported={imported_id}")

    md_status, md_bytes, _ = cli.blob(
        f"/__export?scope=doc&document_id={imported_id}&format=md", identity="editor")
    docx_status, docx_out, headers = cli.blob(
        f"/__export?scope=doc&document_id={imported_id}&format=docx", identity="editor")
    latin = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    check("export doc",
          md_status == 200 and docx_status == 200 and marker.encode("utf8") in md_bytes
          and docx_out[:2] == b"PK" and len(docx_out) > 1000
          and "filename*" in latin.get("content-disposition", ""),
          f"md HTTP {md_status} {len(md_bytes)}B docx HTTP {docx_status} {len(docx_out)}B "
          f"disp={latin.get('content-disposition', '')[:40]}")

    zip_status, zip_out, _ = cli.blob("/__export?scope=all&format=zip", identity="editor")
    names = []
    if zip_status == 200 and zip_out[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(zip_out)) as archive:
            names = archive.namelist()
    check("export zip",
          zip_status == 200 and zip_out[:2] == b"PK" and "manifest.json" in names and len(names) > 2,
          f"HTTP {zip_status} {len(zip_out)}B entries={len(names)}")

    csv_status, csv_out, _ = cli.blob("/__export?scope=audit&format=csv", identity="editor")
    head = csv_out[:60].decode("utf8", "replace")
    check("export audit", csv_status == 200 and csv_out.startswith(b"\xef\xbb\xbf")
          and "actor" in head and "action" in head,
          f"HTTP {csv_status} {len(csv_out)}B head={head.strip()[:34]!r}")

    # 多模态读图：本地 Qwen3.5-9B-VL 把图片转写成 Markdown（扫描件 / 截图入库的前置步骤）。
    try:
        from PIL import Image, ImageDraw, ImageFont
        canvas = Image.new("RGB", (720, 200), "white")
        pen = ImageDraw.Draw(canvas)
        pen.text((28, 68), "OCR CHECK " + marker.upper(), fill="black",
                 font=ImageFont.load_default(size=46))
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG")
        png_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception as exc:  # noqa: BLE001 - 缺 Pillow/字体时降级成 failed 并写清原因
        png_b64 = ""
        png_error = f"skipped: 生成自检图片失败（{type(exc).__name__}: {exc}）"
    if png_b64:
        ocr_status, ocr_out = cli.api("/__ocr", method="POST", identity="editor",
                                      body={"filename": "selfcheck.png", "data": png_b64,
                                            "workspace": "handbook"})
        ocr_text = str((ocr_out or {}).get("text") or "").upper()
        check("ocr read",
              ocr_status == 200 and marker.upper() in ocr_text,
              f"HTTP {ocr_status} chars={(ocr_out or {}).get('chars')} "
              f"ms={(ocr_out or {}).get('ms')} text={ocr_text[:60]!r}")
        ocr_deny, _ = cli.api("/__ocr", method="POST", identity="reader",
                              body={"filename": "x.png", "data": png_b64})
        check("ocr forbidden", ocr_deny == 403, f"HTTP {ocr_deny}")
    else:
        check("ocr read", False, png_error)
        check("ocr forbidden", False, png_error)

    # 自检导入件也不入库：导出检查跑完按标题确认硬删，避免每次自检多留一篇。
    _, imp_detail = cli.api("/documents/%d" % imported_id, identity="editor")
    imp_title = (imp_detail or {}).get("title")
    if imp_title:
        cli.api("/__purge", method="POST", identity="editor",
                body={"document_id": imported_id, "confirm_title": imp_title})
    for it in (locals().get("zitems") or []):
        zid = it.get("document_id")
        ztitle = it.get("title")
        if zid and ztitle:
            cli.api("/__purge", method="POST", identity="editor",
                    body={"document_id": zid, "confirm_title": ztitle})

    status, collected = cli.api("/__collect", method="POST", identity="editor",
                                body={"source": "all", "dry_run": True})
    collect_summary = (collected or {}).get("summary") or {}
    check("collect dry run",
          status == 200 and collect_summary.get("fetched", 0) >= 1
          and collect_summary.get("created") == 0,
          f"HTTP {status} summary={collect_summary}")

    status, quality = cli.api("/__quality", identity="editor")
    quality = quality or {}
    denied, _ = cli.api("/__quality", identity="reader")
    issues = quality.get("issues") or {}
    check("quality board",
          status == 200 and denied == 403 and (quality.get("documents") or {}).get("total", 0) > 0,
          f"HTTP {status} reader={denied} documents={quality.get('documents')} "
          f"issues={issues} files={(quality.get('files') or {}).get('files')}")

    # 分类标签、批量操作、版本对比（都是演示层台账，产品 schema 不动）
    bulk_ids: list[int] = []
    for suffix in ("a", "b"):
        st, made = cli.api("/documents", method="POST", identity="editor",
                           body={"workspace": "handbook",
                                 "title": "临时条目 " + marker + " bulk-" + suffix,
                                 "text": marker + " 批量自检 " + suffix + " 用，跑完即下线。",
                                 "audience": "all"})
        new_id = (made or {}).get("id")
        if st == 201 and new_id:
            bulk_ids.append(int(new_id))
            cli.api("/documents/%d/versions/1/ingest" % int(new_id), method="POST",
                    identity="editor", body={})

    diff_doc = bulk_ids[0] if bulk_ids else None
    status_v2 = 0
    if diff_doc:
        status_v2, _ = cli.api("/documents/%d/versions" % diff_doc, method="POST",
                               identity="editor",
                               body={"text": marker + " 批量自检 a 第二版：加了住宿上限说明。",
                                     "expected_version": 1})
        cli.api("/documents/%d/versions/2/ingest" % diff_doc, method="POST", identity="editor", body={})
    v1_status, v1 = cli.api("/__source?document_id=%d&version=1" % diff_doc, identity="editor") if diff_doc else (0, {})
    v2_status, v2 = cli.api("/__source?document_id=%d&version=2" % diff_doc, identity="editor") if diff_doc else (0, {})
    t1 = ((v1 or {}).get("text") or "")
    t2 = ((v2 or {}).get("text") or "")
    check("version diff",
          bool(diff_doc) and v1_status == 200 and v2_status == 200
          and (v1 or {}).get("version") == 1 and (v2 or {}).get("version") == 2
          and t1 != t2 and "第二版" in t2 and "第二版" not in t1,
          f"doc={diff_doc} v1 HTTP {v1_status} v2 HTTP {v2_status} "
          f"changed={t1 != t2} v2_new={'第二版' in t2}")

    tag_status, tag_tree = cli.api("/__tags?workspace=handbook", identity="editor")
    tag_tree = tag_tree or {}
    groups = tag_tree.get("groups") or []
    tag_denied, _ = cli.api("/__tags", method="POST", identity="reader",
                            body={"workspace": "handbook", "document_id": diff_doc,
                                  "tags": ["越权标签"]})
    tag_set, _ = cli.api("/__tags", method="POST", identity="editor",
                         body={"workspace": "handbook", "document_id": diff_doc,
                               "tags": ["自检分类"]})
    _, tag_after = cli.api("/__tags?workspace=handbook", identity="editor")
    mine = [g for g in ((tag_after or {}).get("groups") or []) if g.get("tag") == "自检分类"]
    check("tag tree",
          tag_status == 200 and len(groups) >= 3 and (tag_tree.get("total") or 0) >= 3,
          f"HTTP {tag_status} groups={len(groups)} total={tag_tree.get('total')} "
          f"auto_tagged={tag_tree.get('auto_tagged')}")
    check("tag write",
          tag_set == 200 and bool(mine) and diff_doc in ((mine[0] or {}).get("ids") or []),
          f"HTTP {tag_set} tag={'自检分类' if mine else 'missing'}")
    check("tag forbidden", tag_denied == 403, f"HTTP {tag_denied}")

    if bulk_ids:
        join = ",".join(str(x) for x in bulk_ids)
        bz_status, bz_out, _ = cli.blob("/__export?scope=docs&ids=" + join, identity="editor")
        check("bulk export",
              bz_status == 200 and bz_out[:2] == b"PK" and len(bz_out) > 500,
              f"HTTP {bz_status} {len(bz_out)}B ids={bulk_ids}")
        b_status, batch = cli.api("/__batch", method="POST", identity="editor",
                                  body={"workspace": "handbook", "action": "archive",
                                        "ids": bulk_ids})
        archived = (batch or {}).get("archived") or []
        states = []
        for doc_id in bulk_ids:
            _, detail = cli.api("/documents/%d" % doc_id, identity="editor")
            states.append((detail or {}).get("status"))
        check("bulk archive",
              b_status == 200 and len(archived) == len(bulk_ids)
              and all(state == "ARCHIVED" for state in states),
              f"HTTP {b_status} archived={archived} states={states}")
        b_denied, _ = cli.api("/__batch", method="POST", identity="reader",
                              body={"workspace": "handbook", "action": "archive", "ids": bulk_ids})
        check("bulk forbidden", b_denied == 403, f"HTTP {b_denied}")
        # 临时文档不入库：下线后按标题确认硬删，否则每跑一次自检就往侧栏留两篇。
        for doc_id in bulk_ids:
            _, detail = cli.api("/documents/%d" % doc_id, identity="editor")
            title = (detail or {}).get("title")
            if title:
                cli.api("/__purge", method="POST", identity="editor",
                        body={"document_id": doc_id, "confirm_title": title})
    else:
        for name in ("version diff", "tag tree", "tag write", "tag forbidden",
                     "bulk export", "bulk archive", "bulk forbidden"):
            check(name, False, "skipped: 临时文档没建出来")

    # 双层：企业库 + 个人库。个人库的私有性靠产品的工作区级隔离（ekb/auth.py require(workspace)），
    # 同一个身份在企业库可能只是 reader，在自己的个人库是 editor —— 所以权限必须按库判。
    personal = "personal-wangfang"          # reader 自己的库
    foreign = "personal-liqiang"            # staff 的库，reader 无权
    pmarker = "pnote" + marker
    status, made = cli.api("/documents", method="POST", identity="reader",
                           body={"workspace": personal, "title": f"个人条目 {pmarker}",
                                 "text": f"{pmarker} 个人库自检用，跑完即删。", "audience": "all"})
    made = made or {}
    pid = made.get("id")
    ok_write = status == 201 and bool(pid)
    check("personal write", ok_write, f"HTTP {status} doc={pid} workspace={personal}")

    if ok_write:
        cli.api(f"/documents/{pid}/versions/1/ingest", method="POST", identity="reader", body={})
        status_list, listing = cli.api(f"/documents?workspace={personal}", identity="reader")
        ids = {item.get("id") for item in ((listing or {}).get("items") or [])}
        check("personal list", status_list == 200 and pid in ids,
              f"HTTP {status_list} docs={len(ids)} has={pid in ids}")

        pz_status, pz_out, _ = cli.blob(
            f"/__export?scope=all&format=zip&workspace={personal}", identity="reader")
        check("personal export", pz_status == 200 and pz_out[:2] == b"PK",
              f"HTTP {pz_status} {len(pz_out)}B")

        status_purge, purged = cli.api("/__purge", method="POST", identity="reader",
                                       body={"document_id": pid,
                                             "confirm_title": f"个人条目 {pmarker}",
                                             "workspace": personal})
        deleted = (purged or {}).get("deleted") or {}
        check("personal purge", status_purge == 200 and deleted.get("documents") == 1,
              f"HTTP {status_purge} deleted={deleted}")
    else:
        check("personal list", False, "skipped: personal write failed")
        check("personal export", False, "skipped: personal write failed")
        check("personal purge", False, "skipped: personal write failed")

    deny_status, _ = cli.api(f"/documents?workspace={foreign}", identity="reader")
    deny_q, _ = cli.api("/query", method="POST", identity="reader",
                        body={"workspace": foreign, "question": "入职有什么福利", "mode": "auto"})
    check("personal isolate", deny_status == 403 and deny_q == 403,
          f"list HTTP {deny_status} query HTTP {deny_q} foreign={foreign}")

    passed = sum(1 for item in results if item["ok"])
    total = len(results)
    print(json.dumps({"passed": passed, "total": total,
                      "failed": [item["name"] for item in results if not item["ok"]]}, ensure_ascii=False))
    if args.json:
        with open(args.json, "w", encoding="utf8") as handle:
            json.dump({"base": args.base, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                       "passed": passed, "total": total, "checks": results}, handle, ensure_ascii=False, indent=2)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
