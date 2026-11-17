# EKB 演示窗口（可操作）

不是截图、不是录屏、不是静态页：**一个真实的本地服务 + 一个能点的控制台**。
每个按钮都打到独立 Uvicorn 进程，状态码是服务真返回的。

## 启动（链接和口令都是固定的）

| 要干什么 | 双击 | 打开 | 登录 |
| --- | --- | --- | --- |
| 本地完整演示 | `demo\启动演示.cmd` | **http://127.0.0.1:8810/** | 四个账号通用口令 **`ekb-demo-2026`** |
| 对外只读演示 | `demo\启动公开演示.cmd` | http://127.0.0.1:8809/ （口令 `EKBDEMO`） | 访客固定 reader，没有登录层 |

登录窗口没有单独的链接：**打开 8810 就是登录层**（账号下拉 + 口令 + 四个演示快捷入口）。
口令就写在登录卡底部，不用再去翻日志；`runtime\console-live\accounts.txt` 里有同样的说明。

两个脚本都是把参数写死的：

```bat
.venv\Scripts\python.exe scripts\console.py --port 8810 --data-dir runtime\console-live --demo-password ekb-demo-2026 %*
```

- `--port 8810` → 链接每次都是 `http://127.0.0.1:8810/`；想换端口就在后面加 `--port 8811`（会覆盖前面的）。
- `--data-dir runtime\console-live` → **同一份库**，重启不丢资料和版本（不带这个参数才是每次全新库）。
- `--demo-password` → 四个账号共用；不传就每次随机生成，写在运行目录的 `accounts.txt`。
- 端口被占用时脚本会直接提示「port 8810 is already in use」并退出，不会糊里糊涂起一个随机端口。
- 关掉那个黑窗口，四个子进程一起停。

手工起（调试用）：

```powershell
cd E:\enterprise-knowledge-base
.\.venv\Scripts\python.exe scripts\console.py --no-browser --port 8810 --data-dir runtime\console-live --demo-password ekb-demo-2026
```

它会做六件事（四个进程，全在本机回环）：

1. 起**模型网关** `scripts/rag_service.py`：一个端口同时提供 `/v1/embeddings`（bge-m3，1024 维，CPU）、`/v1/rerank`（bge-reranker-v2-m3）、`/v1/chat/completions`（转发本机 Ollama 的 `qwen3:8b-q4km`，并把模型吐出的 `citation_chunk_ids` 归一成整数、把 `status` 同义词折叠——产品的 pydantic 契约是 strict 的）；
2. 库是空的才建合成语料库（`evaluation/corpus.json` 16 篇），**入库时就把 bge-m3 向量写进去**；带 `--data-dir` 复用库时这一步跳过（启动日志里 `zh_documents_created: 0`）；
3. 起 Uvicorn（随机回环端口，`EKB_CONFIG` 指向运行目录写的 config.json，里面 `embedding`/`reranking`/`generation` 三段都配到网关）；
4. 起同源代理（注入 Bearer，页面里没有令牌）+ 通过公开 HTTP 灌 84 篇中文制度文档到企业库、2 篇个人笔记到每个人的个人库（同样带向量）；
5. 起**助手服务** `assistant.api`（`/api/assistant/*` 反代到它）：会话、意图路由、多轮改写、拒答兜底、未解决问题台账，配置由本次运行写在 `runtime/console-<id>/assistant-config.json`，页面拿不到任何令牌；
6. 打开浏览器。

关掉命令行窗口四个进程一起停；运行产物在 `runtime/console-live/`（已 gitignore，含 `rag_service.log`、`accounts.txt`、`assistant-config.json`、`knowledge.db`）。

> 不带脚本手工起时默认随机端口 + 全新库，适合一次性演示；不想自动开浏览器用 `--no-browser`。
> **没有模型环境也能跑**：`--no-rag` 回退到原来的 BM25 + 抽取式（页面会明说「未接模型网关」）。
> 模型路径默认 `E:/ai-models/property-ai/models-hf`（`--models-dir` 可改），torch 解释器默认
> `D:/property-ai/tmp/selfhost-ai-runtime/bge-142-cpu/.venv/Scripts/python.exe`。**全程不下载、不联网**（`HF_HUB_OFFLINE=1`）。

## 窗口里能做什么

打开就是**知识库产品的常规形态**：左侧文档库，中间对话，答案下面挂引用卡片。
工程细节（trace、原始 JSON、请求日志、接口自检）全部收在右上角 ⚙ 打开的**调试面板**里，默认不出现。

