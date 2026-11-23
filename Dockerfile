# syntax=docker/dockerfile:1
# EKB 企业知识库——可交付镜像。依赖从 uv.lock 精确安装（带 hash），运行期不需要联网拉包。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir "uv==0.11.24"

COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
 && uv pip install --system --no-cache --require-hashes -r /tmp/requirements.txt \
 && rm -f /tmp/requirements.txt

COPY ekb ./ekb
COPY assistant ./assistant
COPY scripts ./scripts
COPY demo ./demo
COPY evaluation ./evaluation
COPY pipeline ./pipeline
COPY deploy ./deploy

RUN useradd --create-home --uid 10001 ekb \
 && mkdir -p /app/runtime \
 && chown -R ekb:ekb /app /app/runtime \
 && chmod +x /app/deploy/entrypoint.sh

USER ekb
ENV EKB_PORT=8810 \
    EKB_DATA_DIR=/app/runtime/console-live

EXPOSE 8810
VOLUME ["/app/runtime"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request;u='http://127.0.0.1:%s/healthz'%os.environ.get('EKB_PORT','8810');sys.exit(0 if urllib.request.urlopen(u,timeout=4).status==200 else 1)"]

ENTRYPOINT ["/app/deploy/entrypoint.sh"]