"""Local RAG inference service for the EKB demo (demo layer, not product code).

ekb/providers.py speaks OpenAI-shaped HTTP. This process serves the three routes
it calls, all on one loopback port, so the frozen product can run a real
retrieval-augmented pipeline without any change to ekb/:

  POST /embeddings      -> bge-m3 (sentence-transformers, CPU)
  POST /rerank          -> bge-reranker-v2-m3 (CrossEncoder, CPU)
  POST /chat/completions-> forwards to the local Ollama /v1 endpoint

Two adaptations sit here on purpose, and only here:
  * local chat models like writing citation ids as strings ("3"); we coerce them
    back to ints, because ekb/answer.py validates list[int] in strict mode;
  * a small set of synonym statuses (success/ok -> answered) are folded, so a
    verbose local model cannot turn a good answer into knowledge_unavailable.

Nothing here fabricates evidence: /rerank only reorders what it is given and
/chat/completions only sees the evidence ekb/ selected.
"""

import argparse
import json
import math
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EMBED_DIMENSIONS = 1024
ANSWERED = "answered"
INSUFFICIENT = "insufficient_knowledge"
STATUS_SYNONYMS = {
    "answered": ANSWERED,
    "answer": ANSWERED,
    "success": ANSWERED,
    "ok": ANSWERED,
    "answered_with_citations": ANSWERED,
    "insufficient": INSUFFICIENT,
    "insufficient_knowledge": INSUFFICIENT,
    "no_answer": INSUFFICIENT,
    "unknown": INSUFFICIENT,
    "not_found": INSUFFICIENT,
}

EMBEDDER = None
RERANKER = None
EMBED_LOCK = threading.Lock()
RERANK_LOCK = threading.Lock()
STATE = {"embedding": "loading", "rerank": "loading", "detail": ""}


def _sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def load_models(embed_dir, rerank_dir):
    global EMBEDDER, RERANKER
    from sentence_transformers import CrossEncoder, SentenceTransformer

    started = time.perf_counter()
    EMBEDDER = SentenceTransformer(embed_dir, device="cpu")
    STATE["embedding"] = "ready"
    mid = time.perf_counter()
    RERANKER = CrossEncoder(rerank_dir, device="cpu")
    STATE["rerank"] = "ready"
    STATE["detail"] = (
        f"embed {mid - started:.1f}s, rerank {time.perf_counter() - mid:.1f}s"
    )
    print(json.dumps({"models_ready": STATE}, ensure_ascii=False), flush=True)


def normalise_answer(content):
    """Coerce a local model's JSON answer into the schema ekb/ validates."""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return content
    if not isinstance(parsed, dict):
        return content
    status = str(parsed.get("status", "")).strip().lower()
    parsed["status"] = STATUS_SYNONYMS.get(status, status)
    ids = parsed.get("citation_chunk_ids")
    if isinstance(ids, list):
        clean = []
        for item in ids:
            if isinstance(item, bool):
                continue
            if isinstance(item, int):
                clean.append(item)
            elif isinstance(item, str):
                match = re.match(r"^\s*(?:chunk[\s_#-]*)?#?\s*(\d+)\s*$", item, re.I)
                if match:
                    clean.append(int(match.group(1)))
        parsed["citation_chunk_ids"] = clean
    answer = parsed.get("answer")
    if not isinstance(answer, str):
        parsed["answer"] = "" if answer is None else str(answer)
    return json.dumps(parsed, ensure_ascii=False)


