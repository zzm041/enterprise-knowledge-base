"""Turn orchestration: route -> (rewrite) -> retrieve -> answer -> persist -> backlog.

The assistant is deliberately a thin orchestration layer with hard rules:
* small talk never touches retrieval, and retrieval never invents a policy answer;
* an answer is only shipped with citations from the KB, otherwise the turn is a refusal;
* every refusal is recorded so operations can close the gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

from ekb.errors import KBError

from assistant import intent as it
from assistant.kb import KBClient
from assistant.llm import ChatModel
from assistant.store import Store

SMALL_TALK_SYSTEM = """你是企业知识库里的助手，正在和员工闲聊。规则：
1) 一到两句话，语气自然、简短，不要说“作为AI”。
2) 绝对不要编造公司的制度、数字、流程或人名——制度类问题一律引导用户直接问，由知识库带引用回答。
3) 结尾给一个可执行的下一步（例如“想查哪条制度，直接说关键词就行”），不要重复同样的句式。"""

SMALL_TALK_FALLBACK = "这个我聊不深，我的正事是帮你查公司制度。比如入职、报销、请假、采购、权限这些，说个关键词就行。"

REFUSAL_REPLY = (
    "这个问题在现有文档里找不到能支撑回答的原文，所以我先不给结论。\n"
    "可以换一个更贴近制度用词的说法；这个问题我已经记进未解决问题清单，方便补文档。"
)

UNAVAILABLE_REPLY = (
    "检索服务暂时不可用（本地向量模型或生成模型没起来），这次给不出带出处的答案。\n"
    "重启演示栈后会恢复；这不是知识库里没有这条制度。"
)

NO_PERMISSION_REPLY = (
    "你当前身份看不到能回答这个问题的文档（可能属于内部或受限范围）。\n"
    "可以切换到有权限的身份再问；或者直接问制度里对应的流程，我按原文答。"
)

OUT_OF_SCOPE_REPLY = (
    "这个不在我负责的范围内——我只管公司制度、流程、权限这类问题。"
    "如果是业务系统报错或外部问题，我这边只能给到制度原文和出处，具体处理得由对应部门定。"
)

# 兜底话术默认值。企业部署通常要把这几句改成自己公司的口吻（FastGPT 的「无匹配数据时拒绝回答」、
# Onyx 的 fallback response 都是这么做的），所以这里留成可配置项：
# assistant-config.json 里写 {"assistant": {"replies": {"refusal": "...", "out_of_scope": "..."}}} 即可覆盖。
REPLY_DEFAULTS = {
    "refusal": REFUSAL_REPLY,
    "unavailable": UNAVAILABLE_REPLY,
    "no_permission": NO_PERMISSION_REPLY,
    "out_of_scope": OUT_OF_SCOPE_REPLY,
}


@dataclass
class AssistantConfig:
    workspace: str = "handbook"
    max_history: int = 6
    retry_on_insufficient: bool = True
    greeting_examples: list[str] = field(
        default_factory=lambda: [
            "入职当天在哪里领取办公电脑？",
            "出差住宿每晚上限多少？",
            "采购一万元以上要几家比价？",
        ]
    )
    # 兜底话术可覆盖：键取 REPLY_DEFAULTS 的键（refusal / unavailable / no_permission / out_of_scope）。
    replies: dict[str, str] = field(default_factory=dict)


def _cite_payload(citations: list[dict]) -> list[dict]:
    out = []
    for item in citations or []:
        out.append(
            {
                "chunk_id": item.get("chunk_id"),
                "document_id": item.get("document_id"),
                "title": item.get("title"),
                "version": item.get("version"),
                "start": item.get("start"),
                "end": item.get("end"),
                "excerpt": item.get("excerpt") or item.get("quote"),
            }
        )
    return out


class Assistant:
    def __init__(
        self,
        store: Store,
        kb: KBClient,
        llm: ChatModel,
        config: AssistantConfig | None = None,
    ) -> None:
        self.store = store
        self.kb = kb
        self.llm = llm
        self.config = config or AssistantConfig()

    def reply_text(self, key: str, default: str | None = None) -> str:
        """取兜底话术：配置里有非空值就用配置，否则用内置默认。"""
        base = default if default is not None else REPLY_DEFAULTS.get(key, "")
        custom = (self.config.replies or {}).get(key) or ""
        return custom.strip() or base

    # -- public -----------------------------------------------------------
    def chat(
        self,
        identity: str,
        text: str,
        *,
        session_id: str | None = None,
        workspace: str | None = None,
    ) -> dict:
        started = perf_counter()
        workspace = workspace or self.config.workspace
        session = self._session(identity, session_id, workspace)
        history = self.store.history(session["id"], limit=self.config.max_history)
        self.store.add_message(session["id"], "user", text)

        decision = it.classify(text, self.llm, history)
        payload = self._dispatch(identity, session, workspace, text, decision, history)

        latency = round((perf_counter() - started) * 1000, 1)
        message_id = self.store.add_message(
            session["id"],
            "assistant",
            payload["reply"],
            intent=decision.intent,
            standalone_question=decision.query or None,
            answer_status=payload["answer_status"],
            citations=payload.get("citations") or [],
            latency_ms=latency,
        )
        self.store.touch(session["id"])
        payload.update(
            {
                "session_id": session["id"],
                "message_id": message_id,
                "workspace": workspace,
                "intent": decision.intent,
                "route_source": decision.source,
                "confidence": decision.confidence,
                "standalone_question": decision.query or text,
                "notes": decision.notes,
                "latency_ms": latency,
            }
        )
        return payload

    def feedback(self, message_id: int, value: str, comment: str | None = None) -> dict:
        fid = self.store.add_feedback(message_id, value, comment)
        return {"id": fid, "message_id": message_id, "value": value}

    # -- internals --------------------------------------------------------
    def _session(self, identity: str, session_id: str | None, workspace: str) -> dict:
        if session_id:
            existing = self.store.session(session_id)
            if existing and existing["subject"] == identity:
                return existing
        tenant, _, subject = identity.partition("/")
        new_id = self.store.create_session(tenant or identity, subject or identity, workspace)
        return self.store.session(new_id)  # type: ignore[return-value]

    def _dispatch(self, identity, session, workspace, text, decision, history) -> dict:
        intent = decision.intent
        if intent == it.GREETING:
            return self._greeting(session)
        if intent == it.CAPABILITY:
            return self._capability(session)
        if intent == it.THANKS:
            return self._plain(
                "不客气。还要查别的制度，直接说关键词就行。",
                answer_status="smalltalk",
                suggestions=self.config.greeting_examples[:2],
            )
        if intent == it.GOODBYE:
            return self._plain("好，需要的时候再叫我。", answer_status="smalltalk", suggestions=[])
        if intent in it.ROUTABLE:
            return self._knowledge(identity, session, workspace, text, decision)
        if intent == it.CHITCHAT:
            return self._chitchat(session, text)
        return self._plain(
            self.reply_text("out_of_scope"),
            answer_status="out_of_scope",
            suggestions=["你能做什么？", *self.config.greeting_examples[:1]],
        )

    def _greeting(self, session) -> dict:
        examples = "；".join(self.config.greeting_examples)
        reply = (
            "你好，我是这个知识库的助手。制度、流程、时限、权限这类问题我都能查，"
            "回答会带上原文出处，看不准时我会直接说不确定，不编。\n"
            f"常被问到的是：{examples}。你想先看哪一条？"
        )
        return self._plain(
            reply,
            answer_status="smalltalk",
            suggestions=[*self.config.greeting_examples, "你能做什么？"],
        )

    def _capability(self, session) -> dict:
        reply = (
            "我能做四件事：\n"
            "1) 按你的权限范围查制度文档，回答带出处（文档、版本、字符偏移）；\n"
            "2) 接住追问，比如“那要几天”“这个谁审批”，会先补全指代再检索；\n"
            "3) 查不到时明确说查不到，并把问题记进未解决问题清单，供制度负责人补文档；\n"
            "4) 你只看得到自己身份允许的文档，看不到的直接说看不到，不猜内容。\n"
            "我不会做的是：编制度、跨权限看文档、替你走审批。"
        )
        return self._plain(
            reply,
            answer_status="smalltalk",
            suggestions=self.config.greeting_examples,
        )

    def _chitchat(self, session, text) -> dict:
        history = self.store.history(session["id"], limit=4)
        try:
            reply = self.llm.complete(
                SMALL_TALK_SYSTEM, text, max_tokens=160, temperature=0.3, history=history
            ).strip()
        except KBError:
            reply = SMALL_TALK_FALLBACK
        if not reply:
            reply = SMALL_TALK_FALLBACK
        return self._plain(
            reply,
            answer_status="smalltalk",
            suggestions=self.config.greeting_examples[:2],
        )

    def _knowledge(self, identity, session, workspace, text, decision) -> dict:
        question = decision.query or text
        kb_error: KBError | None = None
        try:
            result = self.kb.query(identity, workspace, question)
        except KBError as exc:
            kb_error = exc
            result = {}
        if kb_error is not None and kb_error.status in (401, 403):
            self.store.add_unresolved(
                session["id"], identity, workspace, text, "no_permission", question
            )
            return self._plain(
                self.reply_text("no_permission"),
                answer_status="no_permission",
                suggestions=["你能做什么？", *self.config.greeting_examples[:1]],
            )
        if kb_error is not None:
            raise kb_error

        status = result.get("status")
        if status != "answered" and self.config.retry_on_insufficient and question != text:
            retry = self.kb.query(identity, workspace, text)
            if retry.get("status") == "answered":
                result = retry
                status = "answered"
                decision.notes.append("rewrite_retry_with_original")

        citations = _cite_payload(result.get("citations") or [])
        if status == "answered" and citations:
            titles = []
            for item in citations:
                if item.get("title") and item["title"] not in titles:
                    titles.append(item["title"])
            return {
                "reply": result.get("answer", ""),
                "answer_status": "answered",
                "answer_mode": result.get("answer_mode"),
                "citations": citations,
                "trace": result.get("trace"),
                "suggestions": [f"{t} 里还提到什么？" for t in titles[:2]]
            }

        if status == "knowledge_unavailable":
            # 检索网关 / 本地模型没起来时，不能把它当成「库里没有这条内容」：
            # 既不写未解决台账（那是补语料的信号），也不说「换个说法再问」。
            return self._plain(
                self.reply_text("unavailable"),
                answer_status="knowledge_unavailable",
                suggestions=list(self.config.greeting_examples[:1]),
                extra={"trace": result.get("trace")},
            )

        unresolved_id = self.store.add_unresolved(
            session["id"], identity, workspace, text, "below_threshold", question
        )
        return self._plain(
            self.reply_text("refusal"),
            answer_status="insufficient_knowledge",
            suggestions=list(self.config.greeting_examples[:1]),
            extra={"unresolved_id": unresolved_id, "trace": result.get("trace")},
        )

    @staticmethod
    def _plain(reply: str, *, answer_status: str, suggestions: list[str], extra: dict | None = None) -> dict:
        payload = {
            "reply": reply,
            "answer_status": answer_status,
            "citations": [],
            "trace": None,
            "suggestions": [s for s in dict.fromkeys(suggestions) if s],
        }
        if extra:
            payload.update(extra)
        return payload
