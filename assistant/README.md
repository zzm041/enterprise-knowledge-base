# assistant · 知识库上的智能客服层

EKB 本体（`ekb/`，冻结）负责**文档、版本、权限、检索、引用**。这一层不改它一行代码，只通过它公开的 HTTP 接口读知识，负责**会话状态、意图路由、多轮改写、兜底与运营留痕**。

为什么要单独一层：把"寒暄也要检索一遍"和"查不到就沉默"这两种行为从检索服务里摘出来，检索服务才能保持"权限优先、有据才答"的窄契约；而企业里真正上线一个知识库助手，缺的往往不是检索，是**分流、追问、兜底、留痕、复盘**这几件事。

## 一次请求怎么走

```
用户输入
  → 规则快筛（寒暄 / 致谢 / 告别 / 能力询问）
  → 未命中：路由模型判意图 + 把多轮指代改写成独立问题（JSON：intent / standalone_question / reason）
  → intent = knowledge | followup ？ 走 EKB /query（该身份、该工作区、该可见范围）
        ├─ answered 且带引用 → 生成式回答（模型只许用选中的片段，越界引用直接判无效）
        └─ insufficient / 403 → 拒答兜底
  → 其余意图：闲聊由模型答（禁止编制度），越界回复范围说明（投诉、找真人这类没有坐席可转，明确落到能力边界外）
  → 全程落库：sessions / messages / feedback / unresolved
```

关键点：**先路由再检索**。寒暄不消耗检索与生成预算，也不产生"没有可支撑内容"这种把用户吓走的回答；反过来，凡是走检索的，答案必须带引用，没有引用宁可拒答。

## 能力清单

| 能力 | 实现 | 证据 |
| --- | --- | --- |
| 意图路由 | 规则快筛 + 模型 JSON 路由，9 类意图，模型不可用时降级为直接检索 | `assistant/intent.py`；`tests/test_intent.py` 12 例 |
| 多轮改写 | 路由同时输出 `standalone_question`（补全指代，禁止添加历史里没有的实体），用它去检索 | `followup-device`：问"入职当天在哪里领取办公电脑"→"要带什么" 改写成"入职当天要带什么" |
| 引用式回答 | 只允许引用传进去的片段，越界引用判 `KB.INVALID_CITATION`，前端每条结论可点开原文 | 回答下方 citation chips + 右侧原文片段（文档/版本/字符偏移/可见范围复核） |
| 拒答兜底 | 检索无据 → 明确说"查不到"，同时写入未解决问题台账（含改写后的问题与原因） | `unresolved` 表；截图"员工购房无息贷款怎么申请" |
| 会话与身份隔离 | 会话按 `租户/主体` 绑定，切换身份不会串上下文 | `tests/test_service.py` 会话隔离用例 |
| 反馈闭环 | 每条回答可点"有用 / 没帮到" | `feedback` 表；`GET /stats` |
| 运营台账 | 会话数、消息数、正负反馈、未解决问题清单 | `GET /stats`、`GET /unresolved`；演示窗口调试面板"助手运营面板" |

## 没有「转人工」，也没有工单号

这一层不接真实坐席，就不在界面上承诺人工。2026-11-17 起整条链路删干净：`POST /handoff`、`GET /handoffs`、`handoffs` 表、工单号 `HD-XXXXXXXX`、前端「转人工」按钮与建议词全部移除（PA-229）。用户说「我要投诉 / 转人工」时，路由把它判成 `out_of_scope`，回答只说明能力边界，不再生成一个没人接的工单号——那等于把「看起来能用」当成「能用」。

现在真正闭合的闭环只有一条：**有用 / 没答到**（写 `feedback`）→ 没答到的问题进未解决问题台账（`unresolved`）→ 由制度负责人补文档。真实企业要接工单平台，得在外部系统那侧自己写 dispatch，这一层不再假装自己有坐席。

### 兜底话术可配置（企业换成自己的口吻）

四句兜底（拒答 / 服务不可用 / 无权限 / 超出范围）都留了配置口，写在 assistant 配置文件的 `assistant.replies` 里即可覆盖，没配的键走内置默认：

```json
{"assistant": {"replies": {
  "refusal": "这条制度我们还没收录，已登记给制度负责人补文档。",
  "out_of_scope": "投诉请走公司工单系统，这个助手只回答制度与流程。"
}}}
```

为什么做成可配置而不是写死：真实产品都是这个形态——FastGPT 的故障排查文档专门讲「配置知识库检索无匹配数据时拒绝回答」，Onyx 在 PR #14626 里给「检索成功但合成没有答案」补了 fallback response。兜底话术是**部署单位的口吻**，不是框架的判断。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/chat` | `{text, session_id?, workspace?}` + 头 `X-Identity`；返回 reply / answer_status / intent / standalone_question / citations / suggestions / trace / latency_ms |
| POST | `/feedback` | `{message_id, value: "up"|"down", comment?}` |
| GET | `/sessions/{id}` | 会话转录（主体不符 404） |
| GET | `/stats` `/unresolved` `/health` | 运营与探活 |