| 区域 | 操作 | 能看到什么 |
| --- | --- | --- |
| 顶栏 | 账号按钮（右上角头像 + 姓名 + 角色） | 点开是登录层：四个演示账号，口令见启动日志与 `runtime/<id>/accounts.txt`。登录后可见范围按角色生效：`internal` 文档只有 staff、editor 看得见；没登录时整站 401 |
| 左侧 × 知识库列表 | 点库名切换作用域 | **企业库（员工手册）+ 我的资料**两层。reader 侧企业库 84 篇、自己的资料 2 篇；editor 看企业库 98 篇 + 朱佐民的资料。切库即换文档集与检索范围 |
| 左侧 × 搜索 / 筛选 | 搜索框 + 筛选（已索引 / 全部 / 草稿 / 已归档） | 每行带状态徽章、版本号、`internal` 标记 |
| 左侧 × 文档行 | 点一行 → 右侧「文档详情」 | 状态、当前版本、文档 ID、版本表（含 content_hash 与每版状态）；下面是资料管理四件事：**编辑正文 / 重新索引 / 下线（或恢复）/ 删除** |
| 左侧 × ＋新建文档 | 标题 + 正文 + 可见范围 | 创建即自动入库并索引，结束后直接打开详情；**只读身份时按钮直接禁用**并写明原因 |
| 中间 × 对话 | 输入问题回车，或点空状态里的示例卡 | 答案正文（本地 8B 依据选中片段生成）+ 引用 chip（编号 · 标题 · 版本）+ 一行**真实链路**（召回来自 BM25 多少条 / 向量多少条 · 谁重排 · 谁生成）；点 chip → 右侧显示原文片段、文档 ID、字符偏移、可见范围复核 |
| 中间 × 拒答 | 问库里没有的、或没有权限看的 | 不报错，显示「知识库中没有可支撑的内容」，**不给任何引用**（fail-closed） |
| 右侧 × 问答记录 | 右栏第三个页签 | 这一轮问过的每个问题：状态、引用条数、耗时；点一条跳到它的首个引用片段（没有引用就停在记录页） |
| 顶栏 × ☰ / ▤ | 收起或展开左侧文档栏 / 右侧检查器（也可 `Ctrl+B` / `Ctrl+J`，或双击栏边界） | 只看对话时把两边收掉，中间内容自动居中；宽度与收起状态记在浏览器本地，下次打开还是上次的样子 |
| 栏边界 | 拖动左右两条分隔线 | 左栏 220–460px、右栏 300–620px 自由调，双击即收起 |
| ⚙ 调试面板 | 右上角齿轮 | 请求日志（时间/请求/身份/状态/耗时，401 未带令牌、403 越权、404 跨租户、422 不支持格式都能看到）、最后一次回答的 trace 与原始 JSON、「运行 54 项」接口自检（登录/会话/限流/登出、助手契约、资料增删改、正文与权限、入库流水线：导入/去重/只读拦截/可检索、导出 md·docx·zip·csv、采集 dry run、质量看板、分类标签、版本对比、批量操作、双层库隔离）、助手运营面板 |

同一组 54 项断言也能不开浏览器复放（给 CI 用，退出码 0 = 全过）：

```powershell
# 不给 --password 时走 /__dev-login 演示开关（同样是真会话，只是不经口令）
./.venv/Scripts/python.exe scripts/verify_checks.py --base http://127.0.0.1:8810 --password ekb-demo-2026 --json .cache/verify-checks-54.json
```

脚本先登录拿 `ekb_sid` Cookie 再发请求——它不再用 `X-Identity` 自称身份，因为那条路已经关掉了（见下一节）。

URL 参数：`?ask=…` 预置问题、`?doc=<id>` 直接打开文档、`?new=1` 打开新建弹窗、`?tab=cite|doc|log` 指定右栏页签、`?side=off`/`?insp=off` 收起对应栏、`?debug=1` 打开调试面板、`?debug=1&checks=1` 加载即跑完 54 项自检（页面按钮跑 53 条：比脚本少一条需要手输口令的 `login ok`）（我自测用的同一条路径）、`?ws=personal-wangfang` 直接进个人库、`?pick=9,17` 预选文档（批量条直接出现）、`?diff=9@2` 直接打开 v1→v2 的版本对比。

### 窗口布局（这一版重做的部分）

- **输入框和下面那排按钮钉在视口底部**：`html,body{height:100%;overflow:hidden}` + `.shell{height:calc(100dvh - 58px)}` + 中间会话区自己滚（`.thread{flex:1 1 auto;overflow:auto}`）。滚鼠标只滚消息，不会把输入框推出屏幕。
- **左右两栏都能调、能收**：两条 6px 分隔线可拖（`.sidebar` 写 `--side-w`、`.inspector` 写 `--insp-w`，存 `localStorage`），双击或按 ☰ / ▤ 收起；两栏都收起时中间内容居中。
- **右栏第三个页签「问答记录」**：把这一轮的问-答-引用列成卡片，点一条回到它的引用原文——使用者看到的是「资料 + 记录」，不是 trace。
- 窄窗口（≤1360px）自动隐藏输入框里的技术提示行，≤1180px 收窄右栏，保证发送键不被挤走。

### 资料查看与窄屏（本轮补的）

