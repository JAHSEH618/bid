# 投标技术方案生成器 · Web App 需求文档

> **版本**:v0.8(待评审) **日期**:2026-05-02 **形态**:内网服务器 docker compose、多用户(团队共享池)、Python 后端 + Web 前端、HTTP-only
> **工作流内核**:见同目录上级的《技术方案自动生成工作流 — Dify 搭建指南(含人工审核).md》(以下简称 **v10 设计文档**),本需求文档**不重复**其中工作流逻辑细节。

**v0.8 变更**(相对 v0.7,2 处口径澄清):
- **FR-1.3 排队语义按动作类型区分**:新项目 `/start` → queued(异步排队);审核/重试动作 → 503 + Retry-After(同步告知)。
- **§8 Document 表加 `file_size` 字段**:与 spec / 上传配额(NFR-4)对齐。

**v0.7 变更**(相对 v0.6,5 处与 Spec v3.2 同步):
- **API Key 快照模型**:FR-7.5/FR-1.5/§8 数据模型从"引用 user_id"升级为"双重快照":`api_key_owner`(审计用)+ `encrypted_api_key_snapshot`(运行时密文,与 ApiKey 行解耦)。
- **FR-3.5 LLM-3 不流式**:仅 LLM-2 流式,LLM-1/3 是 JSON 模式。
- **API 表加 `/api/me/api-key/test`**:替代 `/health` 查 LLM 的角色。
- **M4 表述纠正**:react-markdown(不是 Tiptap)。
- **NFR-3.1 redis 内存预算**:50M-150M / `noeviction` 策略(原 30M-80M / LRU,LRU 会驱逐 arq 队列与并发锁)。

**v0.6 变更**(相对 v0.5,2 处口径澄清):
- **进程模型澄清**:`app` 容器内 uvicorn 与 arq worker 是**两个独立 Python 进程**(由 supervisord 编排),共享同一容器、同一 `.env`、同一 PostgreSQL/Redis 连接。不是"同进程"。这样进程崩溃可独立重启,任务/HTTP 不互拖。架构图、FR-3.7、NFR-3 同步统一。
- **`/health` 端点口径收窄**:只查内部依赖(db / redis),LLM 连通改由 `/api/me/api-key/test` 单独检查。理由:健康检查应快、不被外网拖慢;LLM 连通是"用户配置正确性"问题,不是"应用是否健康"问题(NFR-5 / 第 9 章 API 表)。

**v0.5 变更**(相对 v0.4):
- **单章 LLM 超时表述澄清**:10 分钟仅计 LLM 调用时长,人工审核 `awaiting_review` 期间不计时;每次 `revise` 重写重新开始计时(FR-3.10 / Q21)
- **服务端口改为 12123**(原 8080),NFR-3 与第 7 章架构图同步

**v0.4 变更**(相对 v0.3,8 条用户决策落地):
- **D1** LLM 模型组合定为:LLM-1=`deepseek-v4-flash`,LLM-2=`qwen3.6-max-preview`,LLM-3=`qwen3.6-flash`
- **D2** 文档格式**收窄为 DOCX / DOC / MD / TXT**,**不再支持 PDF**(含扫描件和文字版)
- **D3** 明确接受:LLM 调用上下文经 DashScope 出公司网络;数据机密性约束仅适用于"上传文件本体的存储位置"
- **D4** LLM 失败重试:**2 次重试,2s/5s 退避,3 次失败 → 章节标记 `failed`,用户手动重试**
- **D5** **DOCX 导出方案大改**:从 Plan C(模板合并)简化为 **Plan A+(mermaid 预渲染 + pandoc 直转)**,不再使用公司 Word 模板,直接按生成 markdown 内容组合 docx
- **D6** 数据保留:**永不自动清理**,用户手动删
- **D7** 接受 HTTP-only 内网部署的密码明文传输风险
- **D8** 时区统一 **Asia/Shanghai**(日志、cron、DOCX 文件名)

---

## 1. 项目概述

把已经设计好的 Dify v10 投标技术方案生成工作流,落地为一个独立的 **Python web app**,部署在公司内网服务器上,投标团队员工通过 IP + 端口访问使用。

**一句话目标**:用户登录后上传 3 份文档(技术需求 / 打分规则 / 方案模板),系统按章节循环生成方案正文,每章生成后人工审核(通过 / 不通过 / 跳过),最终输出完整 markdown 与 docx 方案。

**为什么自建而不用 Dify**:Dify 1.13 的 Human Input resume 接口目前只暴露 Console API,集成成本不亚于自建,且自建后整套流程、prompt、状态机、UI、权限模型都可定制。

---

## 2. 范围声明

| 范围 | 项目 |
|---|---|
| ✅ In scope (本期) | 自建账号认证 / 团队共享项目池 / 用户级 DashScope API Key 配置(加密落库) / 项目管理(增删查) / 3 文档上传(DOCX/DOC/MD/TXT) / 提纲生成与编辑 / 章节循环生成 / 章节流式展示 / 三按钮人工审核 / 反馈重写 / 章节失败手动重试 / 全文整合 / Markdown 导出 / **DOCX 导出(Pandoc 直转 + Mermaid 预渲染)** / LangGraph checkpoint 持久化 / 内网服务器 docker compose 部署 / DashScope 模型调用 |
| 🟡 后续 | PDF 支持(含 OCR) / 公司 Word 模板套用 / 项目级 API Key 覆盖 / prompt 在前端可改 / 多模型 A/B / 章节并行生成 / 邮件通知 / SSO 接入 |
| ❌ Out of scope (永远不做) | 真正的多租户隔离 / 标书检索 / 商务部分(投标报价、资质材料拼装) / 在线协作编辑 / 公网部署 |

---

## 3. 角色与场景

### 3.1 角色

| 角色 | 数量 | 权限 |
|---|---|---|
| **撰写人 / 审核人**(普通用户) | ~10(可扩展) | 登录后看到团队所有项目,可创建/编辑/删除自己创建的项目,可审核任何项目的章节 |
| **管理员**(admin) | 1-2 | 在普通用户权限基础上,可创建/禁用账号、重置密码、查看全员 token 消费 |

