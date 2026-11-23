"""Agentic RAG：把「一次检索一次作答」换成有判定、有重试、有兜底的多步链路。

设计约束：
- ekb/ 是上游冻结层，这里只通过 kb.query(identity, workspace, question) 这一个入口说话，
  不改检索实现；多步能力全部留在 assistant 层。
- 每一步都是确定性的（改写规则 + 阈值判定），不额外调模型：多调一次模型会把延迟翻倍，
  而这一步的目的是「少一次误判」，不是「让模型更会聊」。
- 每步都留 trace：能指着某一次真实请求说清「它为什么重试了三次才答上」。
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Protocol

# 疑问虚词：这些字在多路召回里会进 BM25 分母，把词面覆盖率压低。
_QUESTION_WORDS = (
    "请问", "麻烦", "帮我", "想问", "想知道", "在哪儿", "在哪里", "在哪", "哪里",
    "怎么", "如何", "多久", "几次", "是多少", "是什么", "有什么", "能不能", "可不可以",
    "呢", "吗", "呀", "啊", "吧",
)
_SPLIT = re.compile(r"[，,；;。\n]|以及|还有|另外|顺便|同时")
_CONNECT = re.compile(r"(和|与|及|跟)")

_S = 12  # trace 里参数文本的截断长度


class KB(Protocol):
    def query(self, identity: str, workspace: str, question: str) -> dict: ...


def strip_question_words(text: str) -> str:
    """去掉疑问虚词与客套前缀，只留实体与动作——用于重试查询，不改首次查询。"""
    out = text.strip()
    for word in _QUESTION_WORDS:
        out = out.replace(word, "")
    out = out.strip(" 　的")
    return out or text


def split_subquestions(text: str) -> list[str]:
    """一句话里问了多件事时切开；切不出两块就返回空。"""
    parts = [p.strip(" 　") for p in _SPLIT.split(text) if p and p.strip(" 　")]
    parts = [p for p in parts if len(p) >= 2]
    if len(parts) < 2:
        # 「入库和离职要什么材料」这类没有标点的并列
        halves = [h.strip() for h in _CONNECT.split(text) if h and h.strip()]
        pairs = [h for h in halves if not _CONNECT.fullmatch(h)]
        # 两侧都要够长才切：「入库和离职要什么材料」切出「入库」这种残片只会浪费一次检索
        if len(pairs) >= 2 and all(len(p) >= 3 for p in pairs):
            return pairs[:2]
        return []
    return parts[:2]


def keywordize(text: str) -> str:
    """退回词面：留中文连续段与英文数字，去掉虚词，给分词器一份干净输入。"""
    cleaned = strip_question_words(text)
    spans = re.findall(r"[\u4e00-\u9fa5]{2,}|[A-Za-z0-9]{2,}", cleaned)
    return " ".join(spans) if spans else cleaned


def _max_cov(result: dict) -> float:
    trace = result.get("trace") or {}
    try:
        return float(trace.get("max_lexical_coverage") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def grade(result: dict) -> str:
    """strong=有引用且词面覆盖够；weak=答上了但证据薄；none=没答上。"""
    if result.get("status") != "answered":
        return "none"
    if not (result.get("citations") or []):
        return "none"
    return "strong" if _max_cov(result) >= 0.5 else "weak"


def compress_history(history: list[dict], keep_head: int = 2, keep_tail: int = 2) -> tuple[list[dict], int]:
    """会话过长时折叠中段。返回（压缩后的历史, 折叠条数）。

    只做「结构化折叠」，不调模型做摘要：调模型会把每轮延迟抬上去，而这里要的只是
    别让 20 轮闲聊把上下文吃满。
    """
    if len(history) <= keep_head + keep_tail + 2:
        return history, 0
    head = history[:keep_head]
    tail = history[-keep_tail:]
    folded = len(history) - keep_head - keep_tail
    marker = {"role": "system", "text": f"（此前 {folded} 条对话已折叠）"}
    return [*head, marker, *tail], folded


class AgenticRunner:
    """多步检索：retrieve → grade → 改写重试 → 择优 → 回执。

    max_rounds 控制「最多换几种问法」：默认 3，等价于最多 4 次检索。
    每次检索都是一次真实 HTTP 调用，代价可测，所以有上限、有提前退出。
    """

    def __init__(self, kb: KB, *, max_rounds: int = 3, strong_cov: float = 0.5,
                 planner: Any | None = None):
        self.kb = kb
        self.max_rounds = max_rounds
        self.strong_cov = strong_cov
        # planner：可选，ReAct 规划器——只产出「该怎么问」，不产出答案。
        self.planner = planner

    def candidates(self, question: str, decision_query: str | None, *,
                   workspace: str | None = None, identity: str | None = None,
                   react: dict | None = None) -> list[str]:
        """确定性的候选问法：改写句 → 原句 → 去虚词 → 子问题 → 纯词面。

        这里不调模型：ReAct 规划放在 run() 里、候选全落空之后才触发（见 rescue 注释），
        免得每次提问都先付一次模型延迟，却只换来和确定性候选差不多的问法。
        """
        out: list[str] = []
        first = decision_query or question
        for q in (
            first,
            question,
            strip_question_words(first),
            *split_subquestions(question),
            keywordize(first),
        ):
            q = (q or "").strip()
            if q and q not in out:
                out.append(q)
        return out

    def run(
        self,
        identity: str,
        workspace: str,
        question: str,
        decision_query: str | None = None,
        on_query: Callable[[str], None] | None = None,
    ) -> dict:
        """返回 {result, trace}；result 是最后一次可用的 kb.query 结果（可能仍是未答上）。"""
        attempts: list[dict] = []
        last: dict | None = None
        best: dict | None = None
        best_grade = "none"
        react: dict = {}
        cands = self.candidates(question, decision_query)
        queue: list[str] = list(cands[: self.max_rounds + 1])
        # 救援预算：确定性候选全落空后才允许 ReAct 再补问法（最多再两次检索）。
        rescue_left = 2 if self.planner is not None else 0
        planner_used = False
        index = 0
        while queue:
            q = queue.pop(0)
            if on_query is not None:
                on_query(q)
            started = time.perf_counter()
            try:
                result = self.kb.query(identity, workspace, q)
            except Exception as exc:  # noqa: BLE001 - 由上层按 KBError 语义处理
                attempts.append({"round": index, "query": q[:40], "error": type(exc).__name__})
                raise
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            last = result
            g = grade(result)
            attempts.append({
                "round": index,
                "query": q[:40],
                "status": result.get("status"),
                "grade": g,
                "citations": len(result.get("citations") or []),
                "max_cov": round(_max_cov(result), 3),
                "ms": elapsed,
            })
            index += 1
            # 先到先得：strong 立即收手，weak 记下继续找更强的
            if g == "strong":
                best, best_grade = result, "strong"
                break
            if g == "weak" and best_grade != "weak":
                best, best_grade = result, "weak"
            if result.get("status") == "knowledge_unavailable":
                # 网关/模型没起来：重试没有意义，直接把这次结果交上去
                best = best or result
                break
            # 救援：确定性候选问完了还没答上，才让 ReAct 用只读工具摸一遍库再补问法。
            if (not queue and not planner_used and rescue_left > 0
                    and workspace and identity):
                planner_used = True
                info = self.planner.plan(question, workspace, identity)
                react.update(info)
                react["trigger"] = "rescue"
                for extra in (info.get("queries") or [])[:rescue_left]:
                    extra = (extra or "").strip()
                    if extra and extra not in queue:
                        queue.append(extra)
        if best is None:
            best = last or {"status": "insufficient_knowledge", "citations": []}
        trace = {
            "agentic": True,
            "rounds": len(attempts),
            "best_grade": best_grade,
            "attempts": attempts,
            "final_status": best.get("status"),
        }
        if react:
            trace["react"] = react
        merged = dict(best)
        base_trace = dict(merged.get("trace") or {})
        base_trace["agentic"] = trace
        merged["trace"] = base_trace
        return merged