- **正文预览**：右栏「文档详情」除了版本表，还会懒加载这篇文档当前版本的正文（段落原样、可复制、显示字数）。读者身份能看 \`all\` 文档的正文，看 \`internal\` 会提示「可见范围不让当前身份看正文」——判权按文档 audience，不是按按钮。
- **搜索按标题或 ID**：搜索框支持 \`#9\` 或 \`9\` 直接定位文档，列表上方显示「共 N 篇 / 筛出 X 篇」。
- **窄屏（≤900px）**：三栏塌成单栏——侧栏与右栏变覆盖层（右上角 ☰ / ▤ 打开），示例卡单列，输入框仍钉在视口底。
- 已踩的坑：headless Edge 的 \`--window-size=414\` 只决定截图尺寸，布局视口会被 DPI 放大（实测 \`innerWidth=496\`），**窄屏截图不能直接当布局证据**，要先量 \`innerWidth\`。

### 分类 / 版本对比 / 批量 / 扫描件（本轮补的四条缺口）

- **分类标签**：左侧栏「分类」按标题关键词自动打 14 组标签（IT·账号与数据 / 安全·应急与合规 / 人事·考勤休假 / 财务·报销与资金 / 采购·供应商 / 行政·办公与资产 / 培训与发展 …），规则在 `scripts/console.py` 的 `TAG_RULES`；100 篇演示文档 **未分类 0**。点一个标签即筛出该组文档，editor 还能在文档详情里手工改标签（最多 4 个，写 `doc_tags` 表）。
- **版本对比**：文档详情每个版本行都有「对比上一版」，弹窗按行做 LCS 对齐，绿色是新增、红色是删除，标题写「v1 → v2 · 新增 3 行 / 删除 0 行」。正文取 `GET /api/__source?document_id=N&version=V`；旧版本正文一直留在 `versions` 表里，下线不会删。
- **批量操作**：侧栏文档行前有勾选框，勾上出现「已选 N 篇 / 批量下线 / 批量导出 / 清除」——批量下线走 `POST /api/__batch`（editor only），批量导出打成 `workspace-selected-<时间戳>.zip`（含 manifest）。
- **扫描件与表格**：PDF 文字层字数 < 50 时判为扫描件，逐页用 **RapidOCR**（本地 onnxruntime，不联网）识别，正文里标成「## 第 N 页（OCR）」；带线框的表格用 **pdfplumber** 还原成表格文本。实测整页图片版 PDF → 88 字 / 3,895 ms，画线框表格 → 1 张表 / 178 字。纯文字 PDF 不走 OCR，不会平白慢好几秒。

> 诚实说明：OCR 只认印刷体，手写与歪斜页会掉字；表格还原靠线框识别，无线框的表会退化成按文本摊平。这两件事都不是「全格式通吃」。

### 多模态读图（图片 / 扫描页 / 截图）

导入也接受 `png / jpg / jpeg / webp / bmp`：控制台把图片交给本机 **Qwen3.5-9B-VL**
（q4_K_M 5.7GB + clip 投影 0.92GB，`ollama create` 用两行 `FROM` 建出来），先转写成 Markdown，再走正常入库流程。

- 端点：`POST /api/__ocr`（editor only，reader 403），body `{filename, data(base64), prompt?}`，返回 `{text, chars, ms, model}`。
- 实测：720×200 的自检图（写着 `OCR CHECK …`）→ **874.5 ms / 22 字**，文字原样读出；4.8 KB 扫描样张 → 477.6 ms / 66 字。
- 自检 44 项里的 `ocr read` 与 `ocr forbidden` 就是这两条：读图要出字，只读身份要 403。
- 边界：这是「转写」不是「理解」。密集小字、手写、歪斜拍照会掉字；表格能否还原取决于模型对版面的判断，不如线框表格可靠。
- 显存：读图档常驻约 6.6GB；读完这张图立刻卸载（同一时刻只留一个模型），不会和文本档、也不会和本机常驻的语音机器人抢显存。
- 批量：一次选多张图时，除最后一张都带 `keep`，中途不卸载 —— 第一张约 4–5 秒（含加载），后面每张 0.5–0.9 秒；最后一张读完统一卸载。

## 生成模型档位（可切换）

演示栈默认是 **Qwen3-8B（q4_K_M）**。可选档位只有两个：文本问答用 Qwen3-8B，读图 / 扫描件用 **Qwen3.5-9B-VL**（2026-02 发布的原生多模态，图 + 视频，Vision Encoder）。页面右上 ⚙ →「生成模型档位」可以当场切换；
切换会重启检索网关、知识库服务和助手（实测 9.7–11.9 秒），因为模型名写在 ekb 读取的 provider 配置里，不重启不生效。
两档互斥：切到哪一档，另一档立刻从显存里卸载（`/api/ps` 只剩当前档），任何时刻只有一个模型在 GPU 上——这台机器还有常驻语音机器人，不能让模型叠加。
读图时自动调用 VL、读完自动卸载，不必手动切回文本档。

| 档位 | 权重 | 常驻显存 | 28 题 hybrid + 重排 | 中位延迟 | 备注 |
|---|---|---|---|---|---|
| **Qwen3-8B（文本默认）** | q4_K_M · 5.0GB | ~6.1GB | Recall 1.00 · 负向 8/8 · **坏例 0** | **1,622 ms** | 唯一 28 题零坏例，默认档 |
| Qwen3.5-9B-VL（多模态 · 2026-02） | q4_K_M + clip 投影 · 6.6GB | ~5.9–6.6GB | Recall 1.00 · 负向 **7/8** · 坏例 1 | 1,885 ms（文本）/ 0.5–0.9 s 每张（读图） | 唯一能读图的档；读完自动卸载 |

**已下线（2026-11-16 按用户要求）**：Qwen2.5-14B 与 Qwen3.5-9B 文本档不再出现在可选档位里（`POST /__models` 传它们返回 422 `CONSOLE.UNKNOWN_MODEL`）。
权重文件和四份历史评测报告都留在原处（`evals-rag/model-qwen25-14b/`、`model-qwen3-8b/`、`model-qwen35-9b/`、`model-qwen35-9bvl/`），只是不再可选。
同一套语料（`evaluation/corpus.json` 16 篇）、同一批 28 题、同一个 reranker。**这是 28 题合成集的对比，不是盲测。**

为什么不是 `ollama pull`：本机到 `registry.ollama.ai` 的 TLS 握手直接超时，7 个常见代理端口也不通；
权重从 ModelScope 取 GGUF，再在本地建标签：

```bash
curl -L -C - -o Qwen3-8B-Q4_K_M.gguf \
  "https://www.modelscope.cn/models/Qwen/Qwen3-8B-GGUF/resolve/master/Qwen3-8B-Q4_K_M.gguf"
