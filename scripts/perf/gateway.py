
"""Serving front for the frozen EKB API: response cache + admission control.

Why this exists: the measured /query cost is dominated by the generation step
(~2.7 s of ~3.2 s on CPU), and the upstream serialises that work, so concurrent
arrivals only queue. Two cheap, reversible levers sit outside the frozen package:

  * a normalized-question cache (demo/eval traffic repeats a small question set);
  * an admission gate that turns unbounded queueing into a bounded 429 with
    Retry-After, so p95 of *accepted* requests stops collapsing.

It proxies everything else untouched and never mutates the knowledge base.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from collections import OrderedDict

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

UPSTREAM = os.environ.get("EKB_UPSTREAM", "http://127.0.0.1:53122").rstrip("/")
MAX_INFLIGHT = int(os.environ.get("EKB_MAX_INFLIGHT", "4"))
QUEUE_TIMEOUT = float(os.environ.get("EKB_QUEUE_TIMEOUT", "0.2"))
CACHE_TTL = float(os.environ.get("EKB_CACHE_TTL", "900"))
CACHE_MAX = int(os.environ.get("EKB_CACHE_MAX", "2048"))

_PUNCT = re.compile(r"[\s\u3000\t\r\n,，.。!！?？;；:：、'\"“”‘’()（）\[\]【】<>《》\-—~·]+")
SEM = asyncio.Semaphore(MAX_INFLIGHT)
CACHE: "OrderedDict[str, tuple[float, bytes, int]]" = OrderedDict()
STATS = {"hit": 0, "miss": 0, "shed": 0, "passthrough": 0, "upstream_error": 0}


def normalise(question: str) -> str:
    return _PUNCT.sub("", question).lower()


def cache_key(body: dict, identity: str) -> str:
    raw = "|".join(
        [
            normalise(str(body.get("question", ""))),
            str(body.get("workspace", "")),
            str(body.get("mode", "auto")),
            identity,
        ]
    )
    return hashlib.sha256(raw.encode("utf8")).hexdigest()


def identity_of(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return hashlib.sha256(auth.encode("utf8")).hexdigest()[:16]


def cache_get(key: str):
    item = CACHE.get(key)
    if not item:
        return None
    stored_at, payload, status = item
    if time.time() - stored_at > CACHE_TTL:
        CACHE.pop(key, None)
        return None
    CACHE.move_to_end(key)
    return payload, status


def cache_put(key: str, payload: bytes, status: int):
    CACHE[key] = (time.time(), payload, status)
    while len(CACHE) > CACHE_MAX:
        CACHE.popitem(last=False)


async def query(request: Request):
    body = await request.json()
    ident = identity_of(request)
    if not isinstance(body, dict) or not body.get("question"):
        STATS["passthrough"] += 1
        return await forward(request, body, ident)
    key = cache_key(body, ident)
    hit = cache_get(key)
    if hit is not None:
        STATS["hit"] += 1
        payload, status = hit
        return JSONResponse(json.loads(payload), status_code=status, headers={"x-cache": "hit"})
    STATS["miss"] += 1
    try:
        async with asyncio.timeout(QUEUE_TIMEOUT):
            acquired = await SEM.acquire()
    except TimeoutError:
        STATS["shed"] += 1
        return JSONResponse(
            {"error": {"code": "KB.OVERLOADED", "detail": "too many concurrent questions"}},
            status_code=429,
            headers={"retry-after": "1", "x-cache": "shed"},
        )
    try:
        return await forward(request, body, ident, key)
    finally:
        SEM.release()


async def forward(request: Request, body, ident: str, key: str | None = None):
    headers = {k: v for k, v in request.headers.items() if k.lower() in ("authorization", "content-type", "accept")}
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(f"{UPSTREAM}{request.url.path}", json=body, headers=headers)
    except Exception as exc:  # noqa: BLE001
        STATS["upstream_error"] += 1
        return JSONResponse({"error": {"code": "KB.UPSTREAM_UNAVAILABLE", "detail": type(exc).__name__}}, status_code=503)
    payload = r.content
    if key and r.status_code == 200:
        cache_put(key, payload, r.status_code)
    return Response(payload, status_code=r.status_code, media_type="application/json",
                    headers={"x-cache": "miss" if key else "passthrough"})


async def passthrough(request: Request):
    STATS["passthrough"] += 1
    headers = {k: v for k, v in request.headers.items() if k.lower() in ("authorization", "content-type", "accept")}
    body = await request.body()
    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.request(request.method, f"{UPSTREAM}{request.url.path}",
                                 content=body or None, headers=headers,
                                 params=dict(request.query_params))
    return Response(r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"))


async def stats(_: Request):
    return JSONResponse({**STATS, "inflight_limit": MAX_INFLIGHT, "cache_size": len(CACHE),
                         "cache_ttl_s": CACHE_TTL})


async def health(_: Request):
    return JSONResponse({"status": "ok", "upstream": UPSTREAM})


app = Starlette(routes=[
    Route("/query", query, methods=["POST"]),
    Route("/__gw_stats", stats, methods=["GET"]),
    Route("/health", health, methods=["GET"]),
    Route("/{path:path}", passthrough, methods=["GET", "POST", "PUT", "DELETE", "HEAD"]),
])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("EKB_GW_PORT", "53150")), log_level="warning")
