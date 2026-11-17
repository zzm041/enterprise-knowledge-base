# EKB 可验证入口

> 想看**能点的**窗口而不是静态页：`demo/README.md`，双击 `demo\启动演示.cmd` 即开，
> 可现场换身份、改文档、提问、跑 10 项检查。本页是不可篡改的证据页，两者配一套用。

一个静态页面 + 两个脚本：把冻结时的**真实报告**变成「点开就能看、一条命令就能复算」的东西。
页面里的每个数字都能追到 `reports/` 或 `evaluation/` 的具体字段，页面本身由脚本生成，不是手抄的。

## 1. 打开（10 秒）

直接双击 `showcase/index.html`，或：

```powershell
Start-Process "E:\enterprise-knowledge-base\showcase\index.html"
```

单文件、离线、无 CDN、无外部字体、无网络请求。手机把 `index.html` 发过去也能看。

## 2. 三分钟怎么讲（面试现场）

| 时间 | 说什么 | 页面对应 |
| --- | --- | --- |
| 0:00–0:40 | 这不是 PPT：这是一次真实 HTTP 调用的请求和响应，10 项检查全过，含跨租户 404、写权限 403、归档后不再召回、进程重启后数据还在 | 01 板块 |
| 0:40–1:20 | 权限不是提示词约束：租户/工作区/可见范围被下推成 SQL 条件，生成前过滤、生成后复核（`permission_recheck: passed`），越权时宁可拒答（`insufficient_knowledge`） | 02 板块 + 03 板块 |
| 1:20–2:20 | 28 题人工合成集：Recall@4 / MRR@4 / nDCG@4 均 0.90，8 道负例 8/8 拒答；另有覆盖门槛消融——关掉门槛 0.95 但负例只拒 1/8，打开后 0.90 但 8/8 拒。这是取舍，不是「准确率提升」 | 04 板块 |
| 2:20–3:00 | 已知边界：词法检索（BM25）答不了同义改写，`paraphrase-hard` 就是留着的坏例；语料 16 篇、单进程、题目和语料同一人写，不算无偏基准 | 04 坏例 + 07 板块 |

**不要说的**：不说「端到端 RAG 准确率 90%」，不说是生产系统，不说多租户已上线。
**简历对齐**：简历 L39-L46 的 EKB 段，能对上 0.90 / 8/8 / 引用回溯 / 可见范围过滤；dense·hybrid 那两句不在本仓库证据里，现场按「门槛消融」讲。

## 3. 自己复算（三级，任选）

**L1 不装任何依赖**（只要 PowerShell）
```powershell
Get-Content reports\http-demo.json -Raw | ConvertFrom-Json | Select-Object status, cross_tenant_read_status, server_stopped
Get-Content reports\eval-20261028T184235Z-6e6351\results.json -Raw | ConvertFrom-Json | Select-Object -ExpandProperty metrics
Get-FileHash evaluation\corpus.json -Algorithm SHA256   # 应等于报告里的 corpus_sha256
```

**L2 一键四步核验**（会装依赖，约 1 分钟；不联网、不调用任何模型）
```powershell
$env:TEMP="$PWD\.cache\temp"; $env:TMP=$env:TEMP
$env:UV_CACHE_DIR="$PWD\.cache\uv"; $env:UV_PYTHON_DOWNLOADS="never"
uv sync --frozen
.\.venv\Scripts\python.exe scripts\verify_showcase.py
```
四步 = pytest（74 项）→ `ekb.evaluate`（复算 28 题指标）→ `scripts/demo.py`（真实 HTTP 10 项检查）→ `build_showcase.py --check`（页面与源报告逐字节对账）。
输出 `ALL CHECKS PASS` 且退出码 0 才算通过；任何一步漂移都非零退出。每次运行的明细写 `.cache/showcase-run/<时间戳>/`（已被 .gitignore 覆盖）。

**L3 页面是否被改过**
```powershell
.\.venv\Scripts\python.exe scripts\build_showcase.py --check
```
它重算 `data.json` 与 `index.html` 并与磁盘上的逐字节比对；页面里**不写墙上时钟时间**（时间取自 git 提交时间），所以任何人任何时间复算都应完全一致。

## 4. 每个数字的出处

页面 06 板块是完整的 claim → value → 文件 → 字段表。核心几条：

| 数字 | 值 | 文件 | 字段 |
| --- | --- | --- | --- |
| HTTP 检查 | PASS · 10 checks | `reports/http-demo.json` | `status`, `checks[]` |
| 引用偏移 | 0–50 · v1 | `reports/http-demo.json` | `answer.citations[0].start/end/version` |
| 生成后复核 | passed | `reports/http-demo.json` | `answer.trace.permission_recheck` |
| 跨租户读 | 404 | `reports/http-demo.json` | `cross_tenant_read_status` |
| 归档后 | insufficient_knowledge · citations=0 | `reports/http-demo.json` | `after_archive.status/citations` |
| Recall@4 / MRR@4 / nDCG@4 | 0.90 / 0.90 / 0.90 | `reports/eval-20261028T184235Z-6e6351/results.json` | `metrics.*` |
| 负向拒答 | 1.00（8 题） | 同上 | `metrics.negative_refusal_rate` |
| 门槛关/开消融 | 0.95·拒 0.125 → 0.90·拒 1.00 | `reports/eval-20261005T132700Z-fa0a73/results.json` + 6e6351 | `metrics`（对照组） |
| 语料指纹 | fc346fcd… | `evaluation/corpus.json` | sha256（对账报告 `corpus_sha256`） |
| 测试 | 74 项通过 · ruff 通过 | `reports/takeover-verification-20261029-01/summary.json` | `pytest.tests`, `ruff_check`, `ruff_format` |

防伪三道锁（`scripts/build_showcase.py` 内）：语料/题目 sha256 必须等于报告里记录的值，不等直接 SystemExit；指标只从报告读，脚本里没有手写分数的常量；`--check` 逐字节比对。

## 5. 边界（写在页面上，也写在这里）

- 检索模式是 **BM25 词法**（`retrieval_mode: bm25`），报告里 `embedding_fingerprint: null`、`reranker_model: null`——本次证据不包含 dense/hybrid 与重排的开销与收益。
- 28 题是**人工合成开发集**（`dataset_kind: hand-authored synthetic validation, not independent blind benchmark`），语料 16 篇，题目与语料同一人编写 → 不作无偏泛化结论。
- 指标是 4 篇候选内的 Recall/MRR/nDCG（`candidate_k 12`、`top_k 4`），不等于线上问答准确率。
- 延迟数字（`median 1.15ms`）是进程内小语料，不构成并发或吞吐结论。
- `paraphrase-hard` 是**已知失败**，保留在页面上：BM25 答不了「我忘了登录密钥，去哪找回」。这是这套方案的真实边界，不是待藏起来的缺陷。

## 6. 冻结纪律

`reports/` 与 `evaluation/` 是 Portfolio Freeze 的原始证据，本目录的脚本**只读**它们：
`verify_showcase.py` 运行前备份、运行后还原 `reports/http-demo.json` 与 `reports/latest-evaluation.txt`，新生成的 `reports/eval-<时间戳>/` 会被移到 `.cache/` 后删除。
新产物只有 `showcase/`（页面 + 数据）与 `scripts/{build,verify}_showcase.py`。