> **本期不做角色精细化**:不区分"撰写"和"审核",任何登录用户都能审核任何章节(团队共享池模式)。

### 3.2 典型场景

> 撰写人小张登录系统,新建一个投标项目,上传 3 份文件(必须是 DOCX/DOC/MD/TXT;PDF 一律拒绝),启动工作流。系统按提纲一章一章生成正文,小张每章看一眼:有问题就写两句修改建议点"不通过"让系统重写;质量过得去就点"通过"。中午吃饭离开了,下午同事小李帮忙审了 2 章,小张回来继续审完剩下的。最后下载 .docx 给排版同事调版面 + 加公章页。

预期一份方案 8-15 章,平均每章 LLM 生成 1-3 分钟 + 人工审核 1-5 分钟。允许中途关闭浏览器、跨用户接力审核。

---

## 4. 用户旅程

```
┌──────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐
│ 登录 │──>│ 项目列表  │──>│ 新建项目  │──>│ 文档上传  │──>│ 提纲确认  │──>│ 章节循环 │──>...
└──────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘   └────────┘
   │
   └──> 设置(我的 API Key) / 管理员页(用户管理)
```

### 4.1 关键页面 / 视图

| 视图 | 内容 | 谁能看 |
|---|---|---|
| **P0 登录** | 用户名 + 密码 | 未登录 |
| **P0.5 强制改密**(首次登录) | 旧密码 + 新密码 + 确认;改完跳转 P1 | `must_change_password=true` 用户 |
| **P1 项目列表** | 团队所有项目卡片(名称、创建人、创建时间、当前章节进度、状态);右上角:个人设置入口、登出 | 已登录 + 已改密 |
| **P2 新建项目** | 项目名 + 描述;启动前必须已配 API Key,否则跳转设置页 | 已登录 |
| **P3 文档上传与配置** | 3 个文件上传槽(仅接受 .docx/.doc/.md/.txt;前端 + 后端双重校验)+ `pages_per_chapter` + `max_retry_per_chapter` + "开始生成"按钮 | 已登录 |
| **P4 提纲确认**(可选) | 显示 LLM-1 输出的章节提纲表格,允许编辑章节标题/要点/页数,点"确认"启动循环 | 已登录 |
| **P5 章节生成与审核** | 主面板:当前章节 markdown 流式渲染;侧栏:章节列表 + 状态徽章(含 `failed` 红标)+ 该章上次审核人;底部:三按钮 + 反馈输入框;`failed` 章节显示"重试"按钮 | 已登录 |
| **P6 全文预览与导出** | 完整 markdown 渲染 + "复制全文 / 下载 .md / 下载 .docx"(.docx 按需生成,首次约 5-15s,带进度条) | 已登录 |
| **P7 个人设置** | DashScope API Key 配置(加密保存,write-only) + 测试连通按钮;本月 token 消费统计 | 已登录 |
| **P8 用户管理** | 用户列表 + 新建账号 + 重置密码 + 禁用账号;团队总 token 消费 | 仅 admin |

> P4 提纲确认是**可选环节**:默认走"自动确认",有需要才进入手工编辑。

---

## 5. 功能需求 (FR)

### FR-1 项目管理

- FR-1.1 创建/列出/删除项目;每个项目独立目录存上传文件、生成内容。
- FR-1.2 项目状态机:`init` → `extracting` → `outlining` → `outline_ready` → `running` → `awaiting_review` → `running` → ... → `done` / `failed` / `aborted`。
- FR-1.3 同时只允许 **10 个项目并发**(默认值,可配置)。语义按动作类型区分:
  - **新项目 `/start`** 超过上限 → Project.status='queued',前端显示"排队中(前面 N 个)";有项目结束时自动唤醒 FIFO
  - **审核 / 提纲确认 / 重试**(`/review` / `/confirm-outline` / `/chapters/{idx}/retry`)超过上限 → 返回 **503 Service Unavailable + Retry-After: 60**,前端 toast"系统繁忙,1 分钟后重试";不静默排队,因为这些是**用户当前正在等响应**的同步动作,排队比立即告知更糟糕
- FR-1.4 团队共享池:任何登录用户可看到所有项目;**只有创建者和 admin** 可删除项目。
- FR-1.5 项目实体记录 `created_by`(创建者 user_id)、`api_key_owner`(启动者 user_id,审计用)、`encrypted_api_key_snapshot`(启动瞬间从 ApiKey.encrypted_key 拷贝的密文,运行时反加密用,见 FR-7.5)。
- FR-1.6 项目数据**永不自动清理**(D6),用户在项目列表手动删除,删除时连带磁盘文件、章节、TokenUsage 记录一起清掉。

### FR-2 文档处理

- FR-2.1 接受 **DOCX / DOC / MD / TXT** 四种格式上传,单文件 ≤ 50MB。
- FR-2.2 用 `markitdown` 库统一抽取为 markdown 文本,落盘存项目目录。
- FR-2.3 抽取失败的文档允许用户重传。
- FR-2.4 **PDF 不支持**(D2):
  - 前端 `<input accept="">` 限制 + 后端 MIME 校验双重拒绝。
  - 后端拒绝时返回 415 + 提示 `"PDF 暂不支持,请上传 DOCX/DOC/MD/TXT 格式"`。
  - 后续如需支持需引入 OCR(PaddleOCR),不在本期范围。

### FR-3 工作流执行(内核见 v10 文档)

- FR-3.1 使用 LangGraph 实现 v10 设计文档 §4.5 的状态机循环。State schema 与 v10 §3.3 的 5 个 Loop 变量一一对应。
- FR-3.2 LLM 调用全部走 LiteLLM,**模型组合硬编码**(D1):
  - **LLM-1 提纲生成**:`deepseek-v4-flash`(温度 0.3,JSON 模式)
  - **LLM-2 章节正文生成**:`qwen3.6-max-preview`(温度 0.6,流式)
  - **LLM-3 可视化建议**:`qwen3.6-flash`(温度 0.4,JSON 模式)
  - 模型名通过环境变量可覆盖,但本期默认就是这套。