printf 'FROM ./Qwen3-8B-Q4_K_M.gguf\nPARAMETER num_ctx 8192\n' > Modelfile.qwen3
ollama create qwen3:8b-q4km -f Modelfile.qwen3
```

Qwen3 系列默认开思考：走 OpenAI 兼容层时一问 15 秒（约 1,400 个 think token），`think=false` 只有原生 `/api/chat` 认。
所以 `scripts/rag_service.py` 对白名单模型走原生端点、再把响应套回 OpenAI 形状，同一问从 15s 降到 0.8s；
`/v1` 兼容层那条路只在非 Qwen3 模型上用。

解析口径：模型常把引用写成 `"chunk 101"` / `"#3"` / `3`，`normalise_answer()` 统一转 int；status 的 success/ok 折叠成 answered；
引用越界仍会被 ekb 判 `KB.INVALID_CITATION`。这些适配只在演示层，`ekb/` 一行没改。

自检 40 项里 `archive exclusion` 断言的是「归档后引用里不含这篇」，不是「模型必须拒答」；拒答由 `refusal` 那项单独把关。量化档对库里没有的随机问题偶尔会答一句，那是生成侧行为，归档失效才是产品问题（PA-222）。

## 账号登录（会话 Cookie，不再靠请求头自称身份）

之前的实现有个硬伤：身份来自请求头 `X-Identity`，那是**客户端说自己是睡**。任何 curl 改个头就是 editor，页面上的「身份下拉」也不叫登录——它只是换个头。这一轮换成了真的账号体系（停在演示级，不假装 SSO）。

**流程**：`POST /api/__login {account, password}` → 校验 PBKDF2-SHA256（20 万轮、每账号独立盐）→ 发 `Set-Cookie: ekb_sid=…; HttpOnly; SameSite=Lax` → 之后每个请求由服务端用这个 sid 查会话表得到身份；页面拿不到令牌，也改不了身份。

| 机制 | 实现 | 实测 |
| --- | --- | --- |
| 口令存储 | `pbkdf2_hmac('sha256', password, salt, 200000)` + `secrets.compare_digest`；账号文件 `runtime/<id>/accounts.json`，明文只在首次生成时写一次 `accounts.txt` 并打印 | 口令错 → 401 `CONSOLE.BAD_CREDENTIALS`「账号或口令不对」 |
| 会话 | 服务端 `SESSIONS`（sid → account/tenant/ip/issued/seen）；默认 8 小时绝对过期、2 小时无操作过期；`--session-ttl` / `--session-idle` 按分钟调 | `/__me` 返回 `expires_at` 与 `idle_expires_at` |
| 失败限流 | 键是「ip + 账号」，连续 5 次失败锁 60 秒 | 连错 6 次 → 429 `CONSOLE.LOGIN_LOCKED` |
| 登出 | `POST /api/__logout` 删会话 + 清 Cookie | 登出后 `/__me` → 401 |
| 审计 | 登录成功 `login`、失败 `login_failed`、一键切 `dev_login`，全部写产品的 `audit` 表（右上角「变更记录」能看到） | 登录一次即多一条 audit 行 |
| 未登录 | 除 `/health` 与只够渲染登录层的 `/__meta`，其余一律 401 | 无 Cookie 读 `/documents`、`/__source` 都是 401 |
| 一键切身份 | `POST /api/__dev-login {account}`，**演示开关**：`--strict-auth` 可关掉（关掉后只认口令），每次切换写 `dev_login` 审计 | 关掉后返回 403 `CONSOLE.DEV_SWITCH_OFF` |

**为什么留一键切**：这是给你当面讲权限差异用的——自检里「换 reader 去写就是 403」也是真的切一次会话再切回来，不是伪造请求头。生产形态应该只有口令登录：`--strict-auth` 就是那个开关。

演示口令：启动时 `--demo-password ekb-demo-2026` 给四个账号统一口令；不给就每个账号随机生成（只打印一次）。

> 仍然**没有**：SSO / OIDC / 扫码登录、自助注册、按人审批、细粒度共享。面试被问到就直说这一层是演示级账号；换真实身份源要做的是把 `_client_identity()` 换成从网关注入的 JWT 校验，接口形状已经留好了。

## 兜底话术可配置（改一句就变，不用改代码）

助手那四句兜底——**查不到（拒答）/ 服务不可用 / 无权限 / 超出范围**——是部署单位的口吻，不该写死在代码里。企业真实做法也是这样：FastGPT 把「知识库检索无匹配数据时拒绝回答」做成配置项（[官方故障排查](https://fastgpt.cn/troubleshoot/fastgpt-deny-answer-match)），Onyx 在 [PR #14626](https://github.com/onyx-dot-app/onyx/pull/14626) 里给「检索成功但合成没有答案」补了专门的 fallback response。差别只在谁来定这句话：判断逻辑留在代码，文案交给部署方。

三个入口，都是同一份配置（`assistant-config.json` 的 `assistant.replies`）：

| 方式 | 怎么用 | 实测 |
| --- | --- | --- |
| 启动参数 | `./.venv/Scripts/python.exe scripts/console.py --port 8810 --data-dir runtime/console-live --reply "out_of_scope=投诉请走公司 OA 的工单系统（OA → 工单 → 行政），这里只回答制度与流程。"` | 起来问「我要投诉，转人工」→ `out_of_scope`，回复就是这句 |
| 调试面板 | ⚙ 调试面板 →「兜底话术」四个输入框 → 保存并生效（editor 才可改） | 改拒答那一句 → **818.8 ms** 生效，助手进程重启、不用重启整个栈 |
| 接口 | `GET /api/__replies` 读当前生效值；`POST /api/__replies {"replies": {"refusal": "..."}}` 写（reader → 403） | 某一句清空＝恢复那句的默认；`overridden` 列出被覆盖的键 |

改完之后 `GET /api/__replies` 会告诉你哪些键被覆盖（`overridden`）、哪些还是默认——**演示时能当场证明「这句话是我们配的，不是代码写死的」**。

## 对外远程演示（把窗口发给别人看）

本机演示默认只监听 127.0.0.1，四个身份可自由切换——那是给你当面讲权限差异用的。
**一旦要发到公网，必须开 --public**，否则 X-Identity 这个「客户端说自己是谁」的头
等于把 editor 权限发给每个访客（改个头就能删库）。

```powershell
# 本机：只读公开演示 + 口令，监听所有网卡
.\.venv\Scripts\python.exe scripts\console.py --no-browser --port 8809 --public --public-token 换成你的口令
# 访问（局域网或隧道）：http://<host>:8809/?k=换成你的口令
```

--public 下服务端做了六件事，都有实测：

| 护栏 | 行为 | 实测 |
| --- | --- | --- |
| 身份钉死 | 忽略客户端 X-Identity，所有请求都用 --public-identity（默认 reader） | 伪造 editor 拿到 84 篇、internal 0 篇 |
| 管理端点关闭 | /__source /__files /__export /__audit /__quality /__collect /__purge /__restart 全 403 | 8 个端点逐个 403 |
| 只读 | 只放行 POST /query、/assistant/chat、/assistant/feedback | 建文档 / 删除 / 重启 / 采集 全 403 |
| 口令 | 链接必须带 ?k=，否则 401（换成 cookie 后不再需要） | 不带 k → 401 |
| 限流 | 每 IP 每分钟 30 次提问，超了返回 429 | 并发 40 次 → 29 个 429 |
| 页面收敛 | 身份下拉换成「访客 · 只读」，藏掉新建 / 导入 / 调试 / 变更记录 / 资料库 | 截图 .cache/pub-home2.png |

### 三条通道（按上手速度排）

1. **Cloudflare 快速隧道**（免费、不用域名、不用公网 IP，出站连接）：
   ```powershell
   winget install --id Cloudflare.cloudflared
   cloudflared tunnel --url http://127.0.0.1:8809
   ```
   拿到 https://xxxx.trycloudflare.com 后拼上 ?k=口令 就是可发的链接。关掉进程链接立刻失效。
2. **有域名**：cloudflared named tunnel 或 nginx 反代（TLS 在边缘终止，本机仍是 http）。
3. **有 VPS**：ssh -R 8809:127.0.0.1:8809 user@vps，再在 VPS 上 nginx 反代到该端口。

### 这套东西的真实边界

- 模型和索引都在本机：**机器关机，链接就死了**。这不是托管服务，只是把本机窗口临时开个门。
- 并发：检索 + 重排 + 生成串行，单问 2–3 秒；同时 1–3 人可接受，再多就排队。
- 带宽不是瓶颈（只传文本），但境外访问 trycloudflare 节点可能慢，面试时建议先用手机 4G 试一遍。
- 公网只读演示里没有登录体系：口令是唯一门槛，别用可猜的短口令。
- 对外就一句话：**本人机器上的可交互演示**，不说成 SaaS、不说成生产可用。

## 双层：企业知识库 + 员工个人知识库

侧栏是两个库，不是两个页签：

| 库 | workspace | 谁能读 | 谁能写 |
| --- | --- | --- | --- |
| 员工手册（企业库） | `handbook` | 四个身份按角色（reader 只读、staff 可看 internal） | 只有 editor |
| 王芳的资料 | `personal-wangfang` | 只有 reader 本人 | reader 本人（在自己的库里是编辑者） |
| 朱佐民的资料 | `personal-zhuzuomin` | 只有 editor 本人 | editor 本人 |
| 李强的资料 | `personal-liqiang` | 只有 staff 本人 | staff 本人 |

- **私有性来自产品的工作区级隔离**（`ekb/auth.py` 的 `require(workspace)` 对无权限工作区直接 403），不是演示层假装过滤：`GET /documents?workspace=personal-liqiang` 用 reader 令牌打过去就是 403，`POST /query` 同库同样 403（自检 `personal isolate` 一项就在验这个）。
- **权限按库判**：同一个 identity 在企业库可能只是 reader，在自己的个人库是 editor。所以「新建 / 编辑 / 下线 / 删除 / 导入 / 导出 / 采集 / 质量看板」全部按当前库判权，切库后按钮状态会跟着变。
- 资料台账（导入原件）、采集运行、质量问题统计都按库分开统计：个人库里导入的文件不会出现在企业库的资料库里，也不会参与企业库的质量看板。
- **明确没做**：跨库融合检索（产品的 `/query` 只接受一个 `workspace`）、个人库对话自动写回、给同事开单独可见的细粒度 ACL（产品只有 `all` / `internal` 两档，且 `internal` 是「租户内可读」，不是「指定人可见」）。想共享就把内容放进企业库，这是产品契约内的做法。

## 资料管理与权限（谁能建、谁能改、谁能删）

> 下表说的是**企业库**里的权限。个人库里，你自己的身份就是那个库的编辑者。

知识库产品真正的日常不是「问一句」，而是**谁可以改哪篇资料**。这一版把这条线补齐了，而且每一步都走真实 HTTP 状态码：

| 操作 | reader | staff（reader 角色） | editor | 语义 |
| --- | --- | --- | --- | --- |
| 查看文档 / 提问 | ✅ | ✅（多看到 `internal`） | ✅ | 工作区角色 + 文档可见范围双层过滤 |
| 新建文档 | ❌ 403 | ❌ 403 | ✅ 201 | 角色必须是 `editor`，且 `audience` 得在本人可见集合里 |
| 编辑正文 | ❌ 403 | ❌ 403 | ✅ 200 | **写新版本**再 `ingest`（带 `expected_version` 防并发覆盖）；旧版本留在版本表里可回溯 |
| 重新索引 | ❌ 403 | ❌ 403 | ✅ 200 | 同一版本重跑切分 + 向量化 |
| 下线（归档） | ❌ 403 | ❌ 403 | ✅ 200 | 状态 `ARCHIVED`：立刻从检索候选里消失，引用不再指向它 |
| 恢复 | ❌ 403 | ❌ 403 | ✅ 200 | 产品没有 `unarchive`：**写一版新正文即把状态带回草稿，再索引即恢复**（会生成 v(n+1)，这是契约行为） |
| 删除（物理） | ❌ 403 | ❌ 403 | ✅ 200 | 产品 API 没有 DELETE：控制台在代理层清 `chunks → versions → documents` 并写一条 `purged` 审计；要手打标题确认 |

界面上，只读身份看到的是**禁用的按钮 + 一句原因**（「当前身份 reader 是只读…切换右上角身份为 editor」），
而不是点了才报错；真绕过界面直接打接口，服务端给的是 `403 KB.FORBIDDEN`。

右栏第四个页签 **变更记录** 读的是产品自己的 `audit` 表（`created / version_created / published / archived / ingest_failed / purged`），
带身份与时间——这是「谁在什么时候改了哪篇」的唯一权威来源，不是前端日志。该页只有 editor 能看（记录里有 `internal` 文档标题）。

> 两个诚实的说明：
> ① 标题与可见范围**创建后不可改**（产品契约里 `documents` 没有 update 入口），所以编辑弹窗只改正文；要改这两项等于归档后按新标题重建。
> ② 物理删除走的是控制台代理层（`/api/__purge`），不是产品能力——产品把「下线」当默认删除语义，为的是引用可回溯。这一点在下面的「结构对照」里也算一条缺口。

## 结构对照：成熟企业知识库有什么，我们到哪一步

参照 Dify / RAGFlow / MaxKB / FastGPT / kotaemon / onyx 的公开形态逐项对照（不抄代码，只对齐能力面）：

| 能力 | 成熟产品 | 本项目 | 说明 |
| --- | --- | --- | --- |
| 知识库 / 文档 / 版本三级结构 | 有 | ✅ 有（`documents → versions → chunks`） | 每次改版生成新版本，旧版本保留 |
| 分块与向量 | 有 | ✅ 有（按段落切分 + bge-m3 1024 维，随 chunk 一起存 SQLite） | 单机 SQLite，不做分布式向量库 |
| 检索链路 | 混合 + 重排 | ✅ 有（BM25 ∪ 向量 RRF，bge-reranker-v2-m3 重排） | 有 trace 可核对，不是黑盒 |
| 引用回溯 | 有 | ✅ 有（文档 / 版本 / 分块 / 字符偏移 / 原文摘录） | 引用只保证出处，不保证推论正确 |
| 权限 | 工作区 + 文档可见范围 + 角色 | ✅ 有（`workspace` × `audience` × `role`，按库判权） | 过滤在检索层，不在提示词里 |
| 双层库（企业 + 个人） | 有（飞书知识库 / 语雀 / Notion 团队空间） | ✅ 有（企业库 `handbook` + 每人一个 `personal-<slug>`） | 私有性靠产品工作区隔离；不做跨库融合检索 |
| 资料管理 | 增 / 改 / 删 / 归档 / 重建索引 | ✅ 有（删除走控制台代理层，见上） | 标题与可见范围不可改是产品缺口 |
| 审计 | 操作日志 | ✅ 有（`audit` 表 + 变更记录页） | 产品原生写入，非前端伪造 |
| 评测 | 离线评测集 + 指标 | ✅ 有 28 题冻结评测（BM25）+ 23 条助手脚本评测 | 都写明是脚本化回归，不是盲测 |
| 运营闭环 | 未解决问题 → 补语料 | ✅ 有（feedback / unresolved 台账 + 运营面板） | 没有真实坐席，**「转人工」与工单号整条链路已删除**（PA-229） |
| 文档解析（PDF / Word / Excel / CSV） | 有 | ✅ 有（PDF 文本层 + 扫描件 RapidOCR、带线框表格还原、Word 保标题、Excel 逐 sheet 摊平） | 手写/歪斜页 OCR 会掉字；无线框表格按文本摊平 |
| 分类标签 | 有 | ✅ 有（14 组规则自动打标，可手工调，未分类 0） | 规则式，不做模型分类 |
| 版本对比 | 有 | ✅ 有（行级 LCS diff，v(n-1) vs vn） | 只看正文，不比元数据 |
| 批量操作 | 有 | ✅ 有（批量下线 / 批量导出 zip） | 没有批量改标签与批量重建索引 |
| 导出 | 有 | ✅ 有（单篇 md / docx，整库 zip + manifest，审计 CSV） | 导出按库、按权限 |
| 账号体系（SSO / OIDC） | 有 | ⚠️ 账号口令 + 服务端会话 + 失败限流 + 审计；**没有** SSO/OIDC、自助注册、按人审批 | 会话 Cookie 替代了旧的请求头自称身份 |
| 多租户管理后台 | 有 | ⚠️ 只有 tenant 维度隔离，没有管理界面 | 跨租户读取是 404，已验证 |
| 部署形态 | K8s / 分布式 | ⚠️ 单机单进程 SQLite + 本机模型 | 不声称可商用 / 高可用 |

**一句话**：结构、检索、权限、双层库、入库解析、审计、运营这几件事已经和成熟产品的骨架对齐；
账号体系（SSO / OIDC）与分布式部署这两件事没做，也没有假装做；扫描件 OCR 与表格还原是这一轮补上的（本地 RapidOCR + pdfplumber，有线框才行）。

## 检索模式消融（同一套 28 题，四档实跑）

`scripts/eval_rag.py` 用冻结的 `evaluation/corpus.json`（16 篇）与 `evaluation/cases.json`（20 可答 + 8 负例）逐档跑，口径与 `ekb/evaluate.py` 一致（k=4、candidate_k=12、坏例 = 相关文档没进前 4 或状态不是 answered），报告写 `evals-rag/<run>/`——**`reports/` 一个字节没动**（那是冻结证据）。

| 配置 | Recall@4 | MRR@4 | nDCG@4 | 负向拒答 | 答出 | 中位延迟 |
| --- | --- | --- | --- | --- | --- | --- |
| BM25（词法，无 rerank，抽取式） | 0.90 | 0.90 | 0.90 | **1.00**（8/8） | 18/28 | 2.6 ms |
| dense（bge-m3，无 rerank，抽取式） | **1.00** | 1.00 | 1.00 | 0.00（0/8） | 28/28 | 298 ms |
| hybrid（BM25 + bge-m3，RRF，无 rerank，抽取式） | **1.00** | 1.00 | 1.00 | 0.00（0/8） | 28/28 | 306 ms |
| hybrid + bge-reranker-v2-m3 + qwen2.5-14b 生成（历史基线，14B 已下线） | **1.00** | 1.00 | 1.00 | **1.00**（8/8） | 19/28 | 1,830 ms |

结论（也是这套系统真实的能力边界）：

1. **向量确实补召回**：BM25 的两条坏例（`multi` 多意图、`paraphrase-hard` 口语改写）在 dense/hybrid 下都进了前 4，召回从 0.90 到 1.00。
2. **但"召回什么都像答案"**：只加向量、不加门槛时，8 条负例全部被判成 answered（负向 0/8）。所以不能把 1.00 当成"更准"——召回率不是回答正确率。
3. **加 rerank + 生成侧 insufficient 判定后**，负向回到 8/8，代价是中位延迟 2.6 ms → 1,830 ms；`paraphrase-hard` 仍是已知坏例（语义边界，没有靠调参掩盖）。
4. 这四行数字合起来才解释得清简历里那句"dense/hybrid 召回 1.00 但负向全答"：它指的是第 2 行的现象，不是终点配置。

- 复跑：`./.venv/Scripts/python.exe scripts/eval_rag.py --rag http://127.0.0.1:<rag端口>/v1 --modes bm25,dense,hybrid`；加 `--rerank-model bge-reranker-v2-m3 --chat-model qwen3:8b-q4km` 就是第四行。
- rag 端口在控制台启动摘要的 `rag.port`，或 `GET /api/__meta` 里取。

