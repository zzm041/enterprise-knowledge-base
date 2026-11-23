# 部署手册（EKB 企业知识库）

单机可交付形态：一个容器、一份 SQLite 数据目录、可选本地模型。没有外部依赖，没有云服务，没有需要另外买的中间件。
本文只写「怎么交付、怎么验收、边界在哪」；机制与取舍见 README.md，评测口径见 evaluation/ 与 reports/。

---

## 1. 交付形态与硬边界

| 项 | 事实 |
| --- | --- |
| 进程 | 单机单进程（Python HTTP + 内嵌 ASGI），4 并发饱和 |
| 存储 | 单文件 SQLite（`knowledge.db`）+ 文件库目录，随 `/app/runtime` 卷持久化 |
| 检索 | BM25（词法，中文二元组）∪ 向量（bge-m3）RRF 合并 → bge-reranker-v2-m3 重排 → 抽取式作答，引用带字符偏移 |
| 模型 | 可选。不配模型 = 词法检索 + 抽取式引用；配 ollama 才有多模态读图与生成 |
| 身份 | 本地账号（PBKDF2，200,000 轮）+ 会话 Cookie；支持 `--public` 只读口令模式 |
| 权限 | `workspace × audience × role` 三层，检索前按 SQL 层过滤，生成后再复核一次 |
| 网络 | 默认只监听容器端口；不主动外联（除显式配置的 ollama 地址） |

**明确的非目标**：多副本/水平扩展、跨机房容灾、SSO/OIDC、库级多租户隔离、SaaS 多客户共用。这些是「落地阶段按客户环境做」的事，不在本包内。

---

## 2. 前置条件

- Docker 24+ 与 Docker Compose v2
- 4 vCPU / 8 GB RAM 起步（纯词法模式）；接本地模型建议 16 GB RAM + 8 GB 显存
- 磁盘：镜像约 1.1 GB，数据按 `knowledge.db` 增长（当前 100 篇 / 240 chunk ≈ 19 MB，见 §7）
- 可选：宿主机 ollama（`ollama serve`，默认 `http://host.docker.internal:11434`），模型 `qwen3:8b-q4km`（文本）、`qwen3.5:9b-vl-q4km`（读图）

---

## 3. 首次启动

```bash
cp deploy/.env.example deploy/.env
# 改两处：EKB_PORT、EKB_DEMO_PASSWORD
docker compose up -d --build
docker compose ps          # 等到 healthy
curl -s localhost:8810/healthz | python -m json.tool
```

- 首次启动若数据卷为空，用 `EKB_DEMO_PASSWORD` 生成四个演示账号并写入 `accounts.json`；此后重启改用 `--accounts`，口令不再出现在进程参数里。
- 想换成正式账号：用 `--strict-auth` + 自己的 `accounts.json` 挂进卷，不要再传 `EKB_DEMO_PASSWORD`。
- 页面入口 `http://<host>:8810/`，公开只读模式为 `http://<host>:8810/?k=<EKB_PUBLIC_TOKEN>`。

### 环境变量（deploy/entrypoint.sh 支持的全集）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `EKB_PORT` | 8810 | 容器内监听端口，compose 同时用它做宿主映射 |
| `EKB_BIND` | 0.0.0.0 | 监听地址 |
| `EKB_DATA_DIR` | /app/runtime/console-live | 数据目录，**必须在 /app 下**（ekb 要求数据库路径相对项目根） |
| `EKB_DEMO_PASSWORD` | 空 | 仅首次启动用于生成演示账号 |
| `EKB_OLLAMA` | http://host.docker.internal:11434 | 模型端点，不可达时自动退化为词法模式 |
| `EKB_CHAT_MODEL` | qwen3:8b-q4km | 生成模型 |
| `EKB_NO_RAG` | 0 | 1 = 不接向量与重排（纯词法，最省资源） |
| `EKB_NO_ASSISTANT` | 0 | 1 = 关掉助手层（意图分流/多轮） |
| `EKB_PUBLIC` | 0 | 1 = 公开只读模式 |
| `EKB_PUBLIC_TOKEN` | 空 | 公开模式口令 |
| `EKB_STRICT_AUTH` | 0 | 1 = 必须登录，禁用演示快捷入口 |

---

## 4. 探活语义（两个端点，别混用）

| 端点 | 用途 | 判定 | 鉴权 |
| --- | --- | --- | --- |
| `/healthz` | 容器 HEALTHCHECK、LB、K8s readiness | api 可达 + 库可读 + 数据目录可写 + 磁盘 ≥512 MiB，任一失败返回 **503 degraded** | 无 |
| `/api/health` | 页面与自检脚本 | 仅 API 进程存活 | 按身份头 |

`/healthz` 返回体：

```json
{"status":"ok","checks":{"api":true,"database":true,"data_dir_writable":true,"disk_free_mb":966893,"disk_ok":true},"active_documents":100,"data_dir":"/app/runtime/console-live"}
```

排障顺序：`api=false` → 看上游进程与端口；`database=false` → 库文件损坏或权限；`data_dir_writable=false` → 卷只读；`disk_ok=false` → 先扩盘，SQLite 写入已在失败边缘。

---

## 5. 备份与恢复

**备份**（在线，不锁库）：

```bash
docker compose exec ekb python scripts/backup.py \
  --db /app/runtime/console-live/knowledge.db \
  --out /app/runtime/backups --keep 7 --with-accounts
```

- 用 `VACUUM INTO` 做一致性快照，随后跑 `integrity_check`、统计 documents/versions/chunks/audit，并写一份 `knowledge-<UTC>.json` 清单（含 sha256）。
- `--keep N` 轮转保留最近 N 份；`--with-accounts` 一并快照账号文件。
- 实测：18,808,832 字节、sha256 `1a2781e3…f55e76`、116 文档 / 117 版本 / 749 chunk / 1248 审计行，integrity ok。

