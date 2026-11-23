#!/bin/sh
# EKB 容器入口。所有开关走环境变量，命令行参数只由这里拼装。
# 口令只在首启（/data 里还没有 accounts.json）时从 EKB_DEMO_PASSWORD 读一次并落盘，
# 之后重启不再把口令放进命令行参数（ps 里看不到）。
set -eu

: "${EKB_PORT:=8810}"
: "${EKB_DATA_DIR:=/app/runtime/console-live}"
: "${EKB_BIND:=0.0.0.0}"
: "${EKB_OLLAMA:=http://host.docker.internal:11434}"

mkdir -p "$EKB_DATA_DIR"

set -- --port "$EKB_PORT" --bind "$EKB_BIND" --data-dir "$EKB_DATA_DIR" --no-browser

if [ "${EKB_NO_RAG:-0}" = "1" ]; then
  set -- "$@" --no-rag
fi
if [ "${EKB_NO_ASSISTANT:-0}" = "1" ]; then
  set -- "$@" --no-assistant
fi
if [ -n "${EKB_OLLAMA:-}" ]; then
  set -- "$@" --ollama "$EKB_OLLAMA"
fi
if [ -n "${EKB_CHAT_MODEL:-}" ]; then
  set -- "$@" --chat-model "$EKB_CHAT_MODEL"
fi
if [ "${EKB_PUBLIC:-0}" = "1" ]; then
  set -- "$@" --public
  if [ -n "${EKB_PUBLIC_TOKEN:-}" ]; then
    set -- "$@" --public-token "$EKB_PUBLIC_TOKEN"
  fi
fi
if [ "${EKB_STRICT_AUTH:-0}" = "1" ]; then
  set -- "$@" --strict-auth
fi

if [ -f "$EKB_DATA_DIR/accounts.json" ]; then
  set -- "$@" --accounts "$EKB_DATA_DIR/accounts.json"
else
  if [ -z "${EKB_DEMO_PASSWORD:-}" ]; then
    echo "EKB_DEMO_PASSWORD 未设置：首启需要它来生成账号库（只需一次）。" >&2
    exit 2
  fi
  set -- "$@" --demo-password "$EKB_DEMO_PASSWORD"
fi

echo "ekb: console on ${EKB_BIND}:${EKB_PORT}, data=${EKB_DATA_DIR}" >&2
exec python /app/scripts/console.py "$@"