## 上线前测试（并发 / 安全 / 容量 / 备份 / 故障 / 持久化）

这一节是「把它当真实项目上线前该做的检查」，不是演示脚本的产物；脚本都在 `.cache` 下，数字都是本机实测。

### 1. 并发（同一台机器，栈跑在 http://127.0.0.1:8810）

| 接口 | 并发 | 吞吐 | p50 | p95 | 错误 |
| --- | --- | --- | --- | --- | --- |
| GET /documents（元数据） | 1 | 34.0 rps | — | 32.2 ms | 0 |
| GET /documents | 8 | 240.7 rps | — | 50.4 ms | 0 |
| GET /documents | 32 | 189.6 rps | — | 580.7 ms | 0 |
| GET /__meta | 8 | 300.4 rps | — | 44.8 ms | 0 |
| POST /query（完整 RAG：BM25+向量+重排+生成） | 1 | 0.38 rps | 2,204.9 ms | — | 0 |
| POST /query | 2 | 0.73 rps | 2,521.8 ms | — | 0 |
| POST /query | 4 | 0.98 rps | 3,418.8 ms | 5,205.6 ms | 0 |
| POST /query | 8 | 0.98 rps | 5,889.5 ms | 8,147.8 ms | 0 |

结论：元数据接口随并发线性涨到 8 并发（约 240–300 rps）后回落；问答的**饱和点是 4 并发**（0.98 rps），再往上只涨延迟不涨吞吐 —— 瓶颈是单卡 16 GB 上的本地 8B 模型，不是 Web 层。

