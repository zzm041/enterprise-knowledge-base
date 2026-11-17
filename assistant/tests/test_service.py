import json

from assistant import intent as it
from assistant.service import Assistant, AssistantConfig
from assistant.store import Store


class FakeLLM:
    def __init__(self, replies=None, chat="我在。想查哪条制度？"):
        self.replies = list(replies or [])
        self.chat = chat
        self.calls = []

    def json(self, system, user, *, max_tokens=512, history=None):
        self.calls.append(("json", user))
        if self.replies:
            return self.replies.pop(0)
        return {"intent": "knowledge", "standalone_question": user.splitlines()[-1], "reason": ""}

    def complete(self, system, user, **kwargs):
        self.calls.append(("complete", user))
        return self.chat


class FakeKB:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.queries = []

    def query(self, identity, workspace, question, mode="auto"):
        self.queries.append((identity, workspace, question))
        for key, value in self.answers.items():
            if key in question:
                if isinstance(value, Exception):
                    raise value
                return value
        return {"status": "insufficient_knowledge", "citations": [], "trace": {"reason": "below_relevance"}}

    def health(self):
        return True

    def identities(self):
        return ["acme/reader"]


ANSWERED = {
    "status": "answered",
    "answer": "新员工在入职当天到信息服务台领取办公电脑。",
    "answer_mode": "generative",
    "citations": [
        {
            "chunk_id": 10,
            "document_id": 9,
            "title": "入职设备领取",
            "version": 1,
            "start": 0,
            "end": 43,
            "excerpt": "新员工在入职当天到信息服务台领取办公电脑。",
        }
    ],
    "trace": {"retrieval_mode": "hybrid", "rerank": "applied"},
}


def test_replies_are_configurable(tmp_path):
    """兜底话术可覆盖：企业部署换成自己的口吻（默认值仍可用）。"""
    config = AssistantConfig(replies={"refusal": "这条库里没有，已登记待补。", "out_of_scope": "投诉请走工单系统。"})
    assistant, _ = build(tmp_path, config=config)
    refused = assistant.chat("acme/reader", "员工购房无息贷款怎么申请")
    assert refused["answer_status"] == "insufficient_knowledge"
    assert refused["reply"] == "这条库里没有，已登记待补。"
    off = assistant.chat("acme/reader", "我要投诉")
    assert off["answer_status"] == "out_of_scope"
    assert off["reply"] == "投诉请走工单系统。"
    # 没配的键仍走内置默认
    default_assistant, _ = build(tmp_path / "d")
    plain = default_assistant.chat("acme/reader", "员工购房无息贷款怎么申请")
    assert "未解决问题清单" in plain["reply"]