def ollama_messages(messages):
    """Translate OpenAI-style multimodal messages into Ollama native shape.

    ekb/ is text-only, so images only ever arrive through the assistant layer or
    the console OCR endpoint. Ollama wants content as a plain string plus an
    images list of base64 payloads (data: prefix stripped).
    """
    out = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        images = [item for item in (message.get("images") or []) if isinstance(item, str)]
        if isinstance(content, list):
            texts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    texts.append(str(part.get("text") or ""))
                elif part.get("type") == "image_url":
                    url = part.get("image_url")
                    if isinstance(url, dict):
                        url = url.get("url")
                    if isinstance(url, str) and url:
                        images.append(url.split(",", 1)[1] if url.startswith("data:") and "," in url else url)
            content = "\n".join(texts)
        item = {"role": message.get("role") or "user",
                "content": content if isinstance(content, str) else ""}
        if images:
            item["images"] = images
        out.append(item)
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ollama = "http://127.0.0.1:11434"
    chat_model = "qwen3:8b-q4km"
    allow_override = False
    model_allowlist: set = set()
    native_chat = True

    def log_message(self, *args):
        pass

    # ---------- plumbing ----------
    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 2_000_000:
            raise ValueError("body size")
        return json.loads(self.rfile.read(length))

    def _reply(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path not in ("/health", "/healthz"):
            return self._reply(404, {"error": "NOT_FOUND"})
        return self._reply(200, {
            "status": "ok" if STATE["embedding"] == "ready" else "loading",
            "embedding": STATE["embedding"],
            "rerank": STATE["rerank"],
            "chat_model": self.chat_model,
            "embedding_dimensions": EMBED_DIMENSIONS,
            "detail": STATE["detail"],
        })

    def do_POST(self):
        try:
            payload = self._body()
        except (ValueError, json.JSONDecodeError):
            return self._reply(400, {"error": "INVALID_BODY"})
        try:
            if self.path == "/v1/embeddings":
                return self._embeddings(payload)
            if self.path == "/v1/rerank":
                return self._rerank(payload)
            if self.path == "/v1/chat/completions":
                return self._chat(payload)
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller as 503
            return self._reply(503, {"error": "PROVIDER_FAILED", "detail": str(exc)})
        return self._reply(404, {"error": "NOT_FOUND"})

    # ---------- routes ----------
    def _embeddings(self, payload):
        texts = payload.get("input")
        if not isinstance(texts, list) or not texts or len(texts) > 64:
            return self._reply(400, {"error": "INVALID_INPUT"})
        if any(not isinstance(t, str) or not t.strip() for t in texts):
            return self._reply(400, {"error": "INVALID_INPUT"})
        if EMBEDDER is None:
            return self._reply(503, {"error": "MODEL_NOT_READY"})
        with EMBED_LOCK:
            vectors = EMBEDDER.encode(
                texts, normalize_embeddings=True, batch_size=8
            ).tolist()
        data = [
            {"object": "embedding", "index": i, "embedding": vector}
            for i, vector in enumerate(vectors)
        ]
        return self._reply(200, {"object": "list", "data": data,
                                 "model": payload.get("model", "bge-m3")})

    def _rerank(self, payload):
        query, documents = payload.get("query"), payload.get("documents")
        if not isinstance(query, str) or not query.strip():
            return self._reply(400, {"error": "INVALID_QUERY"})
        if not isinstance(documents, list) or not documents or len(documents) > 64:
            return self._reply(400, {"error": "INVALID_DOCUMENTS"})
        if any(not isinstance(d, str) for d in documents):
            return self._reply(400, {"error": "INVALID_DOCUMENTS"})
        if RERANKER is None:
            return self._reply(503, {"error": "MODEL_NOT_READY"})
        with RERANK_LOCK:
            raw = RERANKER.predict([(query, d) for d in documents])
        scores = [_sigmoid(float(x)) for x in raw]
        results = [
            {"index": i, "relevance_score": round(score, 6)}
            for i, score in enumerate(scores)
        ]
        return self._reply(200, {"results": results, "model": payload.get("model", "")})

    def _chat(self, payload):
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._reply(400, {"error": "INVALID_MESSAGES"})
        requested = str(payload.get("model") or "")
        model = (requested if self.allow_override and requested in self.model_allowlist
                 else self.chat_model)
        # Ollama 的原生 /api/chat 认 think=false；OpenAI 兼容层不认，而 Qwen3 系列的
        # 思考过程会把一问从 0.8s 拖到 15s。所以白名单模型走原生端点，再套回 OpenAI 形状。
        native = ollama_messages(messages)
        has_image = any(item.get("images") for item in native)
        if self.native_chat and model in self.model_allowlist:
            body = {"model": model, "messages": native, "stream": False, "think": False,
                    "options": {k: payload[k] for k in ("temperature", "top_p", "seed")
                                if k in payload}}
            request = urllib.request.Request(
                self.ollama.rstrip("/") + "/api/chat",
                data=json.dumps(body, ensure_ascii=False).encode("utf8"),
                method="POST",
            )
            request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    data = json.loads(response.read())
            except urllib.error.HTTPError as exc:
                return self._reply(exc.code, {"error": "UPSTREAM_HTTP",
                                              "detail": exc.read()[:400].decode("utf8", "replace")})
            except (urllib.error.URLError, TimeoutError) as exc:
                return self._reply(503, {"error": "UPSTREAM_UNAVAILABLE", "detail": str(exc)})
            message = data.get("message") or {}
            content = message.get("content") or ""
            # Reading an image returns free text; forcing the ekb JSON contract on it corrupts it.
            if not has_image:
                content = normalise_answer(content)
            return self._reply(200, {
                "model": model,
                "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": data.get("prompt_eval_count", 0),
                          "completion_tokens": data.get("eval_count", 0)},
            })
        upstream = dict(payload)
        upstream["model"] = model
        request = urllib.request.Request(
            self.ollama.rstrip("/") + "/v1/chat/completions",
            data=json.dumps(upstream, ensure_ascii=False).encode("utf8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return self._reply(exc.code, {"error": "UPSTREAM_HTTP",
                                          "detail": exc.read()[:400].decode("utf8", "replace")})
        except (urllib.error.URLError, TimeoutError) as exc:
            return self._reply(503, {"error": "UPSTREAM_UNAVAILABLE", "detail": str(exc)})
        try:
            choice = data["choices"][0]
            choice["message"]["content"] = normalise_answer(choice["message"]["content"])
        except (KeyError, IndexError, TypeError):
            return self._reply(502, {"error": "INVALID_UPSTREAM_OUTPUT"})
        return self._reply(200, data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--embed-dir", required=True)
    parser.add_argument("--rerank-dir", required=True)
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--chat-model", default="qwen3:8b-q4km")
    parser.add_argument("--allow-model-override", action="store_true")
    parser.add_argument("--model-allowlist", default="")
    args = parser.parse_args()

    Handler.ollama = args.ollama
    Handler.chat_model = args.chat_model
    Handler.allow_override = bool(args.allow_model_override)
    Handler.model_allowlist = {x.strip() for x in args.model_allowlist.split(",") if x.strip()}
    if not Handler.model_allowlist and "qwen3" in args.chat_model:
        # 单独跑网关时也要走原生端点：OpenAI 兼容层不认 think=false，Qwen3 会白烧 15 秒
        Handler.model_allowlist = {args.chat_model}
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=load_models, args=(args.embed_dir, args.rerank_dir), daemon=True
    )
    thread.start()
    print(json.dumps({"rag_service": f"http://127.0.0.1:{args.port}",
                      "chat_model": args.chat_model}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