### 2. 安全

| 探测 | 结果 |
| --- | --- |
| workspace 做 SQL 注入 `handbook' OR '1'='1` | 403 KB.FORBIDDEN（工作区名走白名单，没进 SQL） |
| 路径穿越 `/documents/../../etc/passwd` | 404 |
| id 注入 `?document_id=1 OR 1=1` | 422 CONSOLE.BAD_DOCUMENT_ID |
| reader 打 /__quality、/__audit | 403 CONSOLE.ADMIN_FORBIDDEN |
| reader 建文档 / 归档 / 删除 | 403 |
| 版本冲突（expected_version 不匹配） | 409 KB.CONFLICT |
| 正文 250,000 字 | 413 KB.REQUEST_TOO_LARGE |
| 匿名查询 | 401 KB.UNAUTHENTICATED |
| 标题写 `<img src=x onerror=alert(1)>` | 产品照收（不校验标题），页面渲染时转义：DOM 里 `<img src=x` 计数 0、`&lt;img` 计数 1 |
| 耐久 | 3,000 次 GET /documents 零错误，89.4 s（约 33 rps 单线程），进程常驻 31 MB 无增长 |

### 3. 容量边界

- 单文档上限是**字节**：200,000 B（中文约 6.6–6.7 万字）。66,000 汉字 = 179,874 B → 入库成功、ingest 31.7 s、问答 3.8 s；70,800 汉字 = 212 KB → 422 KB.INVALID_TEXT。
- 入库耗时与长度近线性：3 万字 16.7 s、6 万字 32.8 s（约 0.5 ms/字，CPU 上算向量）。这就是「导入是同步的」的真实代价，也是已知缺口（没有队列化的异步进度）。