身份来自 `X-Identity`，不在名单里的身份直接 401；页面本身拿不到任何 KB 令牌（由 console 的同源代理注入）。

## 怎么跑

演示窗口（含助手）：

```powershell
cd E:\enterprise-knowledge-base
.\.venv\Scripts\python.exe scripts\console.py
```

只跑助手（已有 EKB 与模型网关时）：

```powershell
.\.venv\Scripts\python.exe -m assistant.api --config runtime\console-<id>\assistant-config.json --port 8804
```

评测：

```powershell
.\.venv\Scripts\python.exe scripts\eval_assistant.py --config runtime\console-<id>\assistant-config.json
# 报告写到 assistant/reports/eval-<UTCstamp>.json，退出码 0 表示全过
```

单测：

```powershell
.\.venv\Scripts\python.exe -m pytest assistant/tests -q
```

## 评测结果（2026-11-15，本机 CPU，真模型，非桩）

- 语料：`assistant/evalset.json` 23 条脚本化对话，覆盖寒暄/致谢/告别/能力/闲聊/知识/多轮追问/拒答/越界/投诉/权限对照/内部文档。拒答样本只用库里确实没有、但员工会问的真实问题（"员工购房无息贷款怎么申请""公司是否提供员工宿舍"）；知识库语料扩到 84 篇后，"年假没休完可以折现吗"从拒答变成有依据回答，因此它被改成正面用例（`kb-leave-cashout`）——拒答用例的前提必须复核，否则测的是过期假设。
- 结果：**23/23 通过**，其中 status 23/23、intent 23/23、citation 23/23、rewrite 23/23（rewrite 断言只对带指代的追问生效，其余按契约跳过）。
- 延迟：中位 3.37 s，p95 7.12 s，最大 8.97 s（qwen3:8b-q4km 生成占大头，向量与重排在本地 CPU；p95 从 4.03 s 涨到 7.12 s 的原因是语料扩到 91 篇、其中 7 篇是上万字的法规全文，重排候选变长）。
- 报告：`.cache/eval-23b.json`（含每条对话的逐轮 intent / status / 引用 / 改写后问题），历史报告在 `assistant/reports/`。
- 2026-11-17 补跑记录：语料扩充后先掉到 21/23，两个失败都不是检索坏了 —— ① 追问「那报销标准呢」被改写成「报销标准是什么」，丢了主体词导致检索不到，在 router prompt 里补三条 few-shot（追问必须带上一轮主体词）后通过；② 拒答样本「异地社保怎么转移」在补进《社会保险法》后**已经可以答**，换成库里确实没有的「员工购房无息贷款怎么申请」。

口径声明（别把这段读大）：**这是一套按契约写的脚本化回归集，不是盲测集**。题目与语料同一批人整理，覆盖率靠"意图 × 状态 × 引用"三个布尔量，不衡量回答措辞好坏；通过率高只说明契约没破，不等于泛化。

## 边界（当前真实能力范围）

- 检索链路是混合（BM25 + bge-m3 向量，RRF 融合）+ bge-reranker-v2-m3 重排 + 本地生成模型；**28 题的 0.90 那份冻结评测是纯 BM25 跑的**，没有用这条链路重跑过，不得拿来当向量成绩。
- 单进程、单机、SQLite；没有并发压测、没有 SLA、没有多副本。会话表没有做过量级设计（索引有，容量没验过）。
- 「转人工 / 工单」整条链路已删除（2026-11-17，PA-229）：没有坐席就不在界面上承诺人工；要接真实工单系统，得在外部系统侧自己做 dispatch。
- 文档只支持纯文本与 Markdown；PDF/扫描件/OCR 属于未实现。
- 意图路由依赖同一个本地生成模型（qwen3:8b-q4km），模型不可用时整层降级为"直接检索 + 拒答"，此时寒暄会退化成检索（有降级记录，但体验下降）。
- 没有做鉴权体系对接（现在靠 `X-Identity` 头 + 服务端令牌表），接企业 SSO 需要替换 `api.py` 的身份依赖。

## 目录

```
assistant/
  api.py         FastAPI 应用（/chat /feedback /handoff /sessions /stats /unresolved /health）
  service.py     Assistant：路由 → 检索 → 生成/拒答 → 落库
  intent.py      规则 + 模型路由，多轮改写
  llm.py         模型网关客户端（JSON 模式、失败码）
  kb.py          EKB HTTP 客户端（按身份注入 Bearer）
  store.py       SQLite 五表：sessions / messages / feedback / handoffs / unresolved
  settings.py    配置数据类（由 console 写 JSON）
  evalset.json   23 条脚本化对话
  reports/       评测报告（JSON，可复算）
  tests/         26 个单测
```
