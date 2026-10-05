import json
import logging
import sqlite3
from time import perf_counter
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ekb.catalog import Catalog
from ekb.config import Config, load_config
from ekb.errors import KBError
from ekb.retrieval import Engine

logger = logging.getLogger("ekb")


class BodyLimit:
    def __init__(self, app, limit=250_000):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.limit:
                rid = scope.setdefault("state", {}).setdefault(
                    "request_id", uuid4().hex
                )
                await JSONResponse(
                    {"error": {"code": "KB.REQUEST_TOO_LARGE"}, "request_id": rid},
                    413,
                    headers={"X-Request-ID": rid},
                )(scope, receive, send)
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        consumed = False

        async def replay():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {
                "type": "http.request",
                "body": b"".join(chunks),
                "more_body": False,
            }

        await self.app(scope, replay, send)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateDocument(StrictBody):
    workspace: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=200_000)
    audience: Literal["all", "internal"] = "all"
    filename: str = Field(default="source.md", max_length=200)


class Revision(StrictBody):
    text: str = Field(min_length=1, max_length=200_000)
    expected_version: int = Field(ge=1, strict=True)
    filename: str = Field(default="source.md", max_length=200)


class Archive(StrictBody):
    expected_version: int = Field(ge=1, strict=True)


class Question(StrictBody):
    workspace: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    question: str = Field(min_length=1, max_length=2000)
    mode: Literal["bm25", "dense", "hybrid"] = "bm25"


def create_app(config: Config | None = None) -> FastAPI:
    logger.setLevel(logging.INFO)
    if not any(getattr(handler, "_ekb_console", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler._ekb_console = True
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    config = config or load_config()
    catalog = Catalog(config.database)
    embedder = config.provider(config.embedding)
    engine = Engine(
        catalog,
        embedder,
        config.provider(config.generation),
        config.provider(config.reranking),
    )
    app = FastAPI(title="Enterprise Knowledge Base", version="0.1.0", debug=False)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"],
    )
    app.add_middleware(BodyLimit)
    app.state.catalog = catalog

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid4().hex
        started = perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        logger.info(
            json.dumps(
                {
                    "event": "request",
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "status": response.status_code,
                    "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                }
            )
        )
        return response

    def error_response(request, code, status):
        rid = getattr(request.state, "request_id", uuid4().hex)
        return JSONResponse(
            {"error": {"code": code}, "request_id": rid},
            status,
            headers={"X-Request-ID": rid},
        )

    @app.exception_handler(KBError)
    async def kb_error(request, exc):
        return error_response(request, exc.code, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error_response(request, "KB.INVALID_INPUT", 422)

    @app.exception_handler(sqlite3.OperationalError)
    async def storage_error(request, exc):
        return error_response(request, "KB.STORAGE_UNAVAILABLE", 503)

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return error_response(request, "KB.INTERNAL", 500)

    def principal(authorization: str | None = Header(default=None)):
        if (
            not authorization
            or not authorization.startswith("Bearer ")
            or len(authorization) > 512
        ):
            raise KBError("KB.UNAUTHENTICATED", 401)
        return config.authenticate(authorization[7:])

    @app.get("/health")
    def health():
        with catalog.store.connection() as c:
            c.execute("SELECT 1").fetchone()
        return {
            "status": "ok",
            "generation": "self_hosted_llm" if config.generation else "extractive",
            "embedding_configured": bool(config.embedding),
        }

    @app.post("/documents", status_code=201)
    def create(body: CreateDocument, request: Request, actor=Depends(principal)):
        return catalog.create(
            actor,
            body.workspace,
            body.title,
            body.text,
            body.audience,
            body.filename,
            request.state.request_id,
        )

    @app.get("/documents")
    def documents(
        workspace: str = Query(min_length=1, max_length=64), actor=Depends(principal)
    ):
        return {"items": catalog.list_documents(actor, workspace)}

    @app.get("/documents/{document_id}")
    def detail(document_id: int, actor=Depends(principal)):
        return catalog.detail(actor, document_id)

    @app.post("/documents/{document_id}/versions")
    def version(
        document_id: int, body: Revision, request: Request, actor=Depends(principal)
    ):
        return catalog.add_version(
            actor,
            document_id,
            body.text,
            body.expected_version,
            body.filename,
            request.state.request_id,
        )

    @app.post("/documents/{document_id}/versions/{version}/ingest")
    def ingest(
        document_id: int, version: int, request: Request, actor=Depends(principal)
    ):
        return catalog.ingest(
            actor, document_id, version, embedder, request.state.request_id
        )

    @app.post("/documents/{document_id}/archive")
    def archive(
        document_id: int, body: Archive, request: Request, actor=Depends(principal)
    ):
        return catalog.archive(
            actor, document_id, body.expected_version, request.state.request_id
        )

    @app.post("/query")
    def query(body: Question, request: Request, actor=Depends(principal)):
        result = engine.query(actor, body.workspace, body.question, body.mode)
        result["request_id"] = request.state.request_id
        return result

    return app