### 4. 备份

`VACUUM INTO .cache/backup-knowledge.db`：0.02 s、7.16 MB；源库与备份 `PRAGMA integrity_check` 都 ok；documents / versions / chunks / audit 四张表行数逐表一致（107 / 108 / 285 / 237）。

### 5. 故障注入（把本地模型网关杀掉）

- `POST /query` 返回 **200 + status=knowledge_unavailable + 空 answer + 空 citations**（fail-closed：宁可说不可用，也不编答案；这是 ekb 的契约，不是我们加的）。
- 其余端点（列表、文档详情、助手寒暄）照常 200。
- 本轮修的两个显示/语义问题：页面原先把 knowledge_unavailable 折算成「知识库中没有可支撑的内容」（把「服务挂了」说成「库里没有」），已在 `demo/console.html` 的 STATUS_TAG 与 aiMessage 里改成独立状态；助手侧新增 `UNAVAILABLE_REPLY`，网关不可用时不写未解决台账（那是「该补语料」的信号，不该被服务抖动污染）。

### 6. 重启持久化

原来每个进程起一份全新 `runtime/console-<hex>`，重启等于换库（演示干净，但没法证明「数据留得住」）。现在加了 `--data-dir`：

```bash
./.venv/Scripts/python.exe scripts/console.py --no-browser --port 8810 --data-dir runtime/console-live
```

