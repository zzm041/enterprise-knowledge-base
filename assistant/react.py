"""ReAct 式的检索规划：让模型用工具先把库摸清楚，再交出「该怎么问」。

边界（和整个项目一致）：
- 模型只规划检索，不作答，也不签任何字：最终答案、引用、权限判定仍然由 kb.query 出。
- 工具全是只读的，权限跟着身份走（复用 scripts/mcp_server.py 的 Toolbox，语义完全一致）。
- 任何一步出问题（模型返回不合法、工具报错、没有 accounts.json）都直接退回空计划，
  由 AgenticRunner 用自己那套确定性候选接着跑——不会因为多了这一层而变得更差。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Protocol

import httpx

from assistant.llm import parse_json_object
from ekb.errors import KBError

TOOL_NAMES = ("list_workspaces", "search_documents", "read_document", "read_chunk")

REACT_SYSTEM = """你是企业知识库的检索计划员。你看不到答案，只负责摸清「这个库里是用什么词描述这件事的」，
然后交出更贴近库内用词的说法。规则：
1) 每轮只做一个动作，只输出一个 JSON 对象，不要解释。
2) 动作只能是这三个工具之一：
   {"action":"search_documents","args":{"query":"...","limit":5}}
   {"action":"read_document","args":{"document_id":12}}
   {"action":"read_chunk","args":{"document_id":12,"ordinal":2}}
3) 摸清之后输出 {"action":"final","queries":["问法一","问法二"]}，最多三条；
   queries 是「拿去检索的问法」，不是答案。