def build(tmp_path, kb=None, llm=None, config=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = Store(tmp_path / "assistant.db")
    return (
        Assistant(store, kb or FakeKB({"办公电脑": ANSWERED}), llm or FakeLLM(), config),
        store,
    )


def test_greeting_answers_with_capability_and_examples(tmp_path):
    assistant, store = build(tmp_path)
    result = assistant.chat("acme/reader", "你好")
    assert result["answer_status"] == "smalltalk"
    assert result["intent"] == it.GREETING
    assert "你好" in result["reply"]
    assert result["suggestions"]
    assert store.transcript(result["session_id"])[-1]["intent"] == "greeting"


def test_greeting_does_not_trigger_retrieval(tmp_path):
    kb = FakeKB()
    assistant, _ = build(tmp_path, kb=kb)
    assistant.chat("acme/reader", "在吗")
    assert kb.queries == []


def test_chitchat_is_answered_by_the_model_without_kb_claims(tmp_path):
    assistant, _ = build(tmp_path, llm=FakeLLM(chat="我挺好，正事是帮你查制度。"))
    result = assistant.chat("acme/reader", "你是谁呀")
    assert result["answer_status"] == "smalltalk"
    assert result["reply"].startswith("我挺好")


def test_chitchat_falls_back_when_the_model_is_down(tmp_path):
    class Broken:
        def json(self, *a, **k):
            raise RuntimeError

        def complete(self, *a, **k):
            raise RuntimeError

    from ekb.errors import KBError

    class Down:
        def json(self, *a, **k):
            raise KBError("ASSISTANT.MODEL_UNAVAILABLE", 502)

        def complete(self, *a, **k):
            raise KBError("ASSISTANT.MODEL_UNAVAILABLE", 502)

    assistant = Assistant(Store(tmp_path / "a.db"), FakeKB(), Down())
    result = assistant.chat("acme/reader", "你是谁呀")
    assert result["answer_status"] == "smalltalk"
    assert "查公司制度" in result["reply"]


def test_knowledge_answer_carries_citations(tmp_path):
    assistant, _ = build(tmp_path)
    result = assistant.chat("acme/reader", "入职当天在哪里领取办公电脑")
    assert result["answer_status"] == "answered"
    assert result["citations"][0]["document_id"] == 9
    assert "信息服务台" in result["reply"]
    assert any("入职设备领取" in item for item in result["suggestions"])


def test_insufficient_retrieval_refuses_and_logs_backlog(tmp_path):
    assistant, store = build(tmp_path, kb=FakeKB({"办公电脑": ANSWERED}))
    result = assistant.chat("acme/reader", "员工购房无息贷款怎么申请")
    assert result["answer_status"] == "insufficient_knowledge"
    assert result["citations"] == []
    assert "未解决问题清单" in result["reply"]
    backlog = store.unresolved()
    assert [row["question"] for row in backlog] == ["员工购房无息贷款怎么申请"]


def test_followup_rewrite_is_used_for_retrieval(tmp_path):
    llm = FakeLLM(
        [
            {"intent": "knowledge", "standalone_question": "入职当天在哪里领取办公电脑", "reason": ""},
            {"intent": "followup", "standalone_question": "办公电脑领取需要什么凭证", "reason": ""},
        ]
    )
    kb = FakeKB({"办公电脑": ANSWERED})
    assistant, _ = build(tmp_path, kb=kb, llm=llm)
    first = assistant.chat("acme/reader", "入职当天在哪里领取办公电脑")
    assistant.chat("acme/reader", "要带什么", session_id=first["session_id"])
    assert kb.queries[-1][2] == "办公电脑领取需要什么凭证"


def test_permission_error_becomes_a_clear_message_not_a_crash(tmp_path):
    from ekb.errors import KBError

    kb = FakeKB({"调薪": KBError("KB.FORBIDDEN", 403)})
    assistant, store = build(tmp_path, kb=kb)
    result = assistant.chat("acme/reader", "调薪窗口在几月")
    assert result["answer_status"] == "no_permission"
    assert "权限" in result["reply"]
    assert store.unresolved()[0]["reason"] == "no_permission"


def test_handoff_request_is_declined_as_out_of_scope(tmp_path):
    # 这一层没有真人坐席：投诉/要真人不承诺转接、不发工单号，明确落到能力边界外。
    assistant, store = build(tmp_path)
    result = assistant.chat("acme/reader", "我要投诉，转人工")
    assert result["answer_status"] == "out_of_scope"
    assert result["citations"] == []
    assert all("转人工" not in s for s in result["suggestions"])
    assert store.unresolved() == []


def test_out_of_scope_is_declined_with_scope_hint(tmp_path):
    llm = FakeLLM([{"intent": "out_of_scope", "standalone_question": "", "reason": ""}])
    assistant, _ = build(tmp_path, llm=llm)
    result = assistant.chat("acme/reader", "帮我写一段快速排序")
    assert result["answer_status"] == "out_of_scope"
    assert "范围" in result["reply"]


def test_session_is_scoped_to_the_identity(tmp_path):
    assistant, _ = build(tmp_path)
    mine = assistant.chat("acme/reader", "你好")
    other = assistant.chat("acme/staff", "你好", session_id=mine["session_id"])
    assert other["session_id"] != mine["session_id"]


def test_feedback_is_recorded(tmp_path):
    assistant, store = build(tmp_path)
    result = assistant.chat("acme/reader", "你好")
    assistant.feedback(result["message_id"], "up")
    assert store.stats()["feedback_positive"] == 1
