# 独立 AI 商品出图平台协作规则

## 当前方向

- 产品以“批量生产型商品图平台”为主，单商品创作台为辅；不要把它做成自由画布或表单集合。
- 运营语言使用“项目、商品/SKU 分组、输出图、版本、创意 Brief”；不要把 Batch、Cluster、Attempt 等技术词暴露给普通用户。
- 每张上传图片默认形成一个商品/SKU 分组；拖入同一分组表示同一商品的多角度参考。

## 主 Agent 与专业 Agent

- 主 Agent 是项目负责人，也是唯一的集成、合并与发布签核人；用户只需要与主 Agent 对齐。
- 长期角色采用“一 Agent 一会话”：产品与 Prompt OS、前端体验、后端平台、平台规则/合规、QA/发布各自维护独立上下文。短小、只读、无状态核查由主 Agent 临时分派，不创建长期会话。
- 每项任务必须在 `docs/project/STATUS.md` 记录 owner、目标、输入契约、输出契约、文件边界、验证、状态和阻塞；需求、接口和决定也必须回写仓库文档，聊天不是项目事实来源。
- 并行任务不得编辑同一文件。涉及模型、迁移、认证、额度、密钥、部署或供应商计费的改动，只有主 Agent 审核后才能合并。
- 每项任务必须返回：改动范围、验证命令与结果、风险、未完成项；没有验证不得标记完成。

## 工程边界

- Django 5.2 继续负责登录、权限、后台、API、数据库事务与任务引擎；运营工作台采用 React + TypeScript + Vite，详见 `docs/superpowers/specs/2026-07-29-react-frontend-architecture-design.md`。
- 前端服务端状态使用 TanStack Query；拖拽分组使用 dnd-kit；局部 UI 状态优先 React 原生 state/context。首版不引入 Redux、Zustand、Next.js、Redis、消息队列或微服务。
- 生产由 Caddy 同源托管前端静态资源并转发 Django API；保留 Django 的 session Cookie 和 CSRF 边界，不在浏览器存储认证 token。
- 平台登录使用 ERP 校验并创建本地影子用户；SKU 导入必须使用当前登录用户服务端 session 中的 ERP Token，不得回退为平台固定商品资料账号。
- 正式素材、SKU 拉取图、生成结果和导出 ZIP 写入私有 OSS 前缀；本地 `MEDIA_ROOT` 只用于开发和假模式回退。
- 生成前必须走预检与人工确认；失败只重做失败项，旧版本不可覆盖。
- 不论目标平台、市场或营销模板，套图第 1 槽始终是标准白底产品主图：完整、真实、无促销文字、无水印，且受商品身份锁约束；平台差异仅作用于后续槽位。同一批次、商品集群和模板的第 2–8 槽，只有第 1 槽技术完成后才能提交；主图失败或取消时后续槽位保持排队、不得调用供应商，直到白底图重做完成。
- 平台规则、套图模板、Prompt 与参考图在生成时必须可追溯到快照；未验证的规则不得宣称合规。

## Prompt OS 与审核边界

- 生成指令只使用已确认的商品事实、身份锁、已发布模板和当前项目参考图；缺失信息必须显式提示，不能臆造。
- 竞品图只能通过已批准的 `gpt-5-nano-2025-08-07` 视觉观察器提炼为抽象的构图、节奏或风格策略；绝不进入生成参考图数组、`gpt-image-2`、`deepseek-v4-pro`、生产 Prompt、导出包或商品事实来源。
- APIMart 中文文档与账户契约测试是模型 ID、端点、参数、响应、限流和计费的唯一接入依据；上游模型文档只作能力参考，发生冲突时以 APIMart 为准。
- Prompt Worker 负责结构化 Brief/Prompt 工作；Generation Worker 负责异步生图、轮询和归档。当前 Prompt Worker 尚是占位循环，不能作为异步 Prompt 已交付的证据。
- 输出图必须经过人工审核；只有 `accepted` 版本允许进入平台导出。技术失败重做和人工修改都创建新版本，不覆盖历史。

## 安全与部署

- 密钥、密码、OSS 凭据、供应商 token 只能存在本地/服务器 `.env`；不得写入仓库、文档、前端、日志或聊天摘要。
- 真实付费调用、真实 OSS、生产部署、账号策略或网络入口变更属于发布门禁，需主 Agent 明确签核。
- Hermes 操作只使用 `ssh hermes-remote`；部署属于全局写操作，先声明操作并获取 `.codex_locks/global.lock`。
- React 生产构建由 Caddy 同源提供静态资源，Caddy 只代理 Django 的 API、认证、后台、健康检查、迁移期 `/batches/` 和 Django 静态资源；浏览器不保存认证 token。
- 正式规则/模板种子只能由独立 `template-seed` 任务提交：仅 global generic 可处于 `published` 基线，Shopee/TikTok 在官方规则、来源和版本获批前一律 `draft`，不得宣称自动合规。

## 权威文档

- 产品方向与调研：`docs/research/2026-07-29-top-image-platform-redesign-research.md`
- Agent 协作与交付设计：`docs/superpowers/specs/2026-07-29-agent-orchestrated-delivery-design.md`
- React 前端架构：`docs/superpowers/specs/2026-07-29-react-frontend-architecture-design.md`
- 后端、安全与部署基线：`docs/specs/2026-07-28-independent-image-platform-design.md`
- 运行手册：`docs/runbook.md`
