"""Minimal OpenAI-compatible chat client used for routing, rewriting and small talk."""

from __future__ import annotations

import json
from typing import Any

import httpx

from ekb.errors import KBError


class ChatModel:
    """Talks to any OpenAI-compatible /chat/completions endpoint (the demo gateway,
    Ollama, vLLM, or a hosted vendor endpoint — only the config differs)."""

    def __init__(self, base_url: str, model: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int = 512,
        temperature: float = 0.0,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        for turn in history or []:
            if turn.get("role") in ("user", "assistant") and turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": user})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions", json=payload
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:  # provider down: caller decides the fallback
            raise KBError("ASSISTANT.MODEL_UNAVAILABLE", 502) from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise KBError("ASSISTANT.MODEL_INVALID_OUTPUT", 502) from exc
        if not isinstance(content, str) or not content.strip():
            raise KBError("ASSISTANT.MODEL_INVALID_OUTPUT", 502)
        return content

    def json(self, system: str, user: str, *, max_tokens: int = 512,
             history: list[dict[str, str]] | None = None) -> dict:
        raw = self.complete(system, user, json_mode=True, max_tokens=max_tokens, history=history)
        return parse_json_object(raw)


def parse_json_object(raw: str) -> dict:
    """Models in the wild fence their JSON or wrap it in prose; recover the object."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if text.count("```") >= 2 else text.strip("`")
        text = text.removeprefix("json").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise KBError("ASSISTANT.MODEL_INVALID_OUTPUT", 502) from None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise KBError("ASSISTANT.MODEL_INVALID_OUTPUT", 502) from exc
    if not isinstance(value, dict):
        raise KBError("ASSISTANT.MODEL_INVALID_OUTPUT", 502)
    return value
