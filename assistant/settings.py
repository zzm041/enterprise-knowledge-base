"""Assistant process settings, loaded from a JSON file the console writes at startup."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from assistant.service import AssistantConfig


@dataclass
class LLMSettings:
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "qwen3:8b-q4km"
    timeout: float = 120.0


@dataclass
class KBSettings:
    base_url: str = "http://127.0.0.1:8000"
    tokens: dict[str, str] = field(default_factory=dict)


@dataclass
class Settings:
    database: str = "runtime/assistant.db"
    workspace: str = "handbook"
    kb: KBSettings = field(default_factory=KBSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    assistant: AssistantConfig = field(default_factory=AssistantConfig)

    @classmethod
    def load(cls, path: str | Path) -> "Settings":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        kb = KBSettings(**data.get("kb", {}))
        llm = LLMSettings(**data.get("llm", {}))
        assistant_raw = data.get("assistant", {})
        assistant = AssistantConfig(
            workspace=data.get("workspace", assistant_raw.pop("workspace", "handbook")),
            max_history=int(assistant_raw.pop("max_history", 6)),
            retry_on_insufficient=bool(assistant_raw.pop("retry_on_insufficient", True)),
            greeting_examples=list(
                assistant_raw.pop(
                    "greeting_examples", AssistantConfig().greeting_examples
                )
            ),
            replies={
                str(k): str(v)
                for k, v in (assistant_raw.pop("replies", {}) or {}).items()
                if str(v).strip()
            },
        )
        return cls(
            database=data.get("database", "runtime/assistant.db"),
            workspace=assistant.workspace,
            kb=kb,
            llm=llm,
            assistant=assistant,
        )