- FR-3.3 LLM 调用使用**项目启动时快照的 API Key**(见 FR-7),不使用全局 Key。
- FR-3.4 提示词模板**硬编码**在后端代码,不在前端暴露。
- FR-3.5 **仅 LLM-2 启用流式输出**(章节正文直接给用户看,流式让首字节延迟低 + 可视化"正在写")。LLM-1(提纲生成)与 LLM-3(可视化建议)是 JSON 模式,响应不大且需要解析后再用,不流式。SSE 推送只发 LLM-2 的 token,与 chapter_started / chapter_ready / awaiting_review / outline_ready / proposal_ready 等控制事件混合。
- FR-3.6 LangGraph 使用 PostgreSQL checkpoint backend(`langgraph-checkpoint-postgres`),每个节点完成后自动持久化,容器重启后能恢复 in-flight 工作流。
- FR-3.7 工作流任务跑在 **arq worker** 进程,与 FastAPI HTTP 进程**独立**(共享同一容器、`.env`、DB/Redis,但是两个 OS 进程,由 supervisord 编排),避免长任务阻塞 HTTP。
- FR-3.8 每次 LLM 调用记录 token 消费到 `TokenUsage` 表(user_id / project_id / model / prompt_tokens / completion_tokens / ts)。
- FR-3.9 **LLM 失败重试**(D4):
  - 单次 LLM 调用失败(网络错误 / 5xx / 限流)→ **重试 2 次**,退避 **2s / 5s**(总 3 次尝试)。
  - 4xx 客户端错误(API Key 无效 / 模型不存在)**不重试**,直接报错。
  - 3 次都失败 → 该章节状态置 **`failed`**(见 FR-4.7),工作流暂停在该章节,等用户手动点"重试"。
  - 重试日志记录到 `errors.log` 与结构化日志(model / 错误类型 / 重试次数)。
- FR-3.10 **单次 LLM-2 节点执行**(从发起调用到流式输出完成)**总时长上限 10 分钟**,含 FR-3.9 的 2 次重试与退避(2s+5s)。**人工审核 `awaiting_review` 期间不计时**;每次 `revise` 重写都重新开始 10 分钟计时。超时 → 章节状态置 `failed`,用户手动重试。

### FR-4 章节审核

- FR-4.1 章节生成完后工作流通过 `interrupt()` 暂停,前端进入"待审核"态。
- FR-4.2 审核界面提供三按钮:
  - **通过**(`approve`)→ 章节累积,进入下一章
  - **不通过**(`revise`)→ 必须填反馈文本,本章 `retry_count + 1`,重写;超过 `max_retry_per_chapter` 后自动转 skip
  - **跳过**(`skip`)→ 累积一个占位章节(含 `<!-- ⚠️ 章节《xxx》被人工跳过 -->`),进入下一章
- FR-4.3 审核期间允许撰写人**离开页面 / 关闭浏览器**;别人或自己回来后从 P1 项目列表点回来继续审。
- FR-4.4 同一章节的历史版本(每次重写)可查看 diff(本期最简实现:tab 切换历次版本)。
- FR-4.5 每次审核动作记录 `ReviewEvent`(reviewer_id / decision / feedback / ts),便于审计"谁审了哪章"。
- FR-4.6 任何登录用户均可审核任何章节(团队共享池);章节侧栏显示"上次审核人"。
- FR-4.7 **章节状态扩展为**:`pending` / `generating` / `awaiting_review` / `approved` / `skipped` / **`failed`**(D4)。
  - `failed` 章节在 P5 红标显示并暴露"重试"按钮。
  - 重试动作:重置 `retry_count=0` + 清空当前章节的本轮 ChapterVersion → 重新触发 LangGraph 该章节生成。
  - 任何登录用户都可触发重试。

### FR-5 全文整合与导出