**恢复**（先停服务，再原子替换）：

```bash
docker compose stop ekb
docker compose run --rm ekb python scripts/restore.py \
  --db /app/runtime/console-live/knowledge.db \
  --from /app/runtime/backups/knowledge-20261123T120934Z.db \
  --manifest /app/runtime/backups/knowledge-20261123T120934Z.json
docker compose start ekb
```

- 恢复脚本会：核对清单 sha256 → `integrity_check` → 拒绝带 `-wal`/`-shm` 的在线库 → 旧库另存 `knowledge.db.pre-restore-<UTC>` → 暂存区 + `os.replace` 原子替换。
- 演练周期建议：每次导入大批资料后各备一次；每月做一次「备份→异机恢复→跑自检」的完整演练。

---

## 6. 升级与回滚

- 镜像按 digest/tag 固定（compose 里写死 `ekb-knowledge:1.0.0`），不要用 `latest`。
- 升级：`docker compose pull/build` → `up -d` → `curl /healthz` 为 ok → 跑 §8 验收清单。
- 回滚：把 tag 改回上一版 → `up -d`；**数据不需要回滚**（本版无破坏性 schema 变更；有变更时先在备份上验证）。
- 数据目录与镜像解耦，回滚镜像不动 `/app/runtime`。

---

## 7. 容量与性能边界（实测，不是估算）

| 项 | 实测值 | 条件 |
| --- | --- | --- |
| `GET /documents` | 240.7 rps，p95 50.4 ms | 8 并发，100 篇 |
| `POST /query` | 0.98 rps，p95 5.2 s | 4 并发即饱和（单进程 + 单模型） |
| 单文档上限 | 200,000 字节 | 超出 413 |
| 语料上限 | 5,000 chunk | 超出 `KB.CORPUS_LIMIT` 409 |
| 库体积 | 100 篇 / 240 chunk ≈ 19 MB | 线性增长 |

结论：**读多问少**的知识库场景够用；问答并发上不去是设计取舍（本地模型串行）。要横向扩，得把检索与生成拆成独立服务——那是落地阶段的事。

---

## 8. 交付验收清单（照着跑，全绿才算交付）

```bash
# 1 探活
curl -s localhost:8810/healthz | grep -q '"status": "ok"'
# 2 页面
curl -s -o /dev/null -w '%{http_code}\n' localhost:8810/            # 200
# 3 接口
curl -s -o /dev/null -w '%{http_code}\n' localhost:8810/api/health # 200
# 4 权限（未登录读文档应 401/403）
curl -s -o /dev/null -w '%{http_code}\n' 'localhost:8810/api/documents?workspace=handbook'
# 5 备份可跑
docker compose exec ekb python scripts/backup.py --db /app/runtime/console-live/knowledge.db --out /app/runtime/backups
# 6 页面自检 54 项（需登录 editor）
#    http://<host>:8810/?debug=1&checks=1
```

外加人工三条：问一个库内问题（应带引用与字符偏移）、问一个库外问题（应 fail-closed 说查不到，不是编）、切到 reader 身份看内部文档（应不可见）。

---

## 9. 已知限制（交付时必须如实告知）

1. 无 SSO/OIDC：账号在本地文件，接入企业身份要靠落地阶段对接。
2. 无 `/metrics`：只有 `/healthz` 与页面运维面板，接 Prometheus 需另做。
3. 单机单进程：`/query` 4 并发饱和；无队列、无多副本。
4. 连接器只有 file/http/rss 与手动导入：飞书/企微/Confluence/SharePoint 未做。
5. 审计可查但无留存策略与删除权流程（合规项，需按客户要求补）。
6. 无 LICENSE 与 SBOM 产物。
7. 演示语料是合成的（90 篇 + 公开法规 7 篇），不是真实企业数据；上线前必须换客户语料并重跑评测。
8. 多步检索（`--agentic`）默认关。开着时一句话里问两件事只会答上先命中的那件，
   剩下那半要再问一次——合并多个子问题的答案还没做，因为合并会拉长延迟且要额外调生成模型。
9. ReAct 规划（`--react`，需同时开 `--agentic`）默认关。开着时它**不抢常规路径**：先把
   改写句/原句/去虚词/子问题这几个确定性问法跑完，只有全都没答上才触发一次「救援」——
   模型用只读工具（与 MCP 同一套）摸一遍库、再补最多两个问法，实测这一次约 1.5–1.7 秒。
   答得上的问题不付这笔延迟；代价是最坏情况多查两次（见 §7 容量表）。
   接本地 Ollama 时规划层走原生 `/api/chat` 的 `think=false`：qwen3 系走 `/v1` 兼容端点会先把
   token 预算花在 thinking 上（实测 8.1 秒、正文为空、finish_reason=length），规划永远拿不到 JSON。

---

## 10. 出问题先看这里

| 现象 | 先查 |
| --- | --- |
| 容器反复重启 | `docker logs`；`KB.ALREADY_INITIALIZED` 是旧版 --no-rag 重启 bug，本版已修 |
| `KB.CONFIG_OUTSIDE_PROJECT` | `EKB_DATA_DIR` 不在 /app 下 |
| 页面全 403 | 当前身份是 reader，或会话过期（8h TTL / 2h idle） |
| 问答返回 knowledge_unavailable | 模型端点不可达，检查 `EKB_OLLAMA` 与 `host.docker.internal` |
| 问答返回 insufficient_knowledge | 语料没有可用片段（正常拒答，会进未解决台账） |
