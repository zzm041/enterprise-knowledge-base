"""Intent routing: deterministic fast path first, model classification second.

Production assistants route *before* retrieval: a greeting must not burn a retrieval
call, and a follow-up must be rewritten before it is retrieved. The rule layer keeps
the obvious cases exact and free; the model layer handles the long tail and also fuses
query rewriting into the same step (one call instead of two).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

GREETING = "greeting"
CHITCHAT = "chitchat"
CAPABILITY = "capability"
THANKS = "thanks"
GOODBYE = "goodbye"
KNOWLEDGE = "knowledge"
FOLLOWUP = "followup"
OUT_OF_SCOPE = "out_of_scope"

ROUTABLE = {KNOWLEDGE, FOLLOWUP}

# 规则层：只覆盖高频、零歧义的表达，命中即短路（不花模型调用）
_RULES: list[tuple[str, re.Pattern[str]]] = [
    # 没有真实坐席：要真人/投诉的诉求归到能力边界之外，不承诺转接、不发工单号。
    (OUT_OF_SCOPE, re.compile(r"(转|找|叫|联系).{0,4}(人工|客服|同事|负责人|hr|HR|财务|主管)|"
                         r"投诉|举报人工|(human|agent|representative)\b", re.I)),
    (CAPABILITY, re.compile(r"^(你能做什么|你能干什么|你会什么|有什么功能|怎么用|如何使用|帮助|help|使用说明)"
                            r"[?？!！。,.，、~～\s]*$", re.I)),
    (THANKS, re.compile(r"^(谢谢|多谢|感谢|好的|好|ok|OK|okay|收到|了解|明白|thx|thanks)[!！。,.，、~～\s]*$")),
    (GOODBYE, re.compile(r"^(再见|拜拜|bye|goodbye|结束|没事了)[!！。,.，、?？~～\s]*$", re.I)),
    (GREETING, re.compile(r"^(你好|您好|hi|hello|hey|嗨|哈喽|在吗|在不在|早上好|上午好|下午好|晚上好|"
                          r"早安|午安|晚安)[!！。,.，、?？~～\s]*$", re.I)),
]

SMALL_TALK_MARKERS = re.compile(
    r"(你是谁|你叫什么|你是什么|你是人|你是机器|你能聊天|陪我聊|讲个笑话|说个笑话|"
    r"今天天气|你吃饭|你多大|你有男朋友|你有女朋友)"
)


@dataclass
class Decision:
    intent: str
    query: str                      # standalone question used for retrieval
    confidence: float = 1.0
    source: str = "rule"            # rule | model | default
    notes: list[str] = field(default_factory=list)


def classify_by_rule(text: str) -> Decision | None:
    stripped = text.strip()
    if not stripped:
        return Decision(GREETING, "", source="rule", notes=["empty"])
    for intent, pattern in _RULES:
        if pattern.search(stripped):
            return Decision(intent, stripped, source="rule")
    if SMALL_TALK_MARKERS.search(stripped):
        return Decision(CHITCHAT, stripped, source="rule")
    return None


ROUTER_SYSTEM = """你是企业知识库助手的路由器。只输出 JSON，不要解释。
字段：
- intent：greeting | chitchat | capability | thanks | goodbye | knowledge | followup | out_of_scope
- standalone_question：当 intent 是 knowledge 或 followup 时，把用户这句话改写成可以独立检索的问题
  （结合对话历史补全“它/这个/那它/上面说的/还有呢”等指代，保留关键实体与数字，不要添加历史中不存在的信息）；
  其他 intent 填空字符串。
- reason：一句中文说明判断依据（不超过 20 字）。

判定要求：
- 寒暄、感谢、告别、自我介绍类闲聊 → 对应 intent，不要当知识问题。
- 问公司制度、流程、时限、权限、报销、假期、采购、安全、IT 等内部信息 → knowledge。
- 承接上一轮的追问（“那要几天”“流程呢”“这个谁审批”）→ followup，并改写为独立问题。
- 追问的改写**必须带上上一轮的主体词**，不能泛化成只有“报销标准”“流程”“时限”这类通用词。例：
  历史「出差住宿怎么订酒店」+「那报销标准呢」→「出差住宿的报销标准是什么」；
  历史「年假有几天」+「那怎么申请」→「年假怎么申请」；
  历史「采购一万元以上要几家比价」+「那五万以上呢」→「采购五万元以上要几家比价」。
- 与公司知识库无关的外部问题（天气、写代码、股票、明星）→ out_of_scope。
- 要求真人处理、投诉、或明确不想和机器人说话 → out_of_scope（这一层没有真人坐席，不要承诺转接、不要编工单号）。"""


def classify(text: str, llm, history: list[dict[str, str]]) -> Decision:
    rule = classify_by_rule(text)
    if rule is not None:
        return rule
    try:
        data = llm.json(
            ROUTER_SYSTEM,
            f"对话历史（最近几轮）：\n{_render_history(history)}\n\n用户这句话：{text}",
            max_tokens=256,
        )
    except Exception:  # noqa: BLE001 - routing must never take the whole turn down
        return Decision(KNOWLEDGE, text, confidence=0.3, source="default",
                        notes=["router_failed_fallback_to_retrieval"])
    intent = str(data.get("intent") or "").strip().lower()
    if intent not in {GREETING, CHITCHAT, CAPABILITY, THANKS, GOODBYE,
                      KNOWLEDGE, FOLLOWUP, OUT_OF_SCOPE}:
        intent = KNOWLEDGE
    standalone = str(data.get("standalone_question") or "").strip()
    if intent in ROUTABLE and not standalone:
        standalone = text
    confidence = 1.0 if intent not in ROUTABLE else 0.8
    return Decision(intent, standalone, confidence=confidence, source="model",
                    notes=[str(data.get("reason") or "")][:1])


def _render_history(history: list[dict[str, str]], limit: int = 6) -> str:
    if not history:
        return "（无）"
    lines = []
    for turn in history[-limit:]:
        who = "用户" if turn.get("role") == "user" else "助手"
        lines.append(f"{who}：{turn.get('content', '')[:200]}")
    return "\n".join(lines)