- FR-5.1 所有章节累积完成后,工作流自动跑 v10 §4.6 全文整合,产出 `final_proposal` 字段。
- FR-5.2 P6 视图渲染完整 markdown,支持代码块、mermaid、表格。
- FR-5.3 提供"复制全文"、"下载 .md"、"下载 .docx" 三个动作。
- FR-5.4 **DOCX 导出实现**(D5,**简化方案**):
  1. **Mermaid 预渲染**:扫描 markdown 中所有 ` ```mermaid ` 代码块 → 调 mermaid-cli(`mmdc`)本地渲染为 PNG → 替换为 `![](path/to/img.png)` 图片引用,生成 `proposal_inlined.md`。如某图渲染失败 → 退回保留代码块原文(降级容错),docx 继续生成。
  2. **Pandoc 直转**:`pandoc proposal_inlined.md -o proposal.docx`,使用 Pandoc 默认样式生成 docx。可选 `--reference-doc` 参数指向一个**最小样式参考文件**(`reference.docx`,只定义 Heading 1-4 / 正文中文字体、行距,见 FR-5.5)。
  3. 完成。**没有模板合并、没有封面、没有页眉页脚、没有公章占位、没有 docxtpl 元数据注入**——这些后续若公司投标排版需要,由排版同事在 Word 里手动加。
- FR-5.5 内置**最小样式参考文件** `app/backend/templates/reference.docx`:
  - 仅定义中文友好字体(标题黑体 / 正文宋体 / 行距 1.5)+ Heading 1-4 大小 + 表格基础边框
  - **不含**封面、页眉、页脚、目录、公章占位
  - 用户如需替换,直接覆盖此文件后重启容器
- FR-5.6 DOCX 文件名格式:`{project_name}_技术方案_{YYYYMMDD}.docx`(YYYYMMDD 用 Asia/Shanghai 时区,D8)。
- FR-5.7 DOCX 生成是**按需触发**(用户点"下载 .docx"才跑),不在工作流末端自动生成;首次生成后缓存到 `{project_dir}/proposal.docx`,直到 markdown 重新生成才失效。
- FR-5.8 **DOCX 导出在 arq worker 中串行执行**(同时只允许 1 个 docx 导出任务),避免 mermaid-cli 的 chromium 进程同时启动多份打爆 2c4g 内存。其他 docx 请求排队。
- FR-5.9 生成失败(Pandoc 报错 / mermaid 全部失败)需返回明确的错误码和阶段信息,前端给出可重试按钮。
- FR-5.10 **DOCX 生成耗时 SLA**:10 章方案 < 15 秒(简化后比 Plan C 快约一半)。

### FR-6 认证与用户管理

- FR-6.1 自建账号认证,密码用 **bcrypt** 哈希存储,**不**支持注册开放(账号由 admin 创建)。
- FR-6.2 登录返回 **JWT access token**(短期,2h)+ **refresh token**(长期,7d)双 token,**HttpOnly Cookie** 落地。
- FR-6.3 后端所有 `/api/*` 端点(除 `/api/auth/login` `/api/auth/refresh`)需要有效 access token,否则返回 401。
- FR-6.4 admin 端点(`/api/admin/*`)额外校验 admin 角色,否则返回 403。
- FR-6.5 admin 可创建账号(用户名 + 初始密码 + 角色)、重置密码、禁用账号;不删账号(保留历史归属)。
- FR-6.6 **首次登录强制改密**(必做):
  - User 表加字段 `must_change_password: bool`,新建账号时(包括默认 admin)默认为 `true`,改密后置为 `false`。
  - 当 `must_change_password=true` 时,除 `/api/auth/me` `/api/auth/logout` `/api/me/change-password` 三个端点外,所有 `/api/*` 端点返回 **`428 Precondition Required`** + JSON `{error: "must_change_password"}`。
  - 前端拿到 428 后强制路由到改密页面(P0.5),改完才能进入正常视图。
  - admin 重置普通用户密码时,把对方的 `must_change_password` 重新置为 `true`。
- FR-6.7 失败登录限流:**单 IP 每分钟 5 次**;超过后该 IP 锁 5 分钟。
- FR-6.8 默认 admin 账号:用户名 `admin`,密码 `admin123`,**硬编码在数据库初始化迁移脚本里**,`must_change_password=true`。文档与启动横幅强提醒首次登录后立即改密。

### FR-7 API Key 管理

- FR-7.1 用户在 P7 个人设置页可配置自己的 DashScope API Key。
- FR-7.2 API Key 用 **AES-GCM** 加密落库(master key 来自环境变量 `BID_APP_MASTER_KEY`,启动时校验存在);**前端永远拿不到明文**,只能看"已配置 ✓ / 未配置 ✗"+ 重设按钮。
- FR-7.3 配置时后端做一次**测试调用**(向 DashScope 发一条最小请求,验证 Key 有效),失败则不保存并返回错误信息。
- FR-7.4 用户启动项目时,如果未配 API Key → 跳转设置页 + Toast 提示。
- FR-7.5 项目启动瞬间**双重快照**该用户的 API Key:
  - `Project.api_key_owner` = user_id(审计/UI 展示用,知道"这个项目用谁的额度")
  - `Project.encrypted_api_key_snapshot` = 当时 ApiKey.encrypted_key 的字节拷贝(运行时解密 + 调用 LLM 用)
  整个项目生命周期(包括其他人审核触发的重写、章节失败重试)都用快照的密文,不再反查 ApiKey 表。
- FR-7.6 用户重置 / 删除 API Key 后,**已启动项目继续跑**(因为运行时读 Project 上的密文快照,与 ApiKey 行解耦);**新项目**会快照新 Key。
- FR-7.7 P7 设置页显示当前用户本月 token 消费(从 `TokenUsage` 聚合);admin 在 P8 可查全员消费。

---

## 6. 非功能需求 (NFR)

### NFR-1 性能

- 单章 LLM 生成首字节延迟 < 5s(DashScope 国内节点,LLM-2 用 Max-Preview 时可放宽到 8s)。
- 流式输出每秒至少 20 token 推送到前端。
- 提纲生成 < 60s。
- 同时跑 10 个工作流项目(其中大多在等人审,实际 LLM 流式生成峰值 2-3 章并行)。
- 登录请求 P95 < 200ms。
- 项目列表查询 P95 < 300ms(10 用户 × 50 项目,500 行数据无压力)。
- DOCX 生成 SLA 见 FR-5.10。

### NFR-2 持久化与可恢复

- 任意时刻关闭后端进程或 docker 容器,**已完成章节不丢**,in-flight 章节会回到上一个 checkpoint 重跑。
- LangGraph checkpoint 与业务数据全在 PostgreSQL,数据卷挂载到宿主机 `/var/lib/bid-app/postgres-data`。
- 上传的原始文档、抽取后 markdown、章节产出、生成的 docx 均落盘到挂载卷 `/var/lib/bid-app/projects/`。
- **每日凌晨 3 点(Asia/Shanghai,D8)cron 触发** `pg_dump` 导出到挂载卷 `/var/lib/bid-app/backups/`,保留最近 7 天。
- **数据保留策略**(D6):**永不自动清理**。项目跑完后所有数据一直保留,直到用户在 P1 列表手动删除。删除是不可逆操作,前端二次确认。

### NFR-3 部署与运行环境

- **目标服务器**:Linux x86_64(Ubuntu 22.04 / CentOS 7+),内网 IP 直接访问,HTTP-only,**2c4g 起步**。
- **时区**:容器和宿主机统一 **Asia/Shanghai**(D8),通过 `TZ=Asia/Shanghai` 环境变量 + `/etc/localtime` 挂载。
- **部署方式**:`docker compose up -d` 一条命令,**3 个容器**:
  - `app`(同容器内 supervisord 编排两个独立进程:uvicorn 单 worker FastAPI + arq worker;前端静态资源由 FastAPI 直接 serve)
  - `postgres`(PostgreSQL 16 官方镜像)
  - `redis`(Redis 7 官方镜像,arq 队列)
  - **不引入 nginx**:HTTP-only 内网 + 单 app 进程,uvicorn 直接服务足够;省一个容器和 200MB 内存。
- 端口仅暴露 `app` 容器的 **12123** → 宿主机 12123(可改 `.env` 里的 `APP_PORT`);用户通过 `http://内网IP:12123` 访问。
- `postgres` 与 `redis` 不暴露端口,仅 docker 网络内可达。
- **镜像内含 pandoc + mermaid-cli + chromium-headless**,镜像约 1.1GB(简化方案后比 Plan C 小约 100MB,因为不再装 docxtpl 体系)。

### NFR-3.1 ⚠️ 内存预算(2c4g 关键风险)

| 组件 | 常驻 | 峰值 | 备注 |
|---|---|---|---|
| postgres | 200M | 400M | shared_buffers 调小 |
| redis | 50M | 150M | maxmemory 200M / **noeviction** |
| app(uvicorn 单 worker) | 300M | 500M | LangGraph + LiteLLM |
| arq worker(独立进程,同容器) | 200M | 400M | LLM 流式 + DOCX 流水线 |
| chromium(mermaid 渲染时) | 0 | 600M | 仅 docx 生成时拉起,完成即退 |
| OS / 容器开销 | 200M | 300M | docker daemon + 缓存 |
| **合计常驻** | **~1.0G** | — | 富余 3G |
| **合计峰值**(LLM 流式 + docx 同时) | — | **~2.3G** | 富余 1.7G |

**风险**:如果同时多个 docx 导出 + 多个 LLM 流式,可能瞬时 OOM。**应对**(已落入 FR-5.8):
- DOCX 导出在 arq 里**串行化**(全局只允许 1 个)
- 单 uvicorn worker(不开多 worker)
- 监控发现 OOM 频繁则升级 4c8g(改 docker host,不改代码)

### NFR-4 安全

- **数据机密性边界声明**(D3):
  - **本机存储侧**:所有上传文件、抽取的 markdown、生成的 docx 均**只在本机磁盘**,不上传任何第三方对象存储。
  - **LLM 调用侧**:运行时,LLM-1/2/3 的 prompt 上下文(含投标文档抽取后的 markdown)会经 HTTPS 发到**阿里云 DashScope**。这是设计前提,用户已确认接受。需要避免数据出公司网络的项目应**改用本地大模型**,不在本期范围。
  - 相关用户告知:登录后首页 banner 一次性提示"本系统会将文档内容发送至阿里云大模型生成方案,机密项目请评估后使用"。
- API Key **AES-GCM** 加密落库(`BID_APP_MASTER_KEY` 环境变量,32 字节随机)。
- 密码 **bcrypt** 哈希(work factor 12)。
- JWT secret 通过环境变量注入。
- HttpOnly + SameSite=Strict cookie,防 XSS 窃取 token。
- 登录端点限流(FR-6.7);全局每 IP 100 req/min。
- 单文件上传 ≤ 50MB;单用户每日总上传 ≤ 500MB。
- HTTP 头加 `X-Content-Type-Options: nosniff` / `X-Frame-Options: DENY`。
- **HTTP-only 内网部署的明文密码风险**(D7):**已用户接受**。理论威胁是同网段嗅探,实际威胁低(公司内网信任边界 + 密码强制首次改 + bcrypt 落库)。如未来上公网必须切 HTTPS。

### NFR-5 可观测

- 每个 LangGraph run 输出结构化 JSON 日志(user_id / project_id / 节点名 / token 数 / 耗时 / 模型名 / 重试次数)到 stdout。
- 日志时间戳用 **Asia/Shanghai**(D8)。
- docker logs 由宿主机 logrotate 接管。
- 失败时记录完整堆栈到项目目录的 `errors.log`。
- 健康检查端点 `GET /health`(无需鉴权),只返回 **db / redis** 内部依赖连通状态(NFR-5 / 第 9 章)。LLM 连通由 `GET /api/me/api-key/test` 单独检查;原因:`/health` 应快、应只查内部依赖,不被外网拖慢。

---

## 7. 系统架构(纲要)

```
                            ┌──────────────┐
                            │  内网用户浏览器 │
                            │ (10 人同时在线)│
                            └───────┬──────┘
                                    │ HTTP (内网 IP:12123)
                                    │ JSON / SSE / 静态资源
┌───────────────────────────────────┴───────────────────────────────┐
│                       Docker Host (Linux 2c4g)                     │
│                          TZ=Asia/Shanghai                          │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │  容器: app                                                    │ │
│  │  ┌────────────────────────────────────────────────────────┐ │ │
│  │  │  FastAPI (uvicorn 单 worker)                            │ │ │
│  │  │  - 静态资源(/) → 前端 dist/(Vite + React 构建产物)    │ │ │
│  │  │  - REST API (/api/auth/*, /api/projects/*, ...)        │ │ │
│  │  │  - SSE 流推送                                          │ │ │
│  │  └────────────────────────────────────────────────────────┘ │ │
│  │                          │                                    │ │
│  │                          │ 入队工作流任务                      │ │
│  │                          ▼                                    │ │
│  │  ┌────────────────────────────────────────────────────────┐ │ │
│  │  │  arq worker(独立进程,supervisord 与 uvicorn 并列编排)  │ │ │
│  │  │  - LangGraph 状态机                                    │ │ │
│  │  │  - LiteLLM → DashScope                                  │ │ │
│  │  │    LLM-1: deepseek-v4-flash                             │ │ │
│  │  │    LLM-2: qwen3.6-max-preview                           │ │ │
│  │  │    LLM-3: qwen3.6-flash                                 │ │ │
│  │  │  - markitdown 文档抽取(不支持 PDF)                    │ │ │
│  │  │  - DOCX 流水线: mmdc → pandoc(直转)                  │ │ │
│  │  └────────────────────────────────────────────────────────┘ │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                  │                            │                    │
│                  │                            │                    │
│  ┌───────────────┴────────────┐  ┌───────────┴────────────────┐  │
│  │  容器: postgres (内网)      │  │  容器: redis (内网)         │  │
│  │  - User / Project / ...     │  │  - arq 队列                 │  │
│  │  - LangGraph checkpoint     │  │                             │  │
│  │  - TokenUsage / ReviewEvent │  │                             │  │
│  └─────────────────────────────┘  └─────────────────────────────┘  │
│                  │                                                 │
│  ┌───────────────┴─────────────────────────────────────────────┐  │
│  │  挂载卷 (宿主机)                                              │  │
│  │  /var/lib/bid-app/postgres-data    ← Postgres 数据           │  │
│  │  /var/lib/bid-app/projects/        ← 上传文档 / 章节 / docx  │  │
│  │  /var/lib/bid-app/backups/         ← 每日 03:00 pg_dump      │  │
│  └─────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ HTTPS(运行时 LLM 调用)
                                    ▼
                            ┌──────────────────┐
                            │ 阿里云 DashScope   │ ← 数据出公司网络
                            │ (Qwen / DeepSeek) │   (D3 已确认接受)
                            └──────────────────┘
```

**关键决策**:
- **app 与 arq worker 同容器、独立进程**(supervisord 编排两个进程,共用同一 `.env` 与 PostgreSQL/Redis 连接)。优点:省一个容器、不需要 nginx 反代、容器层共享镜像;代价:HTTP 与后台任务争用 CPU,但 2c4g 上反正只能这么干。**与 v2 描述的"同 Python 进程"区别:进程崩溃可独立重启,任务不会拖垮 HTTP**(详见 IMPLEMENTATION_SPEC v3 决定 D-A)。
- **不引入 nginx**:HTTP-only + 单进程 + 内网,uvicorn 直接服务静态资源足够。
- 未来扩容路径(不动代码):①升 4c8g host;②拆 arq worker 独立容器;③加 nginx 走 HTTPS;④引入 PaddleOCR 支持 PDF。

---

## 8. 数据模型(核心实体)

| 实体 | 关键字段 | 备注 |
|---|---|---|
| **User** | `id` / `username` / `password_hash` / `role`(`user`/`admin`)/ `is_active` / `must_change_password` / `created_at` / `last_login_at` | 自建账号,bcrypt 哈希;新建账号(含默认 admin)`must_change_password=true` |
| **ApiKey** | `id` / `user_id`(unique) / `provider`(=`dashscope`)/ `encrypted_key`(AES-GCM)/ `last_validated_at` / `created_at` / `updated_at` | 1 用户 1 把 Key,加密落库,前端不可读 |
| **Project** | `id` / `name` / `description` / `status` / `created_by` / `api_key_owner`(启动者 user_id,审计用) / `encrypted_api_key_snapshot`(启动时拷贝的 AES-GCM 密文,运行时解密 LLM 调用用) / `created_at` / `dir_path` | 团队共享池,任何登录用户可见;FR-7.5 双重快照 |
| **Document** | `id` / `project_id` / `kind`(`tech_spec`/`scoring`/`template`)/ `original_filename` / `markdown_path` / `file_size`(字节) / `extract_error`(可空) / `created_at` | 上传的原始文件 + 抽取后 md;文件类型限于 docx/doc/md/txt;`file_size` 用于日上传配额聚合(NFR-4 单用户日 500MB) |
| **Run** | `id` / `project_id` / `langgraph_thread_id` / `started_at` / `finished_at` / `status` | 一次完整工作流执行 |
| **Chapter** | `id` / `run_id` / `index` / `title` / `summary` / `key_points` / `target_pages` / `final_text` / `status`(`pending`/`generating`/`awaiting_review`/`approved`/`skipped`/**`failed`**)| 提纲解析后落库;`failed` 见 FR-4.7 |
| **ChapterVersion** | `id` / `chapter_id` / `version` / `body_markdown` / `feedback_in` / `decision` / `created_at` | 每次重写一条记录,保留历史 |
| **ReviewEvent** | `id` / `chapter_id` / `reviewer_id` / `decision`(含 `retry_failed`)/ `feedback_text` / `created_at` | 审计:谁审了哪章 / 谁触发了 failed 重试 |
| **TokenUsage** | `id` / `user_id` / `project_id` / `run_id` / `model` / `prompt_tokens` / `completion_tokens` / `created_at` | 计费/统计 |
| **DocxJob** | `id` / `project_id` / `status`(`pending`/`rendering_mermaid`/`pandoc`/`done`/`failed`)/ `error` / `output_path` / `created_at` / `finished_at` | DOCX 生成异步任务追踪;**简化方案后无 `merging` 阶段** |

LangGraph 自身的 checkpoint 表(`checkpoints` / `writes`)由 `langgraph-checkpoint-postgres` 自动建,不在我们建模范围。

---

## 9. API 草案

### 认证

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `POST` | `/api/auth/login` | body: `{username, password}` → 设 HttpOnly cookie + 返回 user 信息 | 公开 |
| `POST` | `/api/auth/logout` | 清 cookie | 已登录 |
| `POST` | `/api/auth/refresh` | refresh token → 新 access token | 公开(带 refresh cookie) |
| `GET` | `/api/auth/me` | 当前用户信息(含角色、API Key 是否已配) | 已登录 |

### 个人设置

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/me/change-password` | body: `{old_password, new_password}`;成功后置 `must_change_password=false`(首次改密页也用此端点) |
| `GET` | `/api/me/api-key` | 返回是否已配置(布尔)+ 上次校验时间;**不返回明文** |
| `PUT` | `/api/me/api-key` | body: `{key}`;后端做测试调用,通过则加密保存 |
| `DELETE` | `/api/me/api-key` | 删除 |
| `GET` | `/api/me/api-key/test` | 用已保存的 Key 测一次 DashScope 连通(替代 `/health` 查 LLM,见 NFR-5);成功更新 `last_validated_at` |
| `GET` | `/api/me/usage?month=YYYY-MM` | 当月 token 消费统计 |

### 项目

(以下端点全部要求 已登录;权限检查见各条说明)

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/projects` | 列表(团队所有项目,支持 `?status=` `?creator=`) |
| `POST` | `/api/projects` | 创建;需当前用户已配 API Key 否则 412 |
| `GET` | `/api/projects/{id}` | 详情 |
| `DELETE` | `/api/projects/{id}` | 仅创建者 / admin;**手动删除入口**(D6) |
| `POST` | `/api/projects/{id}/documents` | 上传文档(multipart);body 含 `kind`;非 docx/doc/md/txt 返回 415 |
| `POST` | `/api/projects/{id}/start` | 启动工作流;快照启动者 API Key |
| `GET` | `/api/projects/{id}/outline` | 拿提纲 |
| `PUT` | `/api/projects/{id}/outline` | 编辑提纲后确认 |
| `GET` | `/api/projects/{id}/chapters` | 所有章节及状态 |
| `GET` | `/api/projects/{id}/chapters/{idx}` | 单章详情(含历史版本 + 上次审核人) |
| `POST` | `/api/projects/{id}/chapters/{idx}/review` | 提交审核;body: `{decision, feedback?}`;记 reviewer_id |
| `POST` | `/api/projects/{id}/chapters/{idx}/retry` | **`failed` 章节手动重试**(FR-4.7);记入 ReviewEvent decision=`retry_failed` |
| `GET` | `/api/projects/{id}/proposal` | 最终全文 markdown(JSON) |
| `GET` | `/api/projects/{id}/proposal.md` | 直接下载 .md |
| `POST` | `/api/projects/{id}/proposal.docx` | 触发 DOCX 生成 → 返回 `job_id`(入 arq 队列) |
| `GET` | `/api/projects/{id}/proposal.docx` | 下载已生成的 .docx;未生成返回 409 |
| `GET` | `/api/projects/{id}/docx-job/{job_id}` | 查 DOCX 生成进度 |

### 管理员

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/admin/users` | 用户列表 |
| `POST` | `/api/admin/users` | 创建账号 body: `{username, password, role}` |
| `PUT` | `/api/admin/users/{id}/password` | 重置密码 |
| `PUT` | `/api/admin/users/{id}/disable` | 禁用 |
| `GET` | `/api/admin/usage?month=YYYY-MM` | 全员 token 消费 |

### SSE 流

| 路径 | 推送内容 |
|---|---|
| `GET /api/projects/{id}/stream` | 项目级事件流:`outline_ready` / `chapter_started` / `chapter_token` / `chapter_ready` / `awaiting_review` / `chapter_failed` / `proposal_ready` / `error` |

事件 envelope:`{type, project_id, chapter_index?, payload}`。

### 健康检查

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 公开,返回 `{app: ok, db: ok, redis: ok}` |

---

## 10. LangGraph 状态机契约

直接照搬 v10 设计文档 §3.3 的 5 个 Loop 变量作为 LangGraph State,**不引入新变量**。

```python
class WorkflowState(TypedDict):
    chapters: list[dict]           # 来自 §4.4 解析提纲
    current_index: int             # §3.3
    retry_count: int               # §3.3
    finalized_chapters: list[str]  # §3.3
    revision_feedback: str         # §3.3
    # 输入(只读)
    tech_spec_md: str
    scoring_md: str
    template_md: str
    pages_per_chapter: int
    max_retry_per_chapter: int
    api_key: str                   # 项目启动时快照的 DashScope Key
    # 输出
    final_proposal: str | None
```

节点 1:1 对应 v10 §4 各小节:

| LangGraph 节点 | 对应 v10 章节 | 模型 / 工具 |
|---|---|---|
| `extract_documents` | §4.2 | markitdown(docx/doc/md/txt) |
| `generate_outline` | §4.3 | LLM-1 = `deepseek-v4-flash` |
| `parse_outline` | §4.4 | Python(纯逻辑) |
| `pick_chapter` | §4.5.1 | Python |
| `write_chapter` | §4.5.2 | LLM-2 = `qwen3.6-max-preview`(prompt 含 `revision_feedback`) |
| `gen_visuals` | §4.5.3 | LLM-3 = `qwen3.6-flash` |
| `merge_chapter` | §4.5.4 | Python(模板拼接) |
| `human_review` | §4.5.5 | `interrupt()` 暂停 |
| `update_state` | §4.5.7 | Python |
| `assemble` | §4.6 | Python |

条件边:`update_state` → `pick_chapter`(还有章节)/ `assemble`(已完成)。

**Checkpointer**:`PostgresSaver` from `langgraph-checkpoint-postgres`。

**重试与失败**:LLM 节点(`generate_outline` / `write_chapter` / `gen_visuals`)内部封装 FR-3.9 的重试逻辑;3 次失败后通过 LangGraph 的异常通道把章节状态置 `failed`,工作流暂停在该节点等用户手动 retry(retry 端点会重新触发该节点)。

---

## 11. 里程碑

| 里程碑 | 交付内容 | 验收方式 | 工期估计 |
|---|---|---|---|
| **M0 · CLI 验证** | 命令行跑通完整工作流(无 UI、无认证、无 DB),给 3 个固定文件,模拟终端做章节审核;验证 LLM-1/2/3 三个模型能调通 | 跑通 1 个真实投标样本,产出完整 markdown | 1-2 天 |
| **M1 · 后端核心 API** | FastAPI + LangGraph + PostgresSaver + arq;REST 项目相关端点 + SSE;Postgres + Redis 跑起来;FR-3.9 重试 + FR-4.7 失败重试端点 | API 测试脚本通过,工作流可在 docker 里执行并人审,模拟 LLM 失败能正确进入 `failed` 状态 | 3-4 天 |
| **M2 · 认证 + 用户管理 + API Key** | FR-6 / FR-7 全实现;admin 端点;限流 | curl 跑通登录、创建用户、配 Key、重置 Key | 2-3 天 |
| **M3 · DOCX 导出**(Pandoc 直转,简化方案) | mermaid 预渲染 + pandoc 直转;arq 串行化任务;最小 reference.docx 样式定义 | 真实 markdown 输出 docx 可打开、章节层级正确、表格/图片正常 | 2 天 |
| **M4 · 前端 v1**(Vite + React) | 8 个视图(P0 登录 ~ P8 用户管理)全部能用;react-markdown 流式渲染 + 三按钮审核 + Key 配置 + admin 页 + `failed` 章节重试按钮 | 浏览器跑通完整流程含 .docx 下载 | 5-7 天 |
| **M5 · 部署打包** | docker compose(3 容器) + 默认 admin 账号 + 备份 cron + 健康检查 + TZ=Asia/Shanghai | 在测试服务器一键起、内网 IP 可访问、6 小时无 OOM、cron 备份产出 | 2-3 天 |

总计:大约 **15-21 个工作日**(简化 DOCX 方案后比 v0.3 少 1-2 天)。

---

## 12. 未决问题

| # | 问题 | 当前默认 |
|---|---|---|
| Q1 | DOCX 是否套公司 Word 模板 | **不套**(D5),Pandoc 直转 + 最小 reference.docx 样式;后续如需公司模板再加 |
| Q2 | DOCX 文件命名格式 | `{project_name}_技术方案_{YYYYMMDD}.docx`,YYYYMMDD 用 Asia/Shanghai |
| Q3 | DOCX 内的 Mermaid 图渲染 | 用 mermaid-cli(headless chromium)本地渲染为 PNG 嵌入,渲染失败保留代码块 |
| Q4 | DOCX 是否在工作流末端自动生成 | 否,按需触发 |
| Q5 | prompt 是否在前端可改 | 暂不,硬编码后端 |
| Q6 | API Key 是否支持项目级覆盖 | 否(本期),只支持用户级;启动时快照到项目 |
| Q7 | 是否开放注册 | 否,账号由 admin 创建 |
| Q8 | 首次登录强制改密码 | 是(已确认),实现见 FR-6.6 |
| Q9 | 是否需要审核辅助打分 | 不需要 |
| Q10 | 章节并行生成 | 不,严格串行 |
| Q11 | 服务器规格不够时的扩容路径 | 已确认保留升级路径:① 升 4c8g host;② 拆 arq worker 独立容器;③ 加 nginx 走 HTTPS;④ 引入 OCR 支持 PDF |
| Q12 | 备份恢复演练 | M5 出文档,实操演练待用户首次部署后做 |
| Q13 | 默认 admin 用户名密码 | 已确认:`admin` / `admin123` 硬编码 + 首次登录强制改密 |
| Q14 | LLM 模型组合 | 已确认(D1):LLM-1=deepseek-v4-flash / LLM-2=qwen3.6-max-preview / LLM-3=qwen3.6-flash |
| Q15 | 文档格式范围 | 已确认(D2):DOCX / DOC / MD / TXT;**PDF 不支持** |
| Q16 | LLM 调用数据机密性 | 已确认(D3):接受经 DashScope 出公司网络;界面 banner 一次性提示用户 |
| Q17 | LLM 失败重试策略 | 已确认(D4):2 次重试 / 2s+5s 退避 / 3 次失败 → 章节 `failed` / 用户手动重试 |
| Q18 | 数据保留 | 已确认(D6):永不自动清理,用户手动删 |
| Q19 | HTTP-only 内网风险 | 已确认(D7):接受;未来上公网必须切 HTTPS |
| Q20 | 时区 | 已确认(D8):Asia/Shanghai |
| Q21 | 单章 LLM 超时上限 | 10 分钟(含重试),**仅计 LLM 调用时长**,人工审核等待期间不计时;每次 `revise` 重写重置计时(FR-3.10) |

---

## 13. 术语表

| 术语 | 含义 |
|---|---|
| **v10 设计文档** | 同目录上级的 `技术方案自动生成工作流 — Dify 搭建指南(含人工审核).md`,描述工作流内核 |
| **章节 (Chapter)** | 提纲解析后的一个生成单元 |
| **Run** | 一次工作流执行,从启动到 `done`/`failed` |
| **审核三按钮** | 通过 / 不通过 / 跳过,见 v10 §4.5.5 |
| **状态机变量** | `current_index` / `retry_count` / `finalized_chapters` / `revision_feedback` / `chapters_array` |
| **interrupt** | LangGraph 提供的"暂停 workflow 等人输入"原语 |
| **团队共享池** | 任何登录用户可看到/审核所有项目;只有创建者和 admin 可删除 |
| **API Key 快照** | 项目启动瞬间记录该用户当前的 API Key 引用,后续工作流(包括别人触发的重写、failed 重试)都用这把 Key |
| **章节 failed 状态** | LLM 调用 3 次重试都失败 / 单章超时 10 分钟,触发人工重试入口 |

---

## 14. 评审清单

### 已用户确认事项(v0.4)

- [x] **目标形态**:内网服务器、IP 直接访问、HTTP-only、10 用户、2c4g 起步(保留升级路径,见 Q11)
- [x] **角色模型**:自建账号 + 团队共享池 + admin 角色
- [x] **API Key 模型**:用户级配置、AES-GCM 加密、项目启动时快照
- [x] **前端栈**:Vite + React(SPA)
- [x] **默认 admin 账号**:`admin` / `admin123` 硬编码 + 首次登录强制改密
- [x] **SSE 鉴权**:cookie
- [x] **2c4g 内存预算**:理解 OOM 风险,采用单 worker + DOCX 串行化等约束
- [x] **D1 LLM 模型组合**:deepseek-v4-flash / qwen3.6-max-preview / qwen3.6-flash
- [x] **D2 文档格式**:DOCX/DOC/MD/TXT,PDF 不支持
- [x] **D3 数据机密性边界**:接受 LLM 上下文经 DashScope 出公司网络
- [x] **D4 LLM 重试**:2 次重试 / 2s+5s 退避 / failed → 用户手动重试
- [x] **D5 DOCX 简化方案**:Pandoc 直转 + 最小 reference.docx,**不套公司 Word 模板**
- [x] **D6 数据保留**:永不自动清理
- [x] **D7 HTTP-only 风险**:接受
- [x] **D8 时区**:Asia/Shanghai

### 仍待评审

- [ ] FR-1 ~ FR-7 功能范围无遗漏
- [ ] 第 7 章架构图(已加入 DashScope 出网示意)与你的预期一致
- [ ] 第 8 章数据模型够用(章节加 `failed` 状态、ReviewEvent decision 加 `retry_failed`)
- [ ] 第 9 章 API 含 `/chapters/{idx}/retry` 端点
- [ ] 第 10 章 LangGraph 节点表已绑定具体模型
- [ ] 第 11 章里程碑(M3 工期减到 2 天,总工期 15-21 天)
- [ ] 第 12 章未决问题剩余 Q21 单章超时 10 分钟可接受
