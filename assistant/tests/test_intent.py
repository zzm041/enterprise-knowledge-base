from assistant import intent as it
from assistant.llm import parse_json_object


class FakeLLM:
    def __init__(self, payload=None, raw=None):
        self.payload = payload or {}
        self.raw = raw
        self.calls = 0

    def json(self, system, user, *, max_tokens=512, history=None):
        self.calls += 1
        if self.raw is not None:
            return parse_json_object(self.raw)
        return self.payload

    def complete(self, system, user, **kwargs):
        self.calls += 1
        return "你好"


def test_greeting_is_routed_by_rule_without_model():
    llm = FakeLLM()
    decision = it.classify("你好", llm, [])
    assert decision.intent == it.GREETING
    assert decision.source == "rule"
    assert llm.calls == 0


def test_greeting_with_punctuation_and_spacing():
    for text in ("您好！", " 在吗？ ", "Hello", "早上好~"):
        assert it.classify(text, FakeLLM(), []).intent == it.GREETING


def test_thanks_and_goodbye_short_circuit():
    assert it.classify("谢谢！", FakeLLM(), []).intent == it.THANKS
    assert it.classify("再见", FakeLLM(), []).intent == it.GOODBYE


def test_capability_question_is_not_treated_as_knowledge():
    assert it.classify("你能做什么？", FakeLLM(), []).intent == it.CAPABILITY
    assert it.classify("怎么用", FakeLLM(), []).intent == it.CAPABILITY


def test_handoff_request_is_routed_out_of_scope():
    # 没有真人坐席：这类诉求归到能力边界外，不再有 handoff 意图。
    decision = it.classify("转人工", FakeLLM(), [])
    assert decision.intent == it.OUT_OF_SCOPE
    assert decision.source == "rule"


def test_small_talk_marker_without_rule_match():
    assert it.classify("你是谁呀", FakeLLM(), []).intent == it.CHITCHAT


def test_knowledge_question_goes_to_the_model_router():
    llm = FakeLLM({"intent": "knowledge", "standalone_question": "年假有几天", "reason": "制度问题"})
    decision = it.classify("年假有几天", llm, [])
    assert llm.calls == 1
    assert decision.intent == it.KNOWLEDGE
    assert decision.query == "年假有几天"
    assert decision.source == "model"


def test_followup_gets_rewritten_into_standalone_question():
    llm = FakeLLM(
        {
            "intent": "followup",
            "standalone_question": "出差住宿报销标准是多少",
            "reason": "承接上一轮",
        }
    )
    history = [
        {"role": "user", "content": "出差住宿怎么订酒店"},
        {"role": "assistant", "content": "按制度先审批后预订。"},
    ]
    decision = it.classify("那报销标准呢", llm, history)
    assert decision.intent == it.FOLLOWUP
    assert decision.query == "出差住宿报销标准是多少"


def test_router_failure_falls_back_to_retrieval_instead_of_dropping_the_turn():
    class Broken:
        def json(self, *args, **kwargs):
            raise RuntimeError("model down")

    decision = it.classify("年假有几天", Broken(), [])
    assert decision.intent == it.KNOWLEDGE
    assert decision.source == "default"
    assert decision.query == "年假有几天"
    assert "router_failed_fallback_to_retrieval" in decision.notes


def test_unknown_intent_from_model_is_treated_as_knowledge():
    llm = FakeLLM({"intent": "weather", "standalone_question": "", "reason": ""})
    assert it.classify("明天几度", llm, []).intent == it.KNOWLEDGE


def test_parse_json_object_handles_fences_and_prose():
    fence = chr(96) * 3
    raw = fence + 'json\n{"intent": "greeting"}\n' + fence
    assert parse_json_object(raw) == {"intent": "greeting"}
    assert parse_json_object('结果如下：{"intent": "thanks"} 完毕') == {"intent": "thanks"}
