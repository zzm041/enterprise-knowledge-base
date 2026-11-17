"""Interactive demo console for the frozen Enterprise Knowledge Base build.

Starts a real Uvicorn process on a loopback port (the same code path the recorded
HTTP evidence came from), seeds a fresh synthetic catalog from
evaluation/corpus.json, and serves a single-page console that drives it over HTTP
through a same-origin proxy. Nothing under ekb/ is modified; every artefact of a
run lives in runtime/console-<id>/.

    python scripts/console.py            # open the console in the default browser
    python scripts/console.py --no-browser --port 8770
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request as UrlRequest, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ekb.catalog import Catalog  # noqa: E402
from ekb.cli import initialize  # noqa: E402
from ekb.evaluate import populate  # noqa: E402
from ekb.providers import ModelHTTP  # noqa: E402
from pipeline.collect import run_all as run_collect  # noqa: E402
from pipeline.export import csv_bytes, docx_bytes, md_bytes, safe_name, zip_bytes  # noqa: E402
from pipeline.parse import UnsupportedFile, parse_bytes  # noqa: E402
from pipeline.store import FileStore  # noqa: E402

PAGE = ROOT / "demo" / "console.html"
LOCK = threading.Lock()
FILE_STORE: FileStore | None = None

# 对外演示（tunnel / 公网）用的开关。默认关闭：本机演示保留四个身份可自由切换，
# 因为那是给人看权限差异用的。一旦开公网，X-Identity 这种「客户端说自己是谁」的
# 头就不能再当权限依据——否则任何访客改一个头就是 editor。--public 下：
# 身份由服务端钉死、管理端点关闭、写操作一律 403、按 IP 限流。
PUBLIC_MODE = False
PUBLIC_IDENTITY = "reader"
PUBLIC_TOKEN = ""
PUBLIC_RATE_LIMIT = 30          # 每个 IP 每分钟的提问次数
_RATE: dict[str, list[float]] = {}
RATE_LOCK = threading.Lock()

# 本机已有的真实模型资产（不下载、不联网）：
# - bge-m3 / bge-reranker-v2-m3 的 sentence-transformers 权重
# - 装了 torch 的解释器（EKB 自己的 .venv 没有 torch，故复用一个独立 venv）
MODELS_HF = Path("E:/ai-models/property-ai/models-hf")
BGE_PYTHON = Path("D:/property-ai/tmp/selfhost-ai-runtime/bge-142-cpu/.venv/Scripts/python.exe")
RAG_SCRIPT = ROOT / "scripts" / "rag_service.py"
EMBED_MODEL = "bge-m3"
RERANK_MODEL = "bge-reranker-v2-m3"
CHAT_MODEL = "qwen3:8b-q4km"
EMBED_DIMENSIONS = 1024

# 多模态档：读图（扫描件 / 截图）走它，文本问答仍走默认生成档。
VISION_MODEL = "qwen3.5:9b-vl-q4km"
OLLAMA_BASE = "http://127.0.0.1:11434"
OCR_PROMPT = (
    "把这张图里的全部文字按阅读顺序转写成 Markdown："
    "保留标题层级、列表与表格（表格用 Markdown 表格）；只输出转写结果，不要解释或总结。"
)

# 可切换的生成模型档位。qwen3.5 / qwen3 的权重从 ModelScope 取 GGUF（registry.ollama.ai 在本机不通），
# 用 ollama create 建成下面的名字；没建的档位会在 /api/__models 里显示 available=False。
# 只保留两档：文本问答用 qwen3:8b，读图/扫描件用 qwen3.5:9b-vl。
# Qwen2.5-14B 与 Qwen3.5-9B 文本档已下线（2026-11-16 按用户要求）；它们的权重文件与
# 历史评测报告都留在原处，不删、不动，只是不再出现在可选档位里。
MODEL_PROFILES: tuple[dict, ...] = (
    {'key': 'qwen3:8b-q4km', 'label': 'Qwen3-8B（文本默认）',
     'note': 'q4_K_M · 5.0GB · 32K 上下文 · 28 题零坏例、负向 8/8、1.6s'},
    {'key': 'qwen3.5:9b-vl-q4km', 'label': 'Qwen3.5-9B-VL（多模态 · 2026-02）',
     'note': 'q4_K_M + clip 投影 · 6.6GB · 最新多模态，读图/扫描件用它；切过去会自动卸掉文本档'},
)
CHAT_MODEL_CURRENT = CHAT_MODEL
CONFIG_PATH: Path | None = None
ASSISTANT_CONFIG: Path | None = None
ASSISTANT_MODULE = "assistant.api"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Api:
    """Owns the Uvicorn child process so the console can restart it for real."""

    def __init__(self, config_path: Path, port: int, log_path: Path) -> None:
        self.config_path = config_path
        self.port = port
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None
        self.base = f"http://127.0.0.1:{port}"

    def start(self) -> None:
        env = {**os.environ, "EKB_CONFIG": str(self.config_path), "PYTHONUNBUFFERED": "1"}
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "ekb.api:create_app", "--factory",
                "--host", "127.0.0.1", "--port", str(self.port), "--no-access-log",
            ],
            cwd=ROOT, env=env,
            stdout=self.log_path.open("a", encoding="utf8"),
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        for _ in range(200):
            if self.proc.poll() is not None:
                raise SystemExit(f"API exited during startup; see {self.log_path}")
            try:
                with urlopen(f"{self.base}/health", timeout=1) as resp:
                    if resp.status == 200:
                        return
            except (URLError, HTTPError, TimeoutError):
                pass
            time.sleep(0.1)
        raise SystemExit("API did not become healthy in 20s")

    def stop(self) -> None:
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)
        self.proc = None

    def restart(self) -> float:
        started = time.perf_counter()
        self.stop()
        self.start()
        return round((time.perf_counter() - started) * 1000, 1)


def snapshot_dir(models_hf: Path, name: str) -> Path | None:
    """Locate <models_hf>/<name>/<revision>/ the way huggingface_hub lays it out."""
    base = models_hf / name
    if not base.is_dir():
        return None
    for candidate in sorted(base.iterdir()):
        if (candidate / "config.json").exists():
            return candidate
    return None


class RagService:
    """Owns the local model gateway: bge-m3 embeddings, bge-reranker, chat passthrough.

    ekb/providers.py only speaks three routes (/embeddings, /rerank, /chat/completions)
    and refuses non-loopback hosts; scripts/rag_service.py serves exactly those on one
    port, so the frozen product code talks to a real model stack unchanged.
    """

    def __init__(self, port: int, log_path: Path, *, models_hf: Path, ollama: str,
                 chat_model: str, embed_model: str = EMBED_MODEL,
                 rerank_model: str = RERANK_MODEL) -> None:
        self.port = port
        self.log_path = log_path
        self.models_hf = models_hf
        self.ollama = ollama
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.rerank_model = rerank_model
        self.proc: subprocess.Popen | None = None
        self.embed_dir = snapshot_dir(models_hf, embed_model)
        self.rerank_dir = snapshot_dir(models_hf, rerank_model)
        self.detail = ""

    @property
    def available(self) -> bool:
        return bool(
            self.embed_dir and self.rerank_dir and BGE_PYTHON.exists() and RAG_SCRIPT.exists()
        )

    def start(self, wait_seconds: float = 300.0) -> bool:
        if not self.available:
            missing = []
            if not self.embed_dir:
                missing.append(f"{self.models_hf / self.embed_model}")
            if not self.rerank_dir:
                missing.append(f"{self.models_hf / self.rerank_model}")
            if not BGE_PYTHON.exists():
                missing.append(str(BGE_PYTHON))
            if not RAG_SCRIPT.exists():
                missing.append(str(RAG_SCRIPT))
            self.detail = "missing: " + ", ".join(missing)
            return False
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
               "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("ab")
        self.proc = subprocess.Popen(
            [
                str(BGE_PYTHON), str(RAG_SCRIPT),
                "--port", str(self.port),
                "--embed-dir", str(self.embed_dir),
                "--rerank-dir", str(self.rerank_dir),
                "--ollama", self.ollama,
                "--chat-model", self.chat_model,
                "--allow-model-override",
                "--model-allowlist", ",".join(p["key"] for p in MODEL_PROFILES),
            ],
            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
        )
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.detail = f"rag_service exited with code {self.proc.returncode}; see {self.log_path}"
                return False
            info = self.health()
            if info and info.get("status") == "ok":
                self.detail = str(info.get("detail") or "")
                return True
            time.sleep(0.5)
        self.detail = f"rag_service did not become healthy within {wait_seconds:.0f}s"
        return False

    def health(self) -> dict | None:
        try:
            with urlopen(f"http://127.0.0.1:{self.port}/health", timeout=3) as resp:
                return json.loads(resp.read())
        except Exception:  # noqa: BLE001 - not started yet, or still loading weights
            return None

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(15)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def info(self) -> dict:
        return {
            "enabled": self.proc is not None and self.proc.poll() is None,
            "port": self.port,
            "embedding_model": self.embed_model,
            "rerank_model": self.rerank_model,
            "chat_model": self.chat_model,
            "embedding_dimensions": EMBED_DIMENSIONS,
            "detail": self.detail,
        }


class AssistantService:
    """Runs the assistant service (assistant/) as its own process.

    The assistant is a separate deployable: it owns conversation state and talks to the
    KB over its public HTTP API with the caller's token. Keeping it out of the KB process
    is what lets it be scaled, restarted or replaced without touching the knowledge base.
    """

    def __init__(self, port: int, config_path: Path, log_path: Path) -> None:
        self.port = port
        self.config_path = config_path
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None
        self.detail = ""

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, wait_seconds: float = 60.0) -> bool:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1",
               "PYTHONIOENCODING": "utf-8"}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", ASSISTANT_MODULE,
             "--config", str(self.config_path), "--port", str(self.port)],
            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
        )
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.detail = f"assistant service exited with code {self.proc.returncode}; see {self.log_path}"
                return False
            info = self.health()
            if info and info.get("status") == "ok":
                self.detail = ""
                return True
            time.sleep(0.25)
        self.detail = f"assistant service did not become healthy within {wait_seconds:.0f}s"
        return False

    def health(self) -> dict | None:
        try:
            with urlopen(f"{self.base}/health", timeout=3) as resp:
                return json.loads(resp.read())
        except Exception:  # noqa: BLE001 - not started yet
            return None

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def info(self) -> dict:
        return {
            "enabled": self.proc is not None and self.proc.poll() is None,
            "port": self.port,
            "database": str(self.config_path.parent / "assistant.db"),
            "detail": self.detail,
        }


def write_assistant_config(run_dir: Path, api_base: str, tokens: dict[str, str],
                           llm_base: str, chat_model: str,
                           replies: dict[str, str] | None = None) -> Path:
    """The assistant's runtime config: KB endpoint + per-identity tokens + model route.

    Tokens live here (server side) so the browser never holds one; the console proxy
    forwards an identity name and the assistant resolves it to a KB bearer token.
    """
    payload = {
        "database": str(run_dir / "assistant.db"),
        "workspace": "handbook",
        "kb": {"base_url": api_base, "tokens": tokens},
        "llm": {"base_url": llm_base, "model": chat_model, "timeout": 120.0},
        "assistant": {
            "workspace": "handbook",
            "max_history": 6,
            "retry_on_insufficient": True,
            "greeting_examples": [
                "入职当天在哪里领取办公电脑？",
                "出差住宿每晚上限多少？",
                "采购一万元以上要几家比价？",
            ],
            # 兜底话术（拒答 / 不可用 / 无权限 / 超出范围）可覆盖，空字典走助手内置默认。
            "replies": dict(replies or {}),
        },
    }
    path = run_dir / "assistant-config.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf8")
    return path


REPLY_KEYS = ('refusal', 'unavailable', 'no_permission', 'out_of_scope')
REPLY_OVERRIDES: dict[str, str] = {}


def _reply_defaults() -> dict[str, str]:
    """内置兜底话术；从助手包里取，避免这层再抄一份文案。"""
    try:
        from assistant.service import REPLY_DEFAULTS  # noqa: PLC0415 - optional import

        return dict(REPLY_DEFAULTS)
    except Exception:  # noqa: BLE001 - console 可以不带助手包运行
        return {}


def current_replies() -> dict:
    """当前生效的四句兜底：覆盖值 + 内置默认 + 是否被覆盖。"""
    defaults = _reply_defaults()
    custom = _read_replies_file()
    merged = {}
    for key in REPLY_KEYS:
        merged[key] = custom.get(key) or defaults.get(key, '')
    return {
        'replies': merged,
        'defaults': {k: defaults.get(k, '') for k in REPLY_KEYS},
        'overridden': sorted(k for k in custom if custom.get(k)),
        'keys': list(REPLY_KEYS),
    }


def _read_replies_file() -> dict:
    if ASSISTANT_CONFIG is None:
        return {}
    try:
        data = json.loads(Path(ASSISTANT_CONFIG).read_text(encoding='utf8'))
        raw = (data.get('assistant') or {}).get('replies') or {}
        return {str(k): str(v) for k, v in raw.items() if str(v).strip()}
    except Exception:  # noqa: BLE001 - 配置读不到就当没覆盖
        return {}


def _write_replies_file(replies: dict[str, str]) -> None:
    if ASSISTANT_CONFIG is None:
        raise RuntimeError('assistant config path is unknown')
    path = Path(ASSISTANT_CONFIG)
    data = json.loads(path.read_text(encoding='utf8'))
    data.setdefault('assistant', {})['replies'] = {k: v for k, v in replies.items() if v.strip()}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')


def apply_replies(payload: dict) -> dict:
    """写入兜底话术并重启助手进程让它生效（改一句就变，不用重启整个栈）。"""
    if not isinstance(payload, dict):
        raise ValueError('replies 必须是对象')
    unknown = [k for k in payload if k not in REPLY_KEYS]
    if unknown:
        raise ValueError('未知的兜底键：' + ', '.join(sorted(unknown)))
    current = _read_replies_file()
    for key in REPLY_KEYS:
        if key not in payload:
            continue
        text = str(payload.get(key) or '').strip()
        if text:
            current[key] = text
        else:
            current.pop(key, None)
    started = time.time()
    _write_replies_file(current)
    global REPLY_OVERRIDES
    REPLY_OVERRIDES = dict(current)
    steps = {}
    if ASSISTANT is not None:
        ASSISTANT.stop()
        steps['assistant'] = bool(ASSISTANT.start(wait_seconds=90))
    return {
        'changed': True,
        'ms': round((time.time() - started) * 1000, 1),
        'steps': steps,
        **current_replies(),
    }


_OLLAMA_CACHE: dict = {'ts': 0.0, 'models': set()}


def ollama_models(ollama_base: str = 'http://127.0.0.1:11434') -> set:
    """Tags present in the local Ollama library; cached for a minute."""
    now = time.time()
    cached = _OLLAMA_CACHE['models']
    if now - _OLLAMA_CACHE['ts'] < 60 and cached:
        return cached
    found = set()
    try:
        with urlopen(ollama_base.rstrip('/') + '/api/tags', timeout=3) as resp:
            data = json.loads(resp.read().decode('utf8'))
        for item in data.get('models', []):
            for key in ('name', 'model'):
                value = item.get(key)
                if value:
                    found.add(value)
                    found.add(value.split(':')[0])
    except Exception:
        return cached or set()
    _OLLAMA_CACHE.update({'ts': now, 'models': found})
    return found


def ollama_unload(model: str, ollama_base: str = 'http://127.0.0.1:11434') -> bool:
    """Drop one model out of VRAM right away (same effect as 'ollama stop').

    This box is shared with a resident voice bot, so switching profiles must not
    leave the previous model parked in VRAM until keep_alive expires.
    """
    if not model:
        return False
    body = json.dumps({'model': model, 'keep_alive': 0}).encode('utf8')
    req = UrlRequest(ollama_base.rstrip('/') + '/api/generate', data=body,
                  headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(req, timeout=20) as resp:
            resp.read()
        return True
    except Exception:
        return False


def loaded_models(ollama_base: str = 'http://127.0.0.1:11434') -> list:
    """Names currently resident in VRAM (what /api/ps reports)."""
    try:
        with urlopen(ollama_base.rstrip('/') + '/api/ps', timeout=5) as resp:
            data = json.loads(resp.read().decode('utf8'))
    except Exception:
        return []
    names = []
    for item in data.get('models', []):
        name = item.get('name') or item.get('model')
        if name:
            names.append(name)
    return names


def enforce_single(model: str, ollama_base: str = 'http://127.0.0.1:11434') -> list:
    """Unload every resident model except the wanted one.

    The GPU is shared with a resident voice bot, so only one KB model may sit in
    VRAM at a time; otherwise two profiles plus the bot do not fit.
    """
    evicted = []
    for name in loaded_models(ollama_base):
        if name == model:
            continue
        if ollama_unload(name, ollama_base):
            evicted.append(name)
    return evicted


def model_profiles() -> list:
    have = ollama_models()
    rows = []
    for item in MODEL_PROFILES:
        key = item['key']
        rows.append({**item,
                     'available': key in have or (key + ':latest') in have,
                     'current': key == CHAT_MODEL_CURRENT})
    return rows


def switch_chat_model(model: str) -> dict:
    """Swap the generation model at runtime: rag gateway, kb config, api, assistant."""
    global CHAT_MODEL_CURRENT
    if model not in {item['key'] for item in MODEL_PROFILES}:
        raise ValueError('unknown model profile: ' + model)
    if model == CHAT_MODEL_CURRENT:
        return {'model': model, 'changed': False, 'ms': 0,
                'unloaded': enforce_single(model)}
    started = time.perf_counter()
    steps: dict = {}
    previous = CHAT_MODEL_CURRENT
    if RAG is not None:
        RAG.stop()
        RAG.chat_model = model
        steps['rag'] = RAG.start()
    if CONFIG_PATH is not None:
        cfg = json.loads(CONFIG_PATH.read_text(encoding='utf8'))
        cfg.setdefault('generation', {})['model'] = model
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf8')
        steps['api_ms'] = API.restart()
    if ASSISTANT is not None and ASSISTANT_CONFIG is not None:
        acfg = json.loads(ASSISTANT_CONFIG.read_text(encoding='utf8'))
        acfg.setdefault('llm', {})['model'] = model
        ASSISTANT_CONFIG.write_text(json.dumps(acfg, ensure_ascii=False, indent=2), encoding='utf8')
        ASSISTANT.stop()
        steps['assistant'] = ASSISTANT.start()
    steps['unloaded'] = enforce_single(model)
    CHAT_MODEL_CURRENT = model
    return {'model': model, 'changed': True,
            'ms': round((time.perf_counter() - started) * 1000, 1), 'steps': steps}


def build_providers(port: int, chat_model: str) -> dict:
    """The three provider blocks ekb/config.py reads straight into ModelHTTP."""
    base = f"http://127.0.0.1:{port}/v1"
    return {
        "embedding": {
            "base_url": base, "model": EMBED_MODEL, "dimensions": EMBED_DIMENSIONS,
            "revision": EMBED_MODEL, "timeout": 60.0,
        },
        "reranking": {"base_url": base, "model": RERANK_MODEL, "timeout": 60.0},
        "generation": {"base_url": base, "model": chat_model, "timeout": 120.0},
    }


def initialize_with_models(directory: Path, providers: dict) -> Path:
    """Same synthetic bootstrap as ekb.cli.initialize(), but the corpus is embedded
    while it is written, so hybrid retrieval has vectors for the seeded documents.
    Ingestion would otherwise short-circuit on an already-ACTIVE version."""
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / "config.json"
    db = directory / "knowledge.db"
    credentials, tokens = [], {}
    for key, subject, role, tenant, audiences, workspaces in identity_spec():
        token = secrets.token_urlsafe(32)
        tokens[key] = token
        credentials.append({
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "subject": subject,
            "tenant": tenant,
            "workspaces": workspaces,
            "audiences": audiences,
        })
    config = {"database": db.relative_to(ROOT).as_posix(), "credentials": credentials}
    config.update(providers)
    embedder = ModelHTTP(**providers["embedding"]) if providers.get("embedding") else None
    catalog = Catalog(db)
    # 幂等：只有空库才灌冻结评测语料。带 --data-dir 复用同一份库时，
    # 第二次启动不能再写一遍 16 篇英文语料（否则 quality 会报 duplicate_hash）。
    existing = 0
    try:
        with sqlite3.connect(db) as probe:
            existing = probe.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    except sqlite3.Error:
        existing = 0
    if not existing:
        corpus = json.loads((ROOT / "evaluation" / "corpus.json").read_text(encoding="utf8"))
        populate(catalog, corpus, embedder)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf8")
    (directory / "demo-credentials.json").write_text(
        json.dumps(tokens, indent=2), encoding="utf8"
    )
    return config_path


API: Api | None = None
RAG: RagService | None = None
ASSISTANT = None
IDENTITIES: dict[str, str] = {}
DB_PATH: Path | None = None
IDENTITY_META: list[dict] = []

# 演示用的中文语料：走公开 HTTP 接口写入，不碰冻结的 evaluation/corpus.json。
# 目录即语义：handbook/ = 公开制度；handbook-internal/ = audience internal（reader 看不见）；
# restricted/ = 机密工作区（reader 查询返回 403）。文件名就是文档标题。
ZH_DOC_DIR = ROOT / "demo" / "zh-docs"
# 注意：四个演示身份的工作区权限只有 handbook，往 restricted 写会被 403 拒绝，
# 所以机密级文档也放进 handbook 目录、标 audience=internal——用 audience 演示可见范围，
# 用工作区演示越权（语料里本就有一篇 restricted 工作区的文档，reader 查询会 403）。
ZH_BUCKETS = [
    ("handbook", "all"),
    ("handbook-internal", "internal"),
]


def _post(token: str, path: str, payload: dict) -> tuple[int, dict]:
    req = UrlRequest(f"http://127.0.0.1:{API.port}{path}",
                     data=json.dumps(payload, ensure_ascii=False).encode("utf8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except HTTPError as exc:
        return exc.code, {}


def _get(token: str, path: str) -> tuple[int, dict]:
    req = UrlRequest(f"http://127.0.0.1:{API.port}{path}")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except HTTPError as exc:
        return exc.code, {}


def seed_chinese(token: str) -> list[dict]:
    """Create + ingest every demo document once (idempotent by title)."""
    created = []
    for folder, audience in ZH_BUCKETS:
        workspace = "handbook"
        status, listing = _get(token, f"/documents?workspace={workspace}")
        existing = {item.get("title") for item in (listing.get("items") or [])} if status == 200 else set()
        for path in sorted((ZH_DOC_DIR / folder).glob("*.md")):
            title = path.stem
            if title in existing:
                continue
            status, doc = _post(token, "/documents", {
                "workspace": workspace, "title": title,
                "text": path.read_text(encoding="utf8"), "audience": audience,
            })
            if status != 201:
                continue
            _post(token, f"/documents/{doc['id']}/versions/1/ingest", {})
            created.append({"id": doc["id"], "title": title, "workspace": workspace, "audience": audience})
    return created


def seed_personal(tokens: dict) -> list[dict]:
    """每个身份往自己的个人库写种子文档（幂等：按标题去重）。"""
    created: list[dict] = []
    for key, (workspace, who) in PERSONAL_WORKSPACES.items():
        token = tokens.get(key)
        folder = ZH_DOC_DIR / workspace
        if not token or not folder.is_dir():
            continue
        status, listing = _get(token, f"/documents?workspace={workspace}")
        existing = {item.get("title") for item in (listing.get("items") or [])} if status == 200 else set()
        for path in sorted(folder.glob("*.md")):
            title = path.stem
            if title in existing:
                continue
            status, doc = _post(token, "/documents", {
                "workspace": workspace, "title": title,
                "text": path.read_text(encoding="utf8"), "audience": "all",
            })
            if status != 201:
                continue
            _post(token, f"/documents/{doc['id']}/versions/1/ingest", {})
            created.append({"id": doc["id"], "title": title,
                            "workspace": workspace, "owner": who})
    return created


# 两层：企业库一个工作区，个人库每人一个。
# 个人库的私有性来自产品的工作区级隔离（ekb/auth.py:14 require(workspace)），
# 产品只有 all / internal 两档可见范围、没有「指定同事可见」的 ACL，
# 所以这里不做细粒度共享，也不做跨库融合检索（/query 只接受一个 workspace）。
ORG_WORKSPACE = "handbook"
ORG_LABEL = "员工手册"
PERSONAL_WORKSPACES = {
    "editor": ("personal-zhuzuomin", "朱佐民"),
    "reader": ("personal-wangfang", "王芳"),
    "staff": ("personal-liqiang", "李强"),
}
WORKSPACE_CATALOG = (
    [{"key": ORG_WORKSPACE, "label": ORG_LABEL, "kind": "org", "owner": ""}]
    + [{"key": ws, "label": who + "的资料", "kind": "personal", "owner": who}
       for ws, who in PERSONAL_WORKSPACES.values()]
)

# 演示层的分类标签：产品 ekb/ 没有 tags 概念，这里按标题关键词归类，
# 台账写在本次运行的 SQLite 里（doc_tags），产品 schema 一行不动。
TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("人事 · 入职离职",
     ("入职", "离职", "转正", "试用期", "交接", "离职面谈", "新员工", "转岗", "借调", "轮岗",
      "onboarding", "offboarding", "new hire", "handover", "transfer")),
    ("人事 · 招聘与配置",
     ("招聘", "校招", "实习", "内部推荐", "简历", "发 offer", "人员配置",
      "recruit", "hiring", "intern", "headcount", "candidate")),
    ("人事 · 考勤休假",
     ("考勤", "年假", "请假", "加班", "调休", "病假", "医疗期", "打卡", "休假",
      "leave", "holiday", "time off", "attendance")),
    ("人事 · 薪酬福利",
     ("薪酬", "工资", "社保", "公积金", "个税", "福利", "奖金", "调薪",
      "商业保险", "体检", "产假", "陪产", "补助", "关怀", "收入证明", "职工证明",
      "salary", "payroll", "benefit", "insurance", "compensation")),
    ("人事 · 员工关系",
     ("员工关系", "申诉", "争议", "纪律", "处分", "竞业", "组织架构", "汇报",
      "员工手册", "行为准则", "劳动合同",
      "employee relation", "grievance", "discipline", "org chart", "non-compete")),
    ("行政 · 办公与资产",
     ("办公", "工位", "用品", "设备", "会议室", "门禁", "工牌", "固定资产", "保洁",
      "用车", "派车", "车辆", "名片", "印刷", "团建", "员工活动", "快递",
      "公文", "食堂", "茶水",
      "meeting room", "workspace", "office", "supplies", "asset", "badge", "room booking",
      "vehicle", "print", "mail room")),
    ("行政 · 差旅",
     ("差旅", "出差", "住宿", "交通", "机票", "行程",
      "travel", "hotel", "trip", "flight")),
    ("财务 · 报销与资金",
     ("报销", "费用", "发票", "付款", "预付", "借款", "预算", "对公", "银行", "资金",
      "招待", "礼品",
      "expense", "invoice", "payment", "claim", "budget", "reimburse")),
    ("财务 · 合同与用印",
     ("合同", "印章", "用印", "法务", "审阅", "授权",
      "知识产权", "专利", "商标", "著作权",
      "contract", "signature", "seal", "legal", "authority", "intellectual property")),
    ("采购 · 供应商",
     ("采购", "供应商", "比价", "招标", "投标", "验收", "履约", "折扣", "报价",
      "purchas", "supplier", "vendor", "procure", "quote", "tender", "bid")),
    ("IT · 账号与数据",
     ("IT", "AI", "账号", "权限", "数据", "备份", "恢复", "隐私", "出境",
      "密码", "多因素", "认证", "服务器", "发布", "网络", "WiFi", "邮箱", "通讯",
      "文档", "记录", "个人信息",
      "account", "password", "access", "backup", "restoration", "service desk", "identity",
      "server", "release", "network", "wifi", "mail", "multi-factor")),
    ("安全 · 应急与合规",
     ("信息安全", "安全事件", "消防", "应急", "演练", "合规", "反舞弊",
      "举报", "并购", "审计", "风控", "贿赂", "利益冲突", "商业秘密", "保密",
      "security", "incident", "emergency", "fire", "compliance", "audit", "fraud", "drill",
      "bribery", "confidential")),
    ("业务 · 客户与工单",
     ("客户", "工单", "投诉", "商机", "售后", "合作伙伴",
      "customer", "ticket", "complaint", "lead", "crm")),
    ("培训与发展",
     ("培训", "学习", "发展", "晋升", "考核", "绩效", "评估",
      "training", "learning", "development", "review", "performance")),
]


def auto_tags(title: str) -> list[str]:
    """标题关键词归类。中文词与大写缩写（IT / AI）按原样比，英文小写词按 lower() 比——
    否则 "Account recovery" 这种首字母大写的英文标题一律进不了分类。"""
    raw = title or ""
    low = raw.lower()
    hits = []
    for name, words in TAG_RULES:
        for word in words:
            probe = low if (word.isascii() and word.islower()) else raw
            if word in probe:
                hits.append(name)
                break
    return hits[:2] or ["未分类"]



def identity_spec() -> list[tuple]:
    """(key, subject, role, tenant, audiences, workspaces) —— token 与页面 meta 的唯一来源。"""
    base = [
        ("editor", "editor", "editor", "acme", ["all", "internal"]),
        ("reader", "reader", "reader", "acme", ["all"]),
        ("staff", "staff", "reader", "acme", ["all", "internal"]),
        ("other-reader", "other-reader", "reader", "other", ["all"]),
    ]
    spec = []
    for key, subject, role, tenant, audiences in base:
        workspaces = {ORG_WORKSPACE: role}
        if key in PERSONAL_WORKSPACES:
            workspaces[PERSONAL_WORKSPACES[key][0]] = "editor"
        spec.append((key, subject, role, tenant, audiences, workspaces))
    return spec


def identity_meta(tokens: dict[str, str]) -> list[dict]:
    """Mirrors the credential set the console writes for the demo."""
    out = []
    for key, subject, role, tenant, audiences, workspaces in identity_spec():
        out.append({
            "key": key, "subject": subject, "role": role, "tenant": tenant,
            "workspace_role": role, "workspaces": workspaces,
            "audiences": audiences, "token": bool(tokens.get(key)),
        })
    return out


ZIP_MAX_FILES = 300
ZIP_MAX_TOTAL = 300 * 1024 * 1024  # 解压后总字节上限，防 zip bomb
ZIP_MAX_ONE = 60 * 1024 * 1024     # 单个成员解压后上限


def _zip_member_name(info) -> str:
    """把 zip 成员名还原成 UTF-8 字符串。

    zip 规范里文件名要么带 UTF-8 标志位（0x800），要么是本地代码页。Windows 资源管理器
    打的包常常是 GBK 且不带标志位，Python 会按 cp437 解出来变成乱码（目录名跟着一起坏）。
    这里按 cp437 还原原始字节，再依次试 utf-8 / gb18030 / big5。
    """
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for enc in ("utf-8", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


def expand_zip(filename: str, b64data: str) -> tuple[list[dict], list[dict]]:
    """把上传的 .zip 摊平成普通文件项：保留成员相对路径，跳过目录/隐藏文件/__MACOSX。

    返回 (items, errors)。items 里每项仍是 {filename, data(base64)}，走原来的单文件导入路径，
    所以解析、去重、入库、台账与单文件导入完全一致。
    """
    import zipfile  # 局部导入：只有真正上传压缩包时才加载
    errors: list[dict] = []
    try:
        raw = base64.b64decode(b64data, validate=True)
    except Exception:
        return [], [{"filename": filename, "status": "failed", "error": "压缩包内容不是合法的 base64"}]
    items: list[dict] = []
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                member = PurePosixPath(_zip_member_name(info).replace("\\", "/"))
                parts = [p for p in member.parts if p not in ("", ".", "..")]
                if not parts or parts[0] == "__MACOSX":
                    continue
                if any(p.startswith(".") for p in parts):
                    continue
                clean = "/".join(parts)
                if len(items) >= ZIP_MAX_FILES:
                    errors.append({"filename": filename, "status": "failed",
                                   "error": f"压缩包内文件超过 {ZIP_MAX_FILES} 个，其余未导入"})
                    break
                if info.file_size > ZIP_MAX_ONE:
                    errors.append({"filename": clean, "status": "failed",
                                   "error": "解压后超过 60 MB，跳过"})
                    continue
                total += info.file_size
                if total > ZIP_MAX_TOTAL:
                    errors.append({"filename": filename, "status": "failed",
                                   "error": "解压后总量超过 300 MB，其余未导入"})
                    break
                data = archive.read(info)
                items.append({"filename": clean, "data": base64.b64encode(data).decode()})
    except zipfile.BadZipFile:
        return [], [{"filename": filename, "status": "failed", "error": "不是有效的 zip 压缩包"}]
    return items, errors


# ---------- 账号与会话（演示层；ekb/ 一行不改） ----------
# 早先身份靠请求头 X-Identity「客户端自称是谁」，页面下拉框换一下就是 editor。
# 那对着一台本机演示够用，但它不是登录机制：任何人 curl 改个头就拿到全部权限
# （见 D:\AI\review\property-ai-defects.md 的 PA-228）。这里补上真正的账号体系：
# 账号文件 + PBKDF2 口令 + 会话 Cookie + 过期 + 登出 + 失败锁定 + 审计。
ACCOUNTS_PATH: Path | None = None
ACCOUNTS: dict[str, dict] = {}
SESSIONS: dict[str, dict] = {}
SID_LOCK = threading.Lock()
FAILS: dict[str, list[float]] = {}
FAIL_LOCK = threading.Lock()
AUTH_REQUIRED = True
ALLOW_HEADER_IDENTITY = False
DEV_SWITCH = True
SESSION_TTL = 8 * 3600
SESSION_IDLE = 2 * 3600
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_SECONDS = 60
COOKIE_NAME = "ekb_sid"
DEMO_PASSWORD_SET = False
DEMO_PASSWORD = ""

ACCOUNT_NAMES = {
    "editor": "朱佐民 · 知识库管理员",
    "reader": "王芳 · 普通员工",
    "staff": "李强 · 部门主管",
    "other-reader": "外部租户读者",
}


def hash_password(password: str, salt_hex: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf8"), bytes.fromhex(salt_hex), iterations
    ).hex()


def load_accounts(path: Path, demo_password: str = "") -> tuple[dict, list[str]]:
    """读 accounts.json；没有就按 identity_spec() 建一份。

    返回 (accounts, 明文行)。明文只在启动时打印一次、并写 accounts.txt，
    后续鉴权只比对 PBKDF2 哈希 —— 账号文件本身不含口令。
    """
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf8"))
            rows = {row["account"]: row for row in data.get("accounts", [])}
            if rows:
                return rows, []
        except (ValueError, KeyError):
            pass
    iterations = 200_000
    accounts: dict[str, dict] = {}
    lines: list[str] = []
    for key, subject, role, tenant, audiences, workspaces in identity_spec():
        password = demo_password or secrets.token_urlsafe(9)
        salt = secrets.token_hex(16)
        accounts[key] = {
            "account": key,
            "name": ACCOUNT_NAMES.get(key, subject),
            "role": role, "tenant": tenant, "audiences": audiences,
            "workspaces": workspaces, "salt": salt, "iterations": iterations,
            "hash": hash_password(password, salt, iterations),
        }
        lines.append(key + " " + password)
    path.write_text(
        json.dumps({"version": 1, "iterations": iterations,
                    "accounts": list(accounts.values())}, ensure_ascii=False, indent=2),
        encoding="utf8",
    )
    return accounts, lines


def public_account(row: dict) -> dict:
    """给页面看的账号视图：不含 salt / hash / iterations。"""
    return {
        "account": row["account"], "name": row.get("name", row["account"]),
        "role": row.get("role"), "tenant": row.get("tenant"),
        "audiences": row.get("audiences", ["all"]),
        "workspaces": row.get("workspaces", {}),
    }


def _prune_sessions(now: float) -> None:
    dead = [sid for sid, row in SESSIONS.items()
            if now - row["seen"] > SESSION_IDLE or now - row["issued"] > SESSION_TTL]
    for sid in dead:
        SESSIONS.pop(sid, None)


def create_session(account: str, ip: str) -> dict:
    sid = secrets.token_urlsafe(32)
    now = time.time()
    row = {"account": account, "issued": now, "seen": now, "ip": ip}
    with SID_LOCK:
        _prune_sessions(now)
        SESSIONS[sid] = row
    return {"sid": sid, **row}


def read_session(sid: str) -> dict | None:
    if not sid:
        return None
    now = time.time()
    with SID_LOCK:
        row = SESSIONS.get(sid)
        if not row:
            return None
        if now - row["seen"] > SESSION_IDLE or now - row["issued"] > SESSION_TTL:
            SESSIONS.pop(sid, None)
            return None
        row["seen"] = now
        return dict(row)


def drop_session(sid: str) -> None:
    with SID_LOCK:
        SESSIONS.pop(sid, None)


def login_allowed(key: str) -> bool:
    now = time.time()
    with FAIL_LOCK:
        hits = [t for t in FAILS.get(key, []) if now - t < LOGIN_LOCK_SECONDS]
        FAILS[key] = hits
        return len(hits) < LOGIN_MAX_FAILS


def note_login_failure(key: str) -> None:
    with FAIL_LOCK:
        FAILS.setdefault(key, []).append(time.time())


def audit_row(conn, tenant: str, actor: str, action: str, request_id: str | None = None) -> None:
    """登录类事件也写进产品的 audit 表（document_id 为空）。失败不阻断登录流程。"""
    try:
        conn.execute(
            "INSERT INTO audit (tenant, actor, action, document_id, request_id, created_at)"
            " VALUES (?, ?, ?, NULL, ?, ?)",
            (tenant or "acme", actor, action, request_id, time.time()),
        )
        conn.commit()
    except sqlite3.Error:
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "EKBDemoConsole/1.0"

    def log_message(self, fmt, *args):  # keep the terminal readable
        pass

    # ---------- plumbing ----------
    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if PUBLIC_MODE:
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict, extra: dict | None = None):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf8"),
                   "application/json; charset=utf-8", extra)

    def _page(self):
        if not PAGE.exists():
            self._json(500, {"error": {"code": "CONSOLE.PAGE_MISSING"}})
            return
        extra: dict[str, str] = {}
        if PUBLIC_MODE and PUBLIC_TOKEN:
            given = (parse_qs(urlparse(self.path).query).get("k") or [""])[0]
            if given and secrets.compare_digest(given, PUBLIC_TOKEN):
                extra["Set-Cookie"] = (
                    "ekb_pass=" + PUBLIC_TOKEN + "; Path=/; HttpOnly; SameSite=Lax"
                )
        self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8", extra)

    # ---------- 对外演示的护栏 ----------
    def _client_identity(self) -> str:
        """身份只从会话里来：public 模式钉死，本地模式拿 ekb_sid Cookie 反查。

        默认不再信任请求头 X-Identity —— 那等于让客户端自己声明权限（PA-228）。
        只有在 --allow-header-identity 显式打开时才回落到旧行为（给老脚本过渡用）。
        """
        if PUBLIC_MODE:
            return PUBLIC_IDENTITY
        row = read_session(self._sid())
        if row and row.get("account") in ACCOUNTS:
            return row["account"]
        if ALLOW_HEADER_IDENTITY:
            return self.headers.get("X-Identity") or "anon"
        return "anon"

    # ---------- 账号登录（演示层） ----------
    def _cookie(self, name: str) -> str:
        for part in (self.headers.get("Cookie") or "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value.strip()
        return ""

    def _sid(self) -> str:
        return self._cookie(COOKIE_NAME)

    def _session_cookie(self, sid: str, max_age: int) -> str:
        # 本机演示走 http，所以不加 Secure；真上公网应由反向代理终止 TLS 并补 Secure。
        return (COOKIE_NAME + "=" + sid + "; Path=/; HttpOnly; SameSite=Lax; Max-Age="
                + str(max_age))

    def _db_quiet(self):
        if DB_PATH is None or not Path(DB_PATH).exists():
            return None
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _audit_login(self, tenant: str, actor: str, action: str) -> None:
        conn = self._db_quiet()
        if conn is None:
            return
        try:
            audit_row(conn, tenant, actor, action, self.headers.get("X-Request-ID"))
        finally:
            conn.close()

    def _login(self):
        try:
            payload = json.loads(self._raw_body or b"{}")
        except ValueError:
            self._json(422, {"error": {"code": "CONSOLE.BAD_JSON", "detail": "body 不是合法 JSON"}})
            return
        account = str(payload.get("account") or "").strip()
        password = str(payload.get("password") or "")
        if not account or not password:
            self._json(422, {"error": {"code": "CONSOLE.BAD_LOGIN_BODY",
                                       "detail": "账号和口令都要填"}})
            return
        throttle = self.client_address[0] + "|" + account
        if not login_allowed(throttle):
            self._json(429, {"error": {"code": "CONSOLE.LOGIN_LOCKED",
                                       "detail": "这个账号连续登录失败过多，请 60 秒后再试"}})
            return
        row = ACCOUNTS.get(account)
        ok = False
        if row:
            expect = hash_password(password, row["salt"], row.get("iterations", 200_000))
            ok = secrets.compare_digest(expect, row["hash"])
        if not ok:
            note_login_failure(throttle)
            self._audit_login(row.get("tenant") if row else "acme", account, "login_failed")
            self._json(401, {"error": {"code": "CONSOLE.BAD_CREDENTIALS",
                                       "detail": "账号或口令不对"}})
            return
        session = create_session(account, self.client_address[0])
        self._audit_login(row.get("tenant"), account, "login")
        self._json(200, {**public_account(row),
                         "expires_at": int(session["issued"] + SESSION_TTL)},
                   {"Set-Cookie": self._session_cookie(session["sid"], SESSION_TTL)})

    def _logout(self):
        drop_session(self._sid())
        self._json(200, {"ok": True}, {"Set-Cookie": self._session_cookie("", 0)})

    def _me(self):
        row = read_session(self._sid())
        account = ACCOUNTS.get((row or {}).get("account", ""))
        if not (row and account):
            self._json(401, {"error": {"code": "CONSOLE.UNAUTHENTICATED",
                                       "detail": "还没有登录"}})
            return
        self._json(200, {**public_account(account),
                         "expires_at": int(row["issued"] + SESSION_TTL),
                         "idle_expires_at": int(row["seen"] + SESSION_IDLE)})

    def _accounts(self):
        if not DEMO_PASSWORD_SET:
            hint = "演示环境：口令在启动日志与运行目录的 accounts.txt 里"
        elif PUBLIC_MODE:
            hint = "演示环境：口令由部署方提供"
        else:
            hint = "演示口令：" + DEMO_PASSWORD + "（启动脚本里固定的，四个账号通用）"
        self._json(200, {"accounts": [public_account(r) for r in ACCOUNTS.values()],
                         "dev_switch": DEV_SWITCH,
                         "header_identity": ALLOW_HEADER_IDENTITY,
                         "password_hint": hint,
                         "session_ttl": SESSION_TTL})

    def _dev_login(self):
        """一键切身份：只给演示与自检用，显式开关，写进审计（action=dev_login）。"""
        if not DEV_SWITCH:
            self._json(403, {"error": {"code": "CONSOLE.DEV_SWITCH_OFF",
                                       "detail": "这个实例关掉了一键切换（--strict-auth），请用账号口令登录"}})
            return
        try:
            payload = json.loads(self._raw_body or b"{}")
        except ValueError:
            self._json(422, {"error": {"code": "CONSOLE.BAD_JSON", "detail": "body 不是合法 JSON"}})
            return
        account = str(payload.get("account") or "")
        row = ACCOUNTS.get(account)
        if not row:
            self._json(404, {"error": {"code": "CONSOLE.UNKNOWN_ACCOUNT",
                                       "detail": "没有这个账号"}})
            return
        session = create_session(account, self.client_address[0])
        self._audit_login(row.get("tenant"), account, "dev_login")
        self._json(200, {**public_account(row),
                         "expires_at": int(session["issued"] + SESSION_TTL)},
                   {"Set-Cookie": self._session_cookie(session["sid"], SESSION_TTL)})

    def _guest_meta(self) -> dict:
        """未登录访客能看到的全部信息：只够渲染登录层。"""
        return {
            "identities": [],
            "workspaces": {},
            "workspace_default": ORG_WORKSPACE,
            "needs_login": True,
            "public": False,
            "restart_supported": True,
            "api_health": self._health(),
            "models": {"current": CHAT_MODEL_CURRENT, "profiles": []},
            "auth": {"required": AUTH_REQUIRED, "dev_switch": DEV_SWITCH,
                     "header_identity": ALLOW_HEADER_IDENTITY,
                     "session_ttl": SESSION_TTL, "session_idle": SESSION_IDLE},
        }

    def _pass_ok(self) -> bool:
        if not (PUBLIC_MODE and PUBLIC_TOKEN):
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "ekb_pass" and secrets.compare_digest(value.strip(), PUBLIC_TOKEN):
                return True
        given = (parse_qs(urlparse(self.path).query).get("k") or [""])[0]
        return bool(given) and secrets.compare_digest(given, PUBLIC_TOKEN)

    @staticmethod
    def _public_post_allowed(special: str) -> bool:
        # 访客在这套演示里只被允许「提问」和「给回答打分」两件写操作。
        return special in ("/query", "/assistant/chat", "/assistant/feedback")

    def _rate_limited(self) -> bool:
        if not PUBLIC_MODE:
            return False
        ip = self.client_address[0]
        now = time.time()
        with RATE_LOCK:
            hits = [t for t in _RATE.get(ip, []) if now - t < 60]
            if len(hits) >= PUBLIC_RATE_LIMIT:
                _RATE[ip] = hits
                return True
            hits.append(now)
            _RATE[ip] = hits
        return False

    def _public_meta(self) -> dict:
        key = PUBLIC_IDENTITY
        item = next((i for i in IDENTITY_META if i["key"] == key), None) or {}
        return {
            "identities": [{
                "key": item.get("key", key), "subject": item.get("subject", key),
                "role": item.get("role", "reader"), "tenant": item.get("tenant", "acme"),
                "workspaces": item.get("workspaces", {ORG_WORKSPACE: "reader"}),
                "audiences": item.get("audiences", ["all"]),
            }],
            "workspaces": WORKSPACE_CATALOG,
            "workspace_default": ORG_WORKSPACE,
            "api_port": API.port,
            "api_health": self._health(),
            "restart_supported": False,
            "python": sys.version.split()[0],
            "rag": RAG.info() if RAG else {"enabled": False, "detail": "console started with --no-rag"},
            "assistant": ASSISTANT.info() if ASSISTANT else {"enabled": False},
            "public": True,
            "public_note": "对外演示模式：只读、身份钉死为 " + key + "、管理端点关闭",
            "models": {"current": CHAT_MODEL_CURRENT, "profiles": model_profiles()},
            "rate_limit_per_minute": PUBLIC_RATE_LIMIT,
        }

    # ---------- routing ----------
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        # 先把请求体一次读干净存起来：任何分支（比如 /__restart）不读 body，
        # 剩下的字节会被当成下一条请求的请求行，在 keep-alive 下表现为后续 POST 莫名 501。
        length = int(self.headers.get("Content-Length") or 0)
        self._raw_body = self.rfile.read(length) if length > 0 else b""
        self._route("POST")

    def do_HEAD(self):
        self._route("HEAD")

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def _route(self, method: str):
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/console", "/console.html"):
            self._page()
            return
        if not path.startswith("/api/"):
            self._json(404, {"error": {"code": "CONSOLE.NOT_FOUND", "path": path}})
            return
        special = path[len("/api"):]
        if PUBLIC_MODE:
            if not self._pass_ok():
                self._json(401, {"error": {"code": "CONSOLE.PASS_REQUIRED",
                                           "detail": "这是带口令的公开演示，请用完整链接（含 ?k=）打开"}})
                return
            # /__source 是只读正文通道（按 audience 判权，见 _read_guard），公开演示要能看资料正文
            if special.startswith("/__") and special not in ("/__meta", "/__source", "/__export"):
                self._json(403, {"error": {"code": "CONSOLE.PUBLIC_READONLY",
                                           "detail": "对外演示模式关闭了资料管理端点"}})
                return
            if method == "POST" and not self._public_post_allowed(special):
                self._json(403, {"error": {"code": "CONSOLE.PUBLIC_READONLY",
                                           "detail": "对外演示模式是只读的：可以提问，不能改资料"}})
                return
            if special in ("/query", "/assistant/chat") and self._rate_limited():
                self._json(429, {"error": {"code": "CONSOLE.RATE_LIMITED",
                                           "detail": "每个访客每分钟最多 " + str(PUBLIC_RATE_LIMIT) + " 次提问"}})
                return
        if not PUBLIC_MODE:
            if method == "POST" and special == "/__login":
                self._login(); return
            if special == "/__logout":
                self._logout(); return
            if special == "/__me":
                self._me(); return
            if special == "/__accounts":
                self._accounts(); return
            if method == "POST" and special == "/__dev-login":
                self._dev_login(); return
            if special == "/health":
                # 探活不需要登录：就绪轮询、监控、nginx 健康检查都用它
                self._json(200, self._health())
                return
            if special == "/__meta" and self._client_identity() == "anon":
                # 未登录也得能读 meta：页面靠它知道「要不要登录、能不能一键切」。
                # 但只给这点信息，库结构/模型档/工作区等登录后再拿（_guest_meta）。
                self._json(200, self._guest_meta())
                return
            if AUTH_REQUIRED and self._client_identity() == "anon":
                self._json(401, {"error": {"code": "CONSOLE.UNAUTHENTICATED",
                                           "detail": "请先登录（演示口令见启动日志或 runtime/<id>/accounts.txt）"}})
                return
        if special == "/__meta":
            if PUBLIC_MODE:
                self._json(200, self._public_meta())
                return
            self._json(200, {
                "identities": IDENTITY_META,
                "workspaces": WORKSPACE_CATALOG,
                "workspace_default": ORG_WORKSPACE,
                "api_port": API.port,
                "api_health": self._health(),
                "restart_supported": True,
                "python": sys.version.split()[0],
                "rag": RAG.info() if RAG else {"enabled": False, "detail": "console started with --no-rag"},
                "assistant": ASSISTANT.info() if ASSISTANT else {"enabled": False},
                "models": {"current": CHAT_MODEL_CURRENT, "profiles": model_profiles()},
                "auth": {"required": AUTH_REQUIRED, "dev_switch": DEV_SWITCH,
                         "header_identity": ALLOW_HEADER_IDENTITY,
                         "session_ttl": SESSION_TTL, "session_idle": SESSION_IDLE},
            })
            return
        if special == "/__restart":
            with LOCK:
                ms = API.restart()
            self._json(200, {"ok": True, "restart_ms": ms})
            return
        if special == '/__replies':
            if method == 'GET':
                self._json(200, current_replies())
                return
            if self._role_of(self._client_identity()) != 'editor':
                self._json(403, {'error': {'code': 'CONSOLE.ADMIN_FORBIDDEN',
                                           'detail': '只有 editor 身份可以修改兜底话术'}})
                return
            try:
                payload = json.loads(self._raw_body or b'{}')
            except ValueError:
                self._json(422, {'error': {'code': 'CONSOLE.BAD_JSON', 'detail': 'body 不是合法 JSON'}})
                return
            body = payload.get('replies') if isinstance(payload, dict) else None
            if not isinstance(body, dict):
                self._json(422, {'error': {'code': 'CONSOLE.BAD_REPLIES',
                                           'detail': 'body 需要 {"replies": {...}}'}})
                return
            with LOCK:
                try:
                    result = apply_replies(body)
                except ValueError as exc:
                    self._json(422, {'error': {'code': 'CONSOLE.BAD_REPLIES', 'detail': str(exc)}})
                    return
                except Exception as exc:  # noqa: BLE001 - 写盘/重启失败要回给页面
                    self._json(500, {'error': {'code': 'CONSOLE.REPLIES_FAILED', 'detail': str(exc)}})
                    return
            self._json(200, result)
            return
        if special == '/__models':
            if method == 'GET':
                self._json(200, {'current': CHAT_MODEL_CURRENT, 'profiles': model_profiles()})
                return
            if self._role_of(self._client_identity()) != 'editor':
                self._json(403, {'error': {'code': 'CONSOLE.ADMIN_FORBIDDEN',
                                           'detail': '只有 editor 身份可以切换生成模型'}})
                return
            try:
                payload = json.loads(self._raw_body or b'{}')
            except ValueError:
                self._json(422, {'error': {'code': 'CONSOLE.BAD_JSON', 'detail': 'body 不是合法 JSON'}})
                return
            target = str(payload.get('model') or '')
            try:
                with LOCK:
                    result = switch_chat_model(target)
            except ValueError as exc:
                self._json(422, {'error': {'code': 'CONSOLE.UNKNOWN_MODEL', 'detail': str(exc)}})
                return
            except Exception as exc:
                self._json(500, {'error': {'code': 'CONSOLE.MODEL_SWITCH_FAILED',
                                           'detail': str(exc)[:200]}})
                return
            self._json(200, result)
            return
        if special == "/__source":
            self._admin_source()
            return
        if special == "/__audit":
            self._admin_audit()
            return
        if special == "/__purge":
            self._admin_purge()
            return
        if special == "/__import":
            self._admin_import()
            return
        if special == "/__ocr":
            self._admin_ocr()
            return
        if special == "/__files":
            self._admin_files()
            return
        if special == "/__export":
            self._admin_export()
            return
        if special == "/__collect":
            self._admin_collect()
            return
        if special == "/__quality":
            self._admin_quality()
            return
        if special == "/__tags":
            self._admin_tags(method)
            return
        if special == "/__batch":
            self._admin_batch()
            return
        if special == "/assistant" or special.startswith("/assistant/"):
            self._forward_assistant(method, special)
            return
        self._forward(method)

    # ---------- 控制台管理端点 ----------
    # 产品 API（ekb/api.py）只有 create / version / ingest / archive，
    # 没有「读原文」「硬删除」「查审计」三个入口。演示要能改、能删、能看记录，
    # 因此这三个能力放在代理层，直接操作本次运行的 SQLite 库，
    # 并在 audit 表里留一条自己的痕迹。它们不是产品能力，README 里单列说明。
    def _role_of(self, identity: str) -> str | None:
        for item in IDENTITY_META:
            if item["key"] == identity:
                return item.get("role")
        return None

    def _role_in(self, identity: str, workspace: str) -> str | None:
        for item in IDENTITY_META:
            if item["key"] == identity:
                return (item.get("workspaces") or {}).get(workspace)
        return None

    def _audiences_of(self, identity: str) -> set[str]:
        for item in IDENTITY_META:
            if item["key"] == identity:
                return set(item.get("audiences") or ["all"])
        return {"all"}

    def _read_guard(self, workspace: str, audience: str | None) -> bool:
        """读正文的权限：编辑者可读全库；其他人按 audience 判（all 人人可读，internal 要看身份的 audiences）。

        产品 API（ekb/api.py）没有「读原文」入口，演示层的 /__source 是唯一通道；
        早先这里一律要求 editor，结果读者和公开访客一条正文都看不到 —— 那是把产品的
        能力缺口伪装成权限边界（见 D:\\AI\\review\\property-ai-defects.md 的 PA-221）。"""
        identity = self._client_identity()
        role = self._role_in(identity, workspace)
        if role == "editor":
            return True
        if role is not None and (audience == "all" or audience in self._audiences_of(identity)):
            return True
        self._json(403, {"error": {"code": "CONSOLE.READ_FORBIDDEN",
                                   "detail": "当前身份看不到这篇文档的正文",
                                   "identity": identity, "workspace": workspace,
                                   "audience": audience}})
        return False

    def _admin_guard(self, workspace: str = ORG_WORKSPACE) -> bool:
        # 权限按工作区判：同一个人在企业库可能只是 reader，在「我的资料」是 editor。
        identity = self._client_identity()
        if self._role_in(identity, workspace) != "editor":
            self._json(403, {"error": {"code": "CONSOLE.ADMIN_FORBIDDEN",
                                       "detail": "当前身份在这个知识库里不是编辑者",
                                       "identity": identity, "workspace": workspace}})
            return False
        return True

    def _workspace_param(self, qs=None, body: dict | None = None) -> str:
        """当前库：请求里显式指定优先，缺省企业库；不在工作区清单里一律按企业库处理。"""
        raw = ""
        if body:
            raw = str(body.get("workspace") or "")
        if not raw and qs is not None:
            raw = (qs.get("workspace") or [""])[0]
        return raw if any(w["key"] == raw for w in WORKSPACE_CATALOG) else ORG_WORKSPACE

    def _db(self):
        if DB_PATH is None or not Path(DB_PATH).exists():
            self._json(503, {"error": {"code": "CONSOLE.DB_MISSING"}})
            return None
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _admin_source(self):
        qs = parse_qs(urlparse(self.path).query)
        workspace = self._workspace_param(qs=qs)
        try:
            doc_id = int((qs.get("document_id") or [""])[0])
        except ValueError:
            self._json(422, {"error": {"code": "CONSOLE.BAD_DOCUMENT_ID"}})
            return
        try:
            want_version = int((qs.get("version") or [""])[0])
        except ValueError:
            want_version = None
        conn = self._db()
        if conn is None:
            return
        try:
            if want_version is None:
                row = conn.execute(
                    "SELECT d.id,d.title,d.audience,d.status,d.latest_version,d.current_version,"
                    "v.source,v.version AS vnum FROM documents d LEFT JOIN versions v "
                    "ON v.document_id=d.id AND v.version=COALESCE(d.current_version,d.latest_version) "
                    "WHERE d.id=? AND d.workspace=?", (doc_id, workspace),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT d.id,d.title,d.audience,d.status,d.latest_version,d.current_version,"
                    "v.source,v.version AS vnum FROM documents d JOIN versions v "
                    "ON v.document_id=d.id WHERE d.id=? AND d.workspace=? AND v.version=?",
                    (doc_id, workspace, want_version),
                ).fetchone()
        finally:
            conn.close()
        if row is None:
            self._json(404, {"error": {"code": "KB.NOT_FOUND"}})
            return
        if not self._read_guard(workspace, row["audience"]):
            return
        text = row["source"] or ""
        self._json(200, {
            "document_id": row["id"], "title": row["title"], "status": row["status"],
            "audience": row["audience"],
            "version": row["vnum"] or row["current_version"] or row["latest_version"],
            "chars": len(text),
            "text": text,
        })

    # ---------- 资料分类标签（演示层台账） ----------
    def _tag_table(self, conn) -> None:
        conn.execute("CREATE TABLE IF NOT EXISTS doc_tags("
                     "document_id INTEGER NOT NULL, workspace TEXT NOT NULL, tag TEXT NOT NULL, "
                     "PRIMARY KEY(document_id, tag))")

    def _tag_sync(self, conn, workspace: str) -> int:
        self._tag_table(conn)
        # 规则改过以后要能重算：只清「未分类」这层兜底标签，人工打的标签不动。
        conn.execute("DELETE FROM doc_tags WHERE workspace=? AND tag=?", (workspace, "未分类"))
        tagged = {row["document_id"] for row in conn.execute(
            "SELECT DISTINCT document_id FROM doc_tags WHERE workspace=?", (workspace,))}
        rows = conn.execute("SELECT id,title FROM documents WHERE workspace=?", (workspace,)).fetchall()
        added = 0
        for row in rows:
            if row["id"] in tagged:
                continue
            for tag in auto_tags(row["title"] or ""):
                conn.execute("INSERT OR REPLACE INTO doc_tags(document_id,workspace,tag) VALUES(?,?,?)",
                             (row["id"], workspace, tag))
                added += 1
        if added:
            conn.commit()
        return added

    def _admin_tags(self, method: str):
        if method == "POST":
            body = {}
            if getattr(self, "_raw_body", b""):
                try:
                    body = json.loads(self._raw_body or b"{}")
                except json.JSONDecodeError:
                    self._json(422, {"error": {"code": "CONSOLE.BAD_BODY"}})
                    return
            workspace = self._workspace_param(body=body)
            if not self._admin_guard(workspace):
                return
            try:
                doc_id = int(body.get("document_id"))
            except (TypeError, ValueError):
                self._json(422, {"error": {"code": "CONSOLE.BAD_DOCUMENT_ID"}})
                return
            tags = [str(t).strip()[:24] for t in (body.get("tags") or []) if str(t).strip()][:4]
            conn = self._db()
            if conn is None:
                return
            try:
                self._tag_table(conn)
                conn.execute("DELETE FROM doc_tags WHERE document_id=? AND workspace=?",
                             (doc_id, workspace))
                for tag in (tags or ["未分类"]):
                    conn.execute("INSERT OR REPLACE INTO doc_tags(document_id,workspace,tag) "
                                 "VALUES(?,?,?)", (doc_id, workspace, tag))
                conn.commit()
            finally:
                conn.close()
            self._json(200, {"document_id": doc_id, "tags": tags or ["未分类"]})
            return

        qs = parse_qs(urlparse(self.path).query)
        workspace = self._workspace_param(qs=qs)
        conn = self._db()
        if conn is None:
            return
        try:
            added = self._tag_sync(conn, workspace)
            # JOIN documents：只统计当前库里真实存在的文档，历史残留标签不进分类树。
            rows = conn.execute(
                "SELECT t.tag AS tag, COUNT(*) AS n, GROUP_CONCAT(t.document_id) AS ids "
                "FROM doc_tags t JOIN documents d ON d.id = t.document_id AND d.workspace = t.workspace "
                "WHERE t.workspace=? GROUP BY t.tag ORDER BY n DESC, t.tag", (workspace,)).fetchall()
        finally:
            conn.close()
        groups = [{"tag": row["tag"], "count": row["n"],
                   "ids": [int(x) for x in str(row["ids"]).split(",") if x]} for row in rows]
        self._json(200, {"workspace": workspace, "auto_tagged": added,
                         "total": sum(g["count"] for g in groups), "groups": groups})

    def _admin_batch(self):
        body = {}
        if getattr(self, "_raw_body", b""):
            try:
                body = json.loads(self._raw_body or b"{}")
            except json.JSONDecodeError:
                self._json(422, {"error": {"code": "CONSOLE.BAD_BODY"}})
                return
        workspace = self._workspace_param(body=body)
        if not self._admin_guard(workspace):
            return
        action = str(body.get("action") or "").strip()
        try:
            ids = [int(x) for x in (body.get("ids") or [])]
        except (TypeError, ValueError):
            ids = []
        if action != "archive" or not ids:
            self._json(422, {"error": {"code": "CONSOLE.BAD_BATCH",
                                       "detail": "action 只支持 archive，ids 不能为空"}})
            return
        conn = self._db()
        if conn is None:
            return
        versions: dict[int, int] = {}
        try:
            for doc_id in ids:
                row = conn.execute(
                    "SELECT COALESCE(current_version,latest_version) AS v FROM documents "
                    "WHERE id=? AND workspace=?", (doc_id, workspace)).fetchone()
                if row is not None and row["v"]:
                    versions[doc_id] = int(row["v"])
        finally:
            conn.close()
        archived, failed = [], []
        for doc_id in ids:
            if doc_id not in versions:
                failed.append({"id": doc_id, "status": 404, "code": "KB.NOT_FOUND"})
                continue
            status, payload = self._api_call("POST", "/documents/%d/archive" % doc_id,
                                             {"expected_version": versions[doc_id]})
            if status == 200:
                archived.append(doc_id)
            else:
                failed.append({"id": doc_id, "status": status,
                               "code": (payload.get("error") or {}).get("code", "")})
        self._json(200, {"action": action, "archived": archived, "failed": failed})

    def _admin_audit(self):
        if not self._admin_guard():
            return
        qs = parse_qs(urlparse(self.path).query)
        try:
            limit = min(max(int((qs.get("limit") or ["40"])[0]), 1), 200)
        except ValueError:
            limit = 40
        conn = self._db()
        if conn is None:
            return
        try:
            rows = conn.execute(
                "SELECT a.id,a.actor,a.action,a.document_id,a.request_id,a.created_at,"
                "d.title FROM audit a LEFT JOIN documents d ON d.id=a.document_id "
                "ORDER BY a.id DESC LIMIT ?", (limit,),
            ).fetchall()
        finally:
            conn.close()
        self._json(200, {"items": [dict(r) for r in rows]})

    def _admin_purge(self):
        body = {}
        if getattr(self, "_raw_body", b""):
            try:
                body = json.loads(self._raw_body or b"{}")
            except ValueError:
                body = {}
        workspace = self._workspace_param(body=body if isinstance(body, dict) else {})
        if not self._admin_guard(workspace):
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self._raw_body or b"{}")
            doc_id = int(body["document_id"])
            confirm = str(body.get("confirm_title") or "")
        except Exception:
            self._json(422, {"error": {"code": "CONSOLE.BAD_BODY"}})
            return
        conn = self._db()
        if conn is None:
            return
        try:
            doc = conn.execute("SELECT id,title,tenant FROM documents WHERE id=?", (doc_id,)).fetchone()
            if doc is None:
                self._json(404, {"error": {"code": "KB.NOT_FOUND"}})
                return
            if confirm.strip() != doc["title"]:
                self._json(409, {"error": {"code": "CONSOLE.CONFIRM_MISMATCH",
                                           "detail": "输入标题与文档标题不一致"}})
                return
            with conn:
                # 分类台账没有外键，硬删文档时一起清掉，否则 /__tags 会剩幽灵 id。
                try:
                    conn.execute("DELETE FROM doc_tags WHERE document_id=?", (doc_id,))
                except sqlite3.OperationalError:
                    pass
                chunks = conn.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,)).rowcount
                versions = conn.execute("DELETE FROM versions WHERE document_id=?", (doc_id,)).rowcount
                docs = conn.execute("DELETE FROM documents WHERE id=?", (doc_id,)).rowcount
                conn.execute(
                    "INSERT INTO audit(tenant,actor,action,document_id,request_id) VALUES(?,?,?,?,?)",
                    (doc["tenant"], self.headers.get("X-Identity") or "editor", "purged", doc_id, "console"),
                )
        finally:
            conn.close()
        self._json(200, {"deleted": {"documents": docs, "versions": versions, "chunks": chunks},
                         "title": doc["title"]})

    # ---------- 入库流水线（文件 → 解析 → 清洗 → 入库 → 原件台账）----------
    # ekb 只接受纯文本，所以「读文件」这一段必须在产品之外做；
    # 解析失败、正文为空、重复文件都在这里被拦住，产品那边永远只看到干净的正文。
    def _api_call(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = UrlRequest(API.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + IDENTITIES["editor"])
        try:
            with urlopen(req, timeout=180) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except HTTPError as exc:
            try:
                body = json.loads(exc.read() or b"{}")
            except json.JSONDecodeError:
                body = {}
            return exc.code, body
        except URLError as exc:
            return 502, {"error": {"code": "CONSOLE.UPSTREAM", "detail": str(exc.reason)}}

    def _admin_ocr(self):
        """Read one image with the local multimodal model and return Markdown text.

        ekb/ only ingests plain text, so scanned pages and screenshots get turned
        into text here first and then go through the normal import path.
        """
        workspace = self._workspace_param()
        if not self._admin_guard(workspace):
            return
        try:
            body = json.loads(self._raw_body or b"{}")
        except ValueError:
            self._json(422, {"error": {"code": "CONSOLE.BAD_JSON", "detail": "body 不是合法 JSON"}})
            return
        name = str(body.get("filename") or "image.png")[:200]
        data = str(body.get("data") or "")
        if not data:
            self._json(422, {"error": {"code": "CONSOLE.NO_IMAGE", "detail": "没有收到图片内容"}})
            return
        if len(data) > 24 * 1024 * 1024:
            self._json(413, {"error": {"code": "CONSOLE.TOO_LARGE",
                                       "detail": "单张图片上限约 18 MB"}})
            return
        if data.startswith("data:") and "," in data:
            data = data.split(",", 1)[1]
        if VISION_MODEL not in ollama_models(OLLAMA_BASE):
            self._json(503, {"error": {"code": "CONSOLE.VISION_MISSING",
                                       "detail": "本机没有 " + VISION_MODEL + "，先用 ollama create 建好再读图"}})
            return
        # 先腾显存再读图：本机还要留显存给常驻的语音机器人，8B 文本档常常正在占着 6 GB，
        # 直接加载 6.6 GB 的视觉档会挤到 CPU 上（慢十倍）甚至加载失败（502）。
        try:
            enforce_single(VISION_MODEL, OLLAMA_BASE)
        except Exception:  # noqa: BLE001 - 腾显存失败不该挡读图，Ollama 自己也会让路
            pass
        prompt = str(body.get("prompt") or OCR_PROMPT)
        payload = {
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": prompt, "images": [data]}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "num_ctx": 8192},
        }
        request = UrlRequest(
            OLLAMA_BASE.rstrip("/") + "/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=600) as response:
                answer = json.loads(response.read())
        except HTTPError as exc:
            self._json(502, {"error": {"code": "CONSOLE.VISION_HTTP",
                                       "detail": exc.read()[:400].decode("utf8", "replace")}})
            return
        except (URLError, OSError, ValueError) as exc:
            self._json(502, {"error": {"code": "CONSOLE.VISION_FAILED", "detail": str(exc)[:200]}})
            return
        text = str((answer.get("message") or {}).get("content") or "").strip()
        evicted = []
        # Multi-image imports pass keep=true for every page but the last one, so the
        # vision model is loaded once instead of once per page; the last call evicts it.
        if not body.get("keep") and CHAT_MODEL_CURRENT != VISION_MODEL:
            evicted = enforce_single(CHAT_MODEL_CURRENT, OLLAMA_BASE)
        self._json(200, {"model": VISION_MODEL, "filename": name, "text": text,
                         "chars": len(text), "unloaded": evicted,
                         "ms": round((time.perf_counter() - started) * 1000, 1)})

    def _admin_import(self):
        if FILE_STORE is None:
            self._json(503, {"error": {"code": "CONSOLE.STORE_MISSING"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._json(422, {"error": {"code": "CONSOLE.EMPTY_BODY"}})
            return
        if length > 120 * 1024 * 1024:
            self._json(413, {"error": {"code": "CONSOLE.TOO_LARGE",
                                       "detail": "单次导入上限 120 MB（含 base64 与压缩包膨胀）"}})
            return
        try:
            body = json.loads(self._raw_body)
        except json.JSONDecodeError:
            self._json(422, {"error": {"code": "CONSOLE.BAD_BODY"}})
            return
        workspace = self._workspace_param(body=body)
        if not self._admin_guard(workspace):
            return
        audience = str(body.get("audience") or "all")
        dry_run = bool(body.get("dry_run"))
        uploads = body.get("files") or []
        if not isinstance(uploads, list) or not uploads:
            self._json(422, {"error": {"code": "CONSOLE.NO_FILES"}})
            return

        # 压缩包先摊平：成员保留相对路径，之后与单文件走同一条导入路径。
        results: list[dict] = []
        items: list[dict] = []
        archives = 0
        for upload in uploads:
            name0 = str((upload or {}).get("filename") or "")
            if name0.lower().endswith(".zip"):
                archives += 1
                members, problems = expand_zip(name0, str((upload or {}).get("data") or ""))
                items.extend(members)
                results.extend(problems)
            else:
                items.append(upload or {})
        if not items and not results:
            self._json(422, {"error": {"code": "CONSOLE.EMPTY_ARCHIVE",
                                       "detail": "压缩包里没有可导入的文件"}})
            return
        batch = items[:300]
        # 超出的不能静默丢掉：留一条失败记录，让结果表说清「还有 N 个没进来」。
        if len(items) > len(batch):
            results.append({"filename": f"（另有 {len(items) - len(batch)} 个文件）",
                            "status": "failed",
                            "error": f"单次最多处理 {len(batch)} 个文件，超出部分未导入，请分成几批提交"})
        for item in batch:
            name = str((item or {}).get("filename") or "upload.bin")[:200]
            try:
                raw = base64.b64decode(str((item or {}).get("data") or ""), validate=True)
            except Exception:  # noqa: BLE001 - 坏 base64 只废这一个文件
                results.append({"filename": name, "status": "failed",
                                "error": "文件内容不是合法的 base64"})
                continue
            sha = FILE_STORE.sha(raw)
            seen = FILE_STORE.find(sha)
            if seen and seen.get("document_id"):
                results.append({"filename": name, "status": "skipped",
                                "reason": "同一份文件（sha256 相同）已经在库里",
                                "document_id": seen["document_id"], "title": seen.get("title")})
                continue
            try:
                parsed = parse_bytes(name, raw)
            except UnsupportedFile as exc:
                results.append({"filename": name, "status": "failed", "error": str(exc)})
                continue
            except Exception as exc:  # noqa: BLE001 - 一个坏文件不该让整批失败
                results.append({"filename": name, "status": "failed",
                                "error": f"解析失败：{type(exc).__name__}: {exc}"})
                continue

            if dry_run:
                results.append({"filename": name, "status": "parsed", "bytes": len(raw),
                                "sha256": sha[:12], **parsed.as_dict(),
                                "head": parsed.text[:160]})
                continue

            if parsed.chars < 20:
                FILE_STORE.save(filename=name, data=raw, kind=parsed.kind, title=parsed.title,
                                chars=parsed.chars, pages=parsed.pages, tables=parsed.tables,
                                warnings=parsed.warnings, status="failed", note="empty text",
                                workspace=workspace)
                results.append({"filename": name, "status": "failed",
                                "error": "清洗后正文太短（可能是扫描件，或只有图片/表头）",
                                **parsed.as_dict()})
                continue

            # 产品只接受纯文本（ekb/text.py:8 只认 .txt/.md），转换已经在上面解析时做完，
            # 这里把文件名换成 .txt；原始 docx/pdf/xlsx 留在原件台账里，按 sha256 可追溯。
            upload_name = Path(name).with_suffix(".txt").name or "import.txt"
            # 文件夹 / 压缩包保留目录名：不同科室的同名文件标题不打架。
            folder = PurePosixPath(name.replace(chr(92), "/")).parent.name
            title = parsed.title
            if folder and folder not in (".", "") and not title.startswith(folder):
                title = folder + " · " + title
            status, resp = self._api_call("POST", "/documents", {
                "workspace": workspace, "title": title, "text": parsed.text,
                "audience": audience, "filename": upload_name,
            })
            if status not in (200, 201):
                code = (resp.get("error") or {}).get("code") or f"HTTP {status}"
                FILE_STORE.save(filename=name, data=raw, kind=parsed.kind, title=parsed.title,
                                chars=parsed.chars, pages=parsed.pages, tables=parsed.tables,
                                warnings=parsed.warnings, status="failed", note=code,
                                workspace=workspace)
                results.append({"filename": name, "status": "failed", "error": code,
                                **parsed.as_dict()})
                continue

            document_id = int(resp["id"])
            ingest_status, ingest_resp = self._api_call(
                "POST", f"/documents/{document_id}/versions/1/ingest")
            indexed = ingest_status == 200 and ingest_resp.get("status") == "ACTIVE"
            note = "" if indexed else ((ingest_resp.get("error") or {}).get("code")
                                       or f"HTTP {ingest_status}")
            FILE_STORE.save(filename=name, data=raw, kind=parsed.kind, title=parsed.title,
                            chars=parsed.chars, pages=parsed.pages, tables=parsed.tables,
                            warnings=parsed.warnings, document_id=document_id,
                            status="indexed" if indexed else "created_not_indexed", note=note,
                            workspace=workspace)
            # 注意顺序：parsed.as_dict() 里也有 title（解析出的原始标题），必须让它先展开，
            # 再用带目录前缀的 title 覆盖，否则压缩包/文件夹导入的目录名会在结果里丢掉。
            results.append({"filename": name, "status": "imported", "document_id": document_id,
                            "bytes": len(raw), "indexed": indexed, "note": note,
                            **parsed.as_dict(), "title": title})

        summary = {
            "total": len(results),
            "imported": sum(1 for r in results if r["status"] == "imported"),
            "skipped": sum(1 for r in results if r["status"] == "skipped"),
            "parsed": sum(1 for r in results if r["status"] == "parsed"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
        }
        summary["archives"] = archives
        self._json(200, {"workspace": workspace, "audience": audience, "dry_run": dry_run,
                         "summary": summary, "items": results})

    def _admin_files(self):
        if FILE_STORE is None:
            self._json(503, {"error": {"code": "CONSOLE.STORE_MISSING"}})
            return
        qs = parse_qs(urlparse(self.path).query)
        workspace = self._workspace_param(qs=qs)
        if not self._admin_guard(workspace):
            return
        try:
            limit = min(max(int((qs.get("limit") or ["100"])[0]), 1), 500)
        except ValueError:
            limit = 100
        self._json(200, {"workspace": workspace,
                         "stats": FILE_STORE.stats(workspace),
                         "items": FILE_STORE.list(limit, workspace)})

    # ---------- 导出 / 采集 / 质量 ----------
    def _attachment(self, filename: str) -> dict:
        ascii_name = filename.encode("ascii", "ignore").decode().strip() or "export.bin"
        return {"Content-Disposition":
                "attachment; filename=\"" + ascii_name + "\"; filename*=UTF-8''" + quote(filename)}

    def _export_docs(self, scope: str, doc_id: int | None = None,
                     workspace: str | None = None, ids: list[int] | None = None) -> list[dict]:
        conn = self._db()
        if conn is None:
            return []
        try:
            sql = ("SELECT d.id,d.title,d.audience,d.status,"
                   "COALESCE(d.current_version,d.latest_version) AS v,v.source "
                   "FROM documents d LEFT JOIN versions v "
                   "ON v.document_id=d.id AND v.version=COALESCE(d.current_version,d.latest_version) ")
            where, params = [], []
            if scope == "doc" and doc_id is not None:
                where.append("d.id=?")
                params.append(doc_id)
            if scope == "docs" and ids:
                where.append("d.id IN (" + ",".join("?" * len(ids)) + ")")
                params.extend(ids)
            if workspace:
                where.append("d.workspace=?")
                params.append(workspace)
            if where:
                sql += "WHERE " + " AND ".join(where) + " "
            sql += "ORDER BY d.id"
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [{"id": row["id"], "title": row["title"] or "untitled",
                 "audience": row["audience"], "status": row["status"],
                 "version": row["v"], "text": row["source"] or ""} for row in rows]

    def _admin_export(self):
        qs = parse_qs(urlparse(self.path).query)
        workspace = self._workspace_param(qs=qs)
        scope = (qs.get("scope") or ["doc"])[0]
        fmt = ((qs.get("format") or [""])[0] or "").lower()
        try:
            doc_id = int((qs.get("document_id") or [""])[0])
        except ValueError:
            doc_id = None

        # 公开演示只允许导出单篇（正文本来就可见），整库打包和审计流水留给本机
        if PUBLIC_MODE and scope != "doc":
            self._json(403, {"error": {"code": "CONSOLE.PUBLIC_READONLY",
                                       "detail": "对外演示只允许导出单篇文档"}})
            return

        if scope == "audit":
            if not self._admin_guard(ORG_WORKSPACE):
                return
            conn = self._db()
            if conn is None:
                return
            try:
                rows = conn.execute(
                    "SELECT a.id,a.actor,a.action,a.document_id,d.title,a.request_id,a.created_at "
                    "FROM audit a LEFT JOIN documents d ON d.id=a.document_id ORDER BY a.id DESC"
                ).fetchall()
            finally:
                conn.close()
            body = csv_bytes([dict(row) for row in rows],
                             ["id", "actor", "action", "document_id", "title",
                              "request_id", "created_at"])
            name = "audit-" + time.strftime("%Y%m%d-%H%M%S") + ".csv"
            self._send(200, body, "text/csv; charset=utf-8", self._attachment(name))
            return

        try:
            ids = [int(x) for x in str((qs.get("ids") or [""])[0]).split(",") if x.strip()]
        except ValueError:
            ids = []
        docs = self._export_docs(scope, doc_id, workspace, ids)
        if scope == "doc":
            if not docs:
                self._json(404, {"error": {"code": "KB.NOT_FOUND"}})
                return
            doc = docs[0]
            # 单篇导出是只读操作：和读正文同一条判据，读者导出 audience=all 的文档即可
            if not self._read_guard(workspace, doc["audience"]):
                return
            meta = {"知识库": workspace, "文档 ID": doc["id"], "版本": "v" + str(doc["version"]),
                    "可见范围": doc["audience"], "状态": doc["status"],
                    "导出时间": time.strftime("%Y-%m-%d %H:%M")}
            stem = safe_name(doc["title"])
            if fmt == "docx":
                self._send(200, docx_bytes(doc["title"], doc["text"], meta),
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           self._attachment(stem + ".docx"))
            else:
                self._send(200, md_bytes(doc["title"], doc["text"], meta),
                           "text/markdown; charset=utf-8",
                           self._attachment(stem + ".md"))
            return

        if scope == "docs":
            # 多选导出同样是编辑者操作：选中的文档里可能混着 internal
            if not self._admin_guard(workspace):
                return
            if not docs:
                self._json(404, {"error": {"code": "KB.EMPTY", "detail": "没有选中任何文档"}})
                return
            body = zip_bytes(docs)
            name = workspace + "-selected-" + time.strftime("%Y%m%d-%H%M%S") + ".zip"
            self._send(200, body, "application/zip", self._attachment(name))
            return

        # 整库打包会连 internal 一起带走，只给编辑者
        if not self._admin_guard(workspace):
            return
        if not docs:
            self._json(404, {"error": {"code": "KB.EMPTY"}})
            return
        body = zip_bytes(docs)
        name = workspace + "-" + time.strftime("%Y%m%d-%H%M%S") + ".zip"
        self._send(200, body, "application/zip", self._attachment(name))

    def _admin_collect(self):
        if FILE_STORE is None:
            self._json(503, {"error": {"code": "CONSOLE.STORE_MISSING"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        body: dict = {}
        if length:
            try:
                body = json.loads(self._raw_body or b"{}")
            except json.JSONDecodeError:
                self._json(422, {"error": {"code": "CONSOLE.BAD_BODY"}})
                return
        workspace = self._workspace_param(body=body)
        if not self._admin_guard(workspace):
            return
        only = str(body.get("source") or "all")
        dry_run = bool(body.get("dry_run"))
        try:
            result = run_collect(root=ROOT, store=FILE_STORE, api_call=self._api_call,
                                 only=only, dry_run=dry_run, workspace=workspace)
        except Exception as exc:  # noqa: BLE001 - 采集失败要看得见，但不能把控制台打挂
            self._json(500, {"error": {"code": "CONSOLE.COLLECT_FAILED",
                                       "detail": type(exc).__name__ + ": " + str(exc)}})
            return
        self._json(200, result)

    def _admin_quality(self):
        qs = parse_qs(urlparse(self.path).query)
        workspace = self._workspace_param(qs=qs)
        if not self._admin_guard(workspace):
            return
        conn = self._db()
        if conn is None:
            return
        try:
            doc = conn.execute(
                "SELECT COUNT(*) total,"
                "COALESCE(SUM(status='ACTIVE'),0) active,"
                "COALESCE(SUM(status='ARCHIVED'),0) archived,"
                "COALESCE(SUM(status='DRAFT'),0) draft FROM documents WHERE workspace=?",
                (workspace,),
            ).fetchone()
            empty = conn.execute(
                "SELECT COUNT(*) c FROM documents d JOIN versions v "
                "ON v.document_id=d.id AND v.version=COALESCE(d.current_version,d.latest_version) "
                "WHERE d.workspace=? AND LENGTH(TRIM(COALESCE(v.source,'')))<20",
                (workspace,),
            ).fetchone()["c"]
            dup = conn.execute(
                "SELECT COUNT(*) c FROM (SELECT v.content_hash FROM versions v "
                "JOIN documents d ON d.id=v.document_id WHERE d.workspace=? "
                "GROUP BY v.content_hash HAVING COUNT(*)>1)",
                (workspace,),
            ).fetchone()["c"]
            nochunk = conn.execute(
                "SELECT COUNT(*) c FROM documents d WHERE d.workspace=? AND d.status='ACTIVE' "
                "AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.document_id=d.id "
                "AND c.version=COALESCE(d.current_version,d.latest_version))",
                (workspace,),
            ).fetchone()["c"]
            stuck = conn.execute(
                # SUPERSEDED 是写新版本后旧版本的正常状态（产品契约），不是卡住；
                # 只有 FAILED / PENDING 这类非终态才算需要处理。
                "SELECT COUNT(*) c FROM versions v JOIN documents d ON d.id=v.document_id "
                "WHERE d.workspace=? AND v.status NOT IN ('ACTIVE','ARCHIVED','DRAFT','SUPERSEDED')",
                (workspace,),
            ).fetchone()["c"]
            audit = conn.execute(
                "SELECT COUNT(*) c FROM audit a JOIN documents d ON d.id=a.document_id "
                "WHERE d.workspace=?", (workspace,)).fetchone()["c"]
        finally:
            conn.close()
        payload = {
            "workspace": workspace,
            "documents": {"total": doc["total"], "active": doc["active"],
                          "archived": doc["archived"], "draft": doc["draft"]},
            "issues": {"empty_text": empty, "duplicate_hash": dup,
                       "active_without_chunks": nochunk, "stuck_versions": stuck},
            "audit_events": audit,
        }
        if FILE_STORE is not None:
            payload["files"] = FILE_STORE.stats(workspace)
            payload["sources"] = [item for item in FILE_STORE.sources(200)
                                  if (item.get("workspace") or ORG_WORKSPACE) == workspace][:50]
            payload["runs"] = FILE_STORE.runs(5, workspace)
        else:
            payload["files"] = {"files": 0, "bytes": 0, "linked": 0, "failed": 0}
            payload["sources"] = []
            payload["runs"] = []
        self._json(200, payload)

    def _health(self) -> dict:
        try:
            with urlopen(f"{API.base}/health", timeout=5) as resp:
                return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001 - surfaced to the console, not raised
            return {"status": "unreachable", "detail": str(exc)}

    def _forward(self, method: str):
        parsed = urlparse(self.path)
        upstream = API.base + parsed.path[len("/api"):]
        if parsed.query:
            upstream += "?" + parsed.query
        length = int(self.headers.get("Content-Length") or 0)
        body = getattr(self, "_raw_body", b"") or None
        identity = self._client_identity()
        req = UrlRequest(upstream, data=body, method=method)
        req.add_header("Content-Type", self.headers.get("Content-Type") or "application/json")
        if identity in IDENTITIES:
            req.add_header("Authorization", "Bearer " + IDENTITIES[identity])
        started = time.perf_counter()
        # 重启后新进程接管监听的瞬间，第一个请求可能拿到 5xx/连接被重置。
        # 幂等方法给一次重试，避免把启动抖动当成产品错误转发给页面。
        attempts = 2 if method in ("GET", "HEAD") else 1
        for attempt in range(attempts):
            try:
                with urlopen(req, timeout=60) as resp:
                    status, payload = resp.status, resp.read()
                    ctype = resp.headers.get("Content-Type", "application/json")
                    rid = resp.headers.get("X-Request-ID")
                break
            except HTTPError as exc:
                status, payload = exc.code, exc.read()
                ctype = exc.headers.get("Content-Type", "application/json")
                rid = exc.headers.get("X-Request-ID")
                if status < 500 or attempt + 1 >= attempts:
                    break
            except URLError as exc:
                status, payload, ctype, rid = 502, json.dumps(
                    {"error": {"code": "CONSOLE.UPSTREAM", "detail": str(exc.reason)}}
                ).encode(), "application/json", None
                if attempt + 1 >= attempts:
                    break
            time.sleep(0.25)
        extra = {"X-Console-Ms": f"{(time.perf_counter() - started) * 1000:.1f}"}
        if rid:
            extra["X-Request-ID"] = rid
        self._send(status, payload, ctype, extra)


    def _forward_assistant(self, method: str, special: str):
        """Same-origin proxy to the assistant service: the page never sees a token."""
        if ASSISTANT is None:
            self._json(503, {"error": {"code": "CONSOLE.ASSISTANT_UNAVAILABLE"}})
            return
        parsed = urlparse(self.path)
        rest = special[len("/assistant"):] or "/"
        upstream = ASSISTANT.base + rest
        if parsed.query:
            upstream += "?" + parsed.query
        length = int(self.headers.get("Content-Length") or 0)
        body = getattr(self, "_raw_body", b"") or None
        identity = self._client_identity()
        req = UrlRequest(upstream, data=body, method=method)
        req.add_header("Content-Type", self.headers.get("Content-Type") or "application/json")
        req.add_header("X-Identity", identity)
        started = time.perf_counter()
        try:
            with urlopen(req, timeout=180) as resp:
                status, payload = resp.status, resp.read()
                ctype = resp.headers.get("Content-Type", "application/json")
        except HTTPError as exc:
            status, payload = exc.code, exc.read()
            ctype = exc.headers.get("Content-Type", "application/json")
        except URLError as exc:
            status, payload, ctype = 502, json.dumps(
                {"error": {"code": "CONSOLE.ASSISTANT_UPSTREAM", "detail": str(exc.reason)}}
            ).encode(), "application/json"
        self._send(status, payload, ctype,
                   {"X-Console-Ms": f"{(time.perf_counter() - started) * 1000:.1f}"})


def main() -> None:
    global API, RAG, ASSISTANT, IDENTITIES, IDENTITY_META, DB_PATH, FILE_STORE
    global CHAT_MODEL_CURRENT, CONFIG_PATH, ASSISTANT_CONFIG, OLLAMA_BASE
    global PUBLIC_MODE, PUBLIC_IDENTITY, PUBLIC_TOKEN, REPLY_OVERRIDES
    global ACCOUNTS_PATH, ACCOUNTS, AUTH_REQUIRED, ALLOW_HEADER_IDENTITY, DEV_SWITCH
    global SESSION_TTL, SESSION_IDLE, DEMO_PASSWORD_SET, DEMO_PASSWORD
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0, help="console port (default: random)")
    parser.add_argument("--no-seed-zh", action="store_true",
                        help="do not create the Chinese demo documents")
    parser.add_argument("--no-rag", action="store_true",
                        help="skip the local model gateway and fall back to BM25 + extractive")
    parser.add_argument("--models-dir", default=str(MODELS_HF),
                        help="directory holding bge-m3/ and bge-reranker-v2-m3/")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--chat-model", default=CHAT_MODEL)
    parser.add_argument("--rag-port", type=int, default=0)
    parser.add_argument("--data-dir", default="",
                        help="reuse a fixed runtime dir (knowledge.db / assistant.db persist across restarts); "
                             "default is a fresh runtime/console-<hex> per start")
    parser.add_argument("--reply", action="append", default=[], metavar="KEY=TEXT",
                        help="覆盖助手兜底话术，可重复：" + " / ".join(REPLY_KEYS) +
                             "。例：--reply out_of_scope='投诉请走公司工单系统'")
    parser.add_argument("--no-assistant", action="store_true",
                        help="do not start the conversational assistant service")
    parser.add_argument("--public", action="store_true",
                        help="对外演示模式：只读、身份钉死、管理端点关闭、按 IP 限流")
    parser.add_argument("--public-identity", default="reader",
                        help="public 模式下所有访客都以这个身份访问")
    parser.add_argument("--public-token", default="",
                        help="可选口令：链接必须带 ?k=<口令> 才打得开")
    parser.add_argument("--bind", default="", help="监听地址，默认 127.0.0.1；--public 时默认 0.0.0.0")
    parser.add_argument("--accounts", default="",
                        help="账号文件路径（默认 <run-dir>/accounts.json），不存在就按演示身份生成一份")
    parser.add_argument("--demo-password", default="",
                        help="把所有演示账号的口令统一设成这个值（面试演示图省事用；不给就每个账号随机生成）")
    parser.add_argument("--no-auth", action="store_true",
                        help="关掉登录（回落成请求头 X-Identity，仅本地调试）")
    parser.add_argument("--allow-header-identity", action="store_true",
                        help="允许用 X-Identity 头代替会话（默认关闭）")
    parser.add_argument("--strict-auth", action="store_true",
                        help="关掉 /__dev-login 一键切换，只允许账号口令登录")
    parser.add_argument("--session-ttl", type=int, default=480, help="会话绝对有效期（分钟）")
    parser.add_argument("--session-idle", type=int, default=120, help="会话闲置超时（分钟）")
    args = parser.parse_args()
    PUBLIC_MODE = bool(args.public)
    PUBLIC_IDENTITY = args.public_identity or "reader"
    PUBLIC_TOKEN = args.public_token or ""

    CHAT_MODEL_CURRENT = args.chat_model
    OLLAMA_BASE = args.ollama
    # --reply KEY=TEXT 覆盖助手兜底话术（写进 assistant-config.json 的 assistant.replies）
    for item in args.reply:
        if "=" not in item:
            parser.error("--reply 需要 KEY=TEXT 形式，例如 --reply out_of_scope='投诉请走工单系统'")
        key, _, value = item.partition("=")
        key = key.strip()
        if key not in REPLY_KEYS:
            parser.error("--reply 的键只能是：" + " / ".join(REPLY_KEYS))
        if value.strip():
            REPLY_OVERRIDES[key] = value.strip()
    # 默认每次起一份全新 runtime（演示永远干净）；带 --data-dir 则复用同一份库，
    # 重启后文档、版本、审计、未解决台账都还在——这是“重启持久化”的可测形态。
    if args.data_dir:
        run_dir = (ROOT / args.data_dir).resolve() if not Path(args.data_dir).is_absolute() else Path(args.data_dir)
    else:
        run_dir = ROOT / "runtime" / ("console-" + uuid4().hex[:8])
    run_dir.mkdir(parents=True, exist_ok=True)

    providers: dict = {}
    if not args.no_rag:
        RAG = RagService(
            args.rag_port or free_port(), run_dir / "rag_service.log",
            models_hf=Path(args.models_dir), ollama=args.ollama, chat_model=args.chat_model,
        )
        if RAG.start():
            providers = build_providers(RAG.port, args.chat_model)
        else:
            print(json.dumps({"rag_unavailable": RAG.detail}, ensure_ascii=False), flush=True)

    if providers:
        config_path = initialize_with_models(run_dir, providers)
    else:
        config_path = initialize(run_dir)
    tokens = json.loads((run_dir / "demo-credentials.json").read_text(encoding="utf8"))
    IDENTITIES = {key: value for key, value in tokens.items()}
    IDENTITY_META = identity_meta(tokens)

    AUTH_REQUIRED = not args.no_auth
    ALLOW_HEADER_IDENTITY = bool(args.allow_header_identity) or bool(args.no_auth)
    DEV_SWITCH = not args.strict_auth
    SESSION_TTL = max(600, int(args.session_ttl) * 60)
    SESSION_IDLE = max(300, int(args.session_idle) * 60)
    DEMO_PASSWORD_SET = bool(args.demo_password)
    DEMO_PASSWORD = args.demo_password or ""
    ACCOUNTS_PATH = Path(args.accounts) if args.accounts else (run_dir / "accounts.json")
    ACCOUNTS, fresh_lines = load_accounts(ACCOUNTS_PATH, args.demo_password)
    if fresh_lines:
        (run_dir / "accounts.txt").write_text(
            "\n".join(fresh_lines) + "\n", encoding="utf8")
        print("demo accounts (account password) -> " + str(run_dir / "accounts.txt"), flush=True)
        for line in fresh_lines:
            print("  " + line, flush=True)

    DB_PATH = run_dir / "knowledge.db"
    FILE_STORE = FileStore(run_dir)
    CONFIG_PATH = config_path
    API = Api(config_path, free_port(), run_dir / "server.log")
    API.start()
    seeded = [] if args.no_seed_zh else seed_chinese(tokens["editor"])
    personal_seeded = [] if args.no_seed_zh else seed_personal(tokens)

    ASSISTANT = None
    if not args.no_assistant:
        llm_base = (f"http://127.0.0.1:{RAG.port}/v1" if providers
                    else args.ollama.rstrip("/") + "/v1")
        assistant_config = write_assistant_config(
            run_dir, API.base, tokens, llm_base, args.chat_model, REPLY_OVERRIDES
        )
        ASSISTANT_CONFIG = assistant_config
        ASSISTANT = AssistantService(free_port(), assistant_config, run_dir / "assistant.log")
        if not ASSISTANT.start():
            print(json.dumps({"assistant_unavailable": ASSISTANT.detail},
                             ensure_ascii=False), flush=True)
            ASSISTANT = None

    port = args.port or free_port()
    bind = args.bind or ("0.0.0.0" if PUBLIC_MODE else "127.0.0.1")
    httpd = ThreadingHTTPServer((bind, port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(json.dumps({
        "console": url,
        "api": API.base,
        "database": str(run_dir / "knowledge.db"),
        "retrieval": "hybrid (bm25 + bge-m3) + bge-reranker-v2-m3 + " + args.chat_model
        if providers else "bm25 + extractive (no model gateway)",
        "rag": RAG.info() if RAG else None,
        "assistant": ASSISTANT.info() if ASSISTANT else None,
        "identities": [item["key"] for item in IDENTITY_META],
        "auth": {"required": AUTH_REQUIRED, "dev_switch": DEV_SWITCH,
                 "accounts_file": str(ACCOUNTS_PATH),
                 "passwords_file": str(run_dir / "accounts.txt"),
                 "session_ttl_minutes": args.session_ttl,
                 "session_idle_minutes": args.session_idle,
                 "demo_password": bool(args.demo_password)},
        "zh_documents_created": len(seeded),
        "personal_documents_created": len(personal_seeded),
        "workspaces": WORKSPACE_CATALOG,
        "file_store": FILE_STORE.stats(),
        "public": PUBLIC_MODE,
        "bind": bind,
        "public_url": (url if not PUBLIC_MODE
                       else "http://<host>:" + str(port) + "/"
                       + ("?k=" + PUBLIC_TOKEN if PUBLIC_TOKEN else "")),
        "note": "Ctrl+C to stop; the child processes are terminated with the console.",
    }, ensure_ascii=False, indent=2), flush=True)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…", flush=True)
    finally:
        httpd.shutdown()
        if ASSISTANT:
            ASSISTANT.stop()
        API.stop()
        if RAG:
            RAG.stop()


if __name__ == "__main__":
    main()
