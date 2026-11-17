from assistant.store import Store


def test_session_and_history_are_persisted(tmp_path):
    store = Store(tmp_path / "assistant.db")
    session_id = store.create_session("acme", "reader", "handbook")
    store.add_message(session_id, "user", "你好")
    store.add_message(session_id, "assistant", "你好，我是助手", intent="greeting")
    store.touch(session_id)

    history = store.history(session_id)
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert store.session(session_id)["turns"] == 1
    assert len(store.transcript(session_id)) == 2


def test_unresolved_backlog_tracks_open_items(tmp_path):
    store = Store(tmp_path / "assistant.db")
    session_id = store.create_session("acme", "reader", "handbook")
    store.add_unresolved(session_id, "acme/reader", "handbook", "年假有几天", "below_threshold")
    items = store.unresolved()
    assert len(items) == 1
    assert items[0]["question"] == "年假有几天"
    assert items[0]["resolved_at"] is None


def test_stats_counts_feedback_and_unresolved(tmp_path):
    store = Store(tmp_path / "assistant.db")
    session_id = store.create_session("acme", "reader", "handbook")
    message_id = store.add_message(session_id, "assistant", "回答", intent="knowledge")
    store.add_feedback(message_id, "up")
    store.add_feedback(message_id, "down", "答非所问")
    store.add_unresolved(session_id, "acme/reader", "handbook", "报销标准", "below_threshold")

    stats = store.stats()
    assert stats["feedback_positive"] == 1
    assert stats["feedback_negative"] == 1
    assert stats["unresolved_open"] == 1
    assert stats["sessions"] == 1