实测：首启就绪 56.3 s、99 篇；写入探针文档、把「出差住宿政策」做到 v2；重启（同一 data-dir，就绪 14.2 s）后文档 100 篇、探针文档在、v2 的 current_version=2 / 版本表 [1,2]、按内容提问能召回 → 数据留住了。
顺带修掉一个真 bug：`initialize_with_models` 原来每次都重灌 16 篇冻结语料，复用同一份库时会产生 `duplicate_hash` 15。已改成「空库才灌」，重启后 quality issues 全 0。

### 7. 真实资料（不是合成的）

除合成制度语料外，另放了七篇**公开法规原文**（本地副本，未改写条文，带来源与抓取时间）：

| 文档 | 来源 |
| --- | --- |
| 官方原文-劳动合同法 | https://policy.mofcom.gov.cn/claw/clawContent.shtml?id=45137 |
| 官方原文-社会保险法 | https://www.gov.cn/guoqing/2021-10/29/content_5647616.htm |
| 官方原文-住房公积金管理条例 | https://www.gov.cn/gongbao/content/2019/content_5468861.htm |
| 官方原文-个人所得税专项附加扣除暂行办法 | https://www.gov.cn/zhengce/content/2018-12/22/content_5351181.htm |
| 官方原文-职工带薪年休假条例 | https://www.gov.cn/zhengce/content/2008-03/28/content_6636.htm |
| 官方原文-企业职工带薪年休假实施办法 | https://www.gov.cn/zhengce/2022-08/31/content_5711300.htm |
| 官方原文-工伤保险条例 | https://www.gov.cn/zhengce/content/2008-03/28/content_7416.htm |

实测问答（2026-11-17，都是原文里能逐字对上的答案）：
- 「劳动合同的试用期最长可以约定多久」→ 六个月（引用《官方原文-劳动合同法》）
- 「住房公积金的缴存比例怎么确定」→ 管理委员会拟订、报省级政府批准，单位和个人均不得低于上年度月平均工资的 5%（引用《住房公积金管理条例》）
- 「领取基本养老金需要累计缴费多少年」→ 满十五年（引用《社会保险法》）
- 「工伤认定申请由谁提出、多少天内提出」「停工留薪期一般多长」→ 引用《工伤保险条例》

文件在 `demo/zh-docs-real/`，导入走的就是窗口里的「导入文件」（7 篇 imported 4 / skipped 3，重复的按 sha256 跳过）。抓取脚本留在 `.cache/fetch_laws.py`：去网页标签、按「第 N 条」断行（只在条号后跟空格或【】时断，避免把「本法第十七条规定」这类引用切开），不做任何改写。

---

## 资料入库流水线（导入 / 清洗 / 存储 / 导出 / 采集 / 质量）

这一层在 **产品之外**（`pipeline/`，ekb 一行未改）：产品只接受纯文本（`ekb/text.py:8`），所以解析、清洗、台账、导出都放在控制台侧完成，再把纯文本喂给产品接口。

| 环节 | 做什么 | 边界 |
| --- | --- | --- |
| 解析 | PDF（pypdf 取文本层，文字层 < 50 字转 RapidOCR）、带线框表格（pdfplumber）、Word（python-docx，段落与表格保序）、Excel（openpyxl，每个 sheet 一段）、CSV/TSV（分号也能认）、TXT/Markdown | `.doc`/`.xls`/`.wps`/`.et` 提示另存为 docx/xlsx；手写体与歪斜扫描件 OCR 会掉字，不假装认得 |
| 清洗 | 页眉页脚按「≤40 字且出现在 ≥60% 页面」自动删；`recom-`+`mended` 断词复原；中文硬换行合并；NFKC 不启用（避免把全角符号偷偷改形）；表格摊平成「列名：值；列名：值」；压掉三连空行与重复段 | 字符编码依次试 utf-8-sig → utf-8 → gb18030 → utf-16 → latin-1，非 UTF-8 会在告警里写明 |
| 上限 | 单篇清洗后 ≤ **190,000 字** 且 ≤ **200,000 字节**（产品 `BodyLimit` 250 KB 留余量）；超了截断并写进告警 | 大文件不是靠调大产品限制，而是入库前收口 |
| 存储 | 原件按 sha256 落在 `runtime/console-<id>/uploads/`，台账 `files` 记 sha256/类型/字节/字数/页数/表格数/状态/告警/关联文档号；`sources` 记每个采集源最后一次的 sha 与文档号；`runs` 记每次采集的 fetched/created/updated/skipped/failed | 入库时文件名改写成 `.txt`（产品只认 `.txt`/`.md`），原件仍按 sha256 可追溯 |
| 导出 | 单篇 **Markdown** / **Word**（标题层级、列表还原）、整库 **zip**（`0001-标题.md` + `manifest.json`）、变更记录 **csv**（带 BOM，Excel 直接开） | 文件名走 RFC 5987（`filename*=UTF-8''`），中文名不乱码 |
| 采集 | `demo/sources.json` 配 `file` / `http` / `rss` 三类来源，抓取后用 sha256 指纹判断 `created` / `updated` / `skipped`；`dry_run` 只报不写 | 拉模式，按需点「立即采集」，没有内置定时调度器 |
| 质量 | 文档总数/ACTIVE/ARCHIVED/DRAFT、空正文、重复 `content_hash`、ACTIVE 却没有 chunks、卡住的版本、audit 事件数、原件台账、采集历史 | 看板只读，不自动修数据 |

- 入口：侧栏「⇪ 导入文件（PDF / Word / Excel / 图片）」支持拖拽多选（图片会先走多模态读图再入库）；右栏「资料库」页签是运维视图（KPI、质量、采集源、原件台账、导出三个按钮）；文档详情页可直接「导出 md / docx」。
### 批量导入（压缩包 / 整目录）