4) 不要自己回答业务问题；不要编造库里没有的制度、数字、流程。
5) 最多做两次工具调用，第三次必须给 final。同一个 query 不许问第二遍。
6) 只要搜索结果里已经出现和问题直接相关的标题（问灭火器就遇到「办公安全与消防应急」这种），
   立刻给 final，把该标题里的说法写进 queries；摸得再细也不会比这更有用。"""


class Chat(Protocol):
    def json(self, system: str, user: str, *, max_tokens: int = 512,
             history: list[dict[str, str]] | None = None) -> dict: ...


def _root_of(base_url: str) -> str:
    """把 OpenAI 兼容地址还原成 Ollama 根地址（/v1 去掉）。"""
    root = (base_url or "").rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root


class OllamaChat:
    """Ollama 原生 /api/chat 的最小客户端：think=False + format=json。

    为什么不用 OpenAI 兼容端点：qwen3 系会先把 token 预算花在 thinking 上，
    实测同一 prompt 走 /v1/chat/completions 拿回 8.1 秒、content 为空、finish_reason=length
    （把预算加到 1200 也一样），规划层就永远拿不到 JSON；走 /api/chat 加 think=False，
    同 prompt 532 毫秒返回合法 JSON。规划这一步要的是「快且稳」，所以单独开这条通道。
    """

    def __init__(self, base_url: str, model: str, timeout: float = 40.0) -> None:
        self.root = _root_of(base_url)
        self.model = model
        self.timeout = timeout

    def json(self, system: str, user: str, *, max_tokens: int = 220,
             history: list[dict[str, str]] | None = None) -> dict:
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        for turn in history or []:
            if turn.get("role") in ("user", "assistant") and turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": user})
        payload = {
            "model": self.model, "messages": messages, "stream": False,
            "think": False, "format": "json",
            "options": {"temperature": 0, "num_predict": max_tokens},
        }
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                response = client.post(f"{self.root}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise KBError("ASSISTANT.MODEL_UNAVAILABLE", 502) from exc
        content = str((data.get("message") or {}).get("content") or "")
        if not content.strip():
            raise KBError("ASSISTANT.MODEL_INVALID_OUTPUT", 502)
        return parse_json_object(content)


def detect_planner_llm(base_url: str, model: str, timeout: float = 40.0) -> "OllamaChat | None":
    """对端是 Ollama 就用原生通道（think 可控）；不是就返回 None，调用方回落到通用客户端。"""
    root = _root_of(base_url)
    if not root or not model:
        return None
    try:
        with httpx.Client(timeout=1.5, trust_env=False) as client:
            if client.get(f"{root}/api/version").status_code == 200:
                return OllamaChat(base_url, model, timeout=timeout)
    except Exception:  # noqa: BLE001 - 探测失败只是不用原生通道
        return None
    return None


def _clip(text: Any, limit: int = 160) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit]


def _summarize(action: str, result: dict) -> str:
    """把工具结果压成一行观察，避免把整篇文档塞进下一轮 prompt。"""
    if action == "list_workspaces":
        names = [w.get("workspace") for w in (result.get("workspaces") or [])]
        return f"可见 workspace：{', '.join(str(n) for n in names) or '（无）'}"
    if action == "search_documents":
        hits = result.get("hits") or []
        titles = []
        for hit in hits[:5]:
            t = hit.get("title")
            if t and t not in titles:
                titles.append(t)
        return (f"命中 {len(hits)} 条（扫描 {result.get('scanned_chunks')} 个片段）；"
                f"标题：{'、'.join(titles) or '（无）'}")
    if action == "read_document":
        return f"《{result.get('title')}》{result.get('chars')} 字：{_clip(result.get('text'), 120)}"
    if action == "read_chunk":
        return f"《{result.get('title')}》#{result.get('ordinal')}：{_clip(result.get('text'), 120)}"
    return _clip(result, 120)


def _clean_queries(raw: Any, question: str) -> list[str]:
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        q = " ".join(str(item or "").split())
        if not (2 <= len(q) <= 60) or q in out:
            continue
        out.append(q)
        if len(out) >= 3:
            break
    return out


class ReactPlanner:
    """把「模型 + 工具」限制在检索规划这一步。

    factory(identity) 每次按身份造一个只读 Toolbox；造不出来（比如没有 accounts.json）
    就让 plan() 返回空计划，调用方无需特判。
    """

    def __init__(self, factory: Callable[[str], Any], llm: Chat, *,
                 max_steps: int = 3, max_tokens: int = 220):
        self.factory = factory
        self.llm = llm
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self._boxes: dict[str, Any] = {}

    def _toolbox(self, identity: str):
        if identity not in self._boxes:
            self._boxes[identity] = self.factory(identity)
        return self._boxes[identity]

    def plan(self, question: str, workspace: str, identity: str) -> dict:
        started = time.perf_counter()
        steps: list[dict] = []
        try:
            toolbox = self._toolbox(identity)
        except Exception as exc:  # noqa: BLE001 - 没有本地库/账号时不规划，不是错误
            return {"status": "fallback", "reason": f"no_toolbox:{type(exc).__name__}",
                    "queries": [], "steps": [], "ms": 0.0}

        for index in range(self.max_steps):
            history = "\n".join(
                f"{s['step'] + 1}. {s['action']}({s['args']}) -> {s['observation']}"
                for s in steps
            ) or "（还没做任何动作）"
            prompt = (f"问题：{question}\n"
                      f"当前 workspace：{workspace}\n"
                      f"已经做过的动作：\n{history}\n\n"
                      "给出下一步动作（只输出 JSON）。")
            try:
                obj = self.llm.json(REACT_SYSTEM, prompt, max_tokens=self.max_tokens)
            except KBError as exc:
                return {"status": "fallback", "reason": f"model:{exc.code}",
                        "queries": [], "steps": steps,
                        "ms": round((time.perf_counter() - started) * 1000, 1)}
            action = str(obj.get("action") or "").strip()
            if action == "final":
                queries = _clean_queries(obj.get("queries"), question)
                return {"status": "ok", "queries": queries, "steps": steps,
                        "ms": round((time.perf_counter() - started) * 1000, 1)}
            if action not in TOOL_NAMES:
                return {"status": "fallback", "reason": f"bad_action:{action[:20]}",
                        "queries": [], "steps": steps,
                        "ms": round((time.perf_counter() - started) * 1000, 1)}
            args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
            if action == "search_documents":
                query = " ".join(str(args.get("query") or "").split())
                asked = [str((s.get("args") or {}).get("query") or "") for s in steps]
                if query and query in asked:  # 模型在绕圈：用它已经问过的说法收尾
                    return {"status": "ok", "queries": _clean_queries([query], question),
                            "steps": steps, "reason": "repeat_query",
                            "ms": round((time.perf_counter() - started) * 1000, 1)}
            try:
                result = toolbox.call(action, args)
                observation = _summarize(action, result or {})
            except Exception as exc:  # noqa: BLE001 - 工具失败当作一次观察，继续下一步
                observation = f"工具报错 {type(exc).__name__}"
            steps.append({
                "step": index,
                "action": action,
                "args": {k: (str(v)[:40] if isinstance(v, str) else v) for k, v in args.items()},
                "observation": observation[:200],
            })

        # 步数用尽但模型不会收尾：把它在工具里试过的问法交出去，总比空手好。
        asked = [str((s.get("args") or {}).get("query") or "") for s in steps
                 if s.get("action") == "search_documents"]
        return {"status": "fallback", "reason": "max_steps",
                "queries": _clean_queries(asked, question), "steps": steps,
                "ms": round((time.perf_counter() - started) * 1000, 1)}
