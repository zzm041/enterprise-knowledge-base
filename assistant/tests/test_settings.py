import json

from assistant.settings import Settings


def test_settings_loads_custom_replies(tmp_path):
    path = tmp_path / "assistant-config.json"
    path.write_text(
        json.dumps(
            {
                "database": "runtime/assistant.db",
                "workspace": "handbook",
                "kb": {"base_url": "http://127.0.0.1:8000", "tokens": {"acme/reader": "tok"}},
                "llm": {"base_url": "http://127.0.0.1:11434/v1", "model": "qwen3:8b-q4km"},
                "assistant": {
                    "replies": {
                        "refusal": "这条库里没有，已登记待补。",
                        "out_of_scope": "投诉请走工单系统。",
                        "blank": "   ",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    s = Settings.load(path)
    assert s.assistant.replies == {
        "refusal": "这条库里没有，已登记待补。",
        "out_of_scope": "投诉请走工单系统。",
    }
    assert s.llm.model == "qwen3:8b-q4km"


def test_settings_without_replies_use_defaults(tmp_path):
    path = tmp_path / "assistant-config.json"
    path.write_text(json.dumps({"database": "runtime/assistant.db"}), encoding="utf-8")
    s = Settings.load(path)
    assert s.assistant.replies == {}
