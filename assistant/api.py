"""HTTP surface of the assistant service.

Endpoints are the ones an agent desk actually needs: chat, transcript, feedback,
and the unanswered-question backlog that feeds corpus work. Identity arrives
as X-Identity from the same-origin console proxy; the assistant maps it to a KB token.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ekb.errors import KBError

from assistant.kb import KBClient
from assistant.llm import ChatModel
from assistant.service import Assistant
from assistant.settings import Settings
from assistant.store import Store


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChatBody(StrictBody):
    text: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)
    workspace: str | None = Field(default=None, max_length=64)


class FeedbackBody(StrictBody):
    message_id: int = Field(ge=1, strict=True)
    value: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=500)


def create_app(settings: Settings | None = None, config_path: str | None = None) -> FastAPI:
    settings = settings or Settings.load(config_path or Path("assistant/config.json"))
    store = Store(Path(settings.database))
    kb = KBClient(settings.kb.base_url, settings.kb.tokens)
    llm = ChatModel(settings.llm.base_url, settings.llm.model, timeout=settings.llm.timeout)
    assistant = Assistant(store, kb, llm, settings.assistant)

    app = FastAPI(title="Enterprise Knowledge Assistant", version="0.1.0", debug=False)
    app.state.assistant = assistant

    def identity(x_identity: str | None = Header(default=None)) -> str:
        if not x_identity or x_identity not in kb.tokens:
            raise KBError("ASSISTANT.UNKNOWN_IDENTITY", 401)
        return x_identity

    @app.exception_handler(KBError)
    async def kb_error(request: Request, exc: KBError):
        return JSONResponse({"error": {"code": exc.code}}, exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": {"code": "ASSISTANT.INVALID_INPUT"}}, 422)

    @app.exception_handler(Exception)
    async def internal(request: Request, exc: Exception):
        return JSONResponse({"error": {"code": "ASSISTANT.INTERNAL"}}, 500)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "kb": kb.health(),
            "llm_model": settings.llm.model,
            "identities": kb.identities(),
        }

    @app.post("/chat")
    def chat(body: ChatBody, actor: str = Depends(identity)):
        result = assistant.chat(
            actor, body.text, session_id=body.session_id, workspace=body.workspace
        )
        result["identity"] = actor
        return result

    @app.get("/sessions/{session_id}")
    def session(session_id: str, actor: str = Depends(identity)):
        row = store.session(session_id)
        if row is None or row["subject"] != actor:
            raise KBError("ASSISTANT.SESSION_NOT_FOUND", 404)
        return {"session": row, "messages": store.transcript(session_id)}

    @app.post("/feedback", status_code=201)
    def feedback(body: FeedbackBody, actor: str = Depends(identity)):
        return assistant.feedback(body.message_id, body.value, body.comment)

    @app.get("/unresolved")
    def unresolved(limit: int = 50, actor: str = Depends(identity)):
        return {"items": store.unresolved(limit=max(1, min(limit, 200)))}

    @app.get("/stats")
    def stats(actor: str = Depends(identity)):
        return store.stats()

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enterprise knowledge assistant service")
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log", default=None)
    args = parser.parse_args(argv)
    import uvicorn

    uvicorn.run(
        create_app(config_path=args.config),
        host="127.0.0.1",
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
