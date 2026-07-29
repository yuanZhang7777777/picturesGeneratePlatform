# 独立 AI 商品出图平台协作规则

## 当前方向

- 产品以“批量生产型商品图平台”为主，单商品创作台为辅；不要把它做成自由画布或表单集合。
- 运营语言使用“项目、商品/SKU 分组、输出图、版本、创意 Brief”；不要把 Batch、Cluster、Attempt 等技术词暴露给普通用户。
- 每张上传图片默认形成一个商品/SKU 分组；拖入同一分组表示同一商品的多角度参考。

## 主 Agent 与专业 Agent

- 主 Agent 负责范围、产品决策、任务拆分、集成、验收、文档一致性与发布签核；用户只需要与主 Agent 对齐。
- 长期角色采用“一 Agent 一会话”：主会话是项目控制台；产品体验、端到端功能交付、生图平台、质量与发布分别在独立会话持续负责。短小、只读、无状态的核查由主会话临时分派，不创建长期会话。
- 前端与后端是按任务临时拆分的专业能力，不是永久瀑布队列；每个功能交付 Agent 对一个完整运营结果负责。
- 角色可直接沟通接口和阻塞项，但需求、决策、接口契约、任务状态和验收证据必须回写 `docs/project/STATUS.md`；聊天不是项目事实来源。
- 并行任务不得编辑同一文件；涉及模型、迁移、认证、额度、密钥、部署或供应商计费的修改必须由主 Agent 审核后合入。
- 每项任务必须返回：改动范围、验证命令与结果、风险、未完成项；没有验证不得标记完成。

## 工程边界

- Django 5.2 继续负责登录、权限、后台、API、数据库事务与任务引擎；运营工作台采用 React + TypeScript + Vite，详见 `docs/superpowers/specs/2026-07-29-react-frontend-architecture-design.md`。
- 前端服务端状态使用 TanStack Query；拖拽分组使用 dnd-kit；局部 UI 状态优先 React 原生 state/context。首版不引入 Redux、Zustand、Next.js、Redis、消息队列或微服务。
- 生产由 Caddy 同源托管前端静态资源并转发 Django API；保留 Django 的 session Cookie 和 CSRF 边界，不在浏览器存储认证 token。
- 生成前必须走预检与人工确认；失败只重做失败项，旧版本不可覆盖。
- 平台规则、套图模板、Prompt 与参考图在生成时必须可追溯到快照；未验证的规则不得宣称合规。

## 安全与部署

- 密钥、密码、OSS 凭据、供应商 token 只能存在本地/服务器 `.env`；不得写入仓库、文档、前端、日志或聊天摘要。
- 真实付费调用、真实 OSS、生产部署、账号策略或网络入口变更属于发布门禁，需主 Agent 明确签核。
- Hermes 操作只使用 `ssh hermes-remote`；部署属于全局写操作，先声明操作并获取 `.codex_locks/global.lock`。

## 权威文档

- 产品方向与调研：`docs/research/2026-07-29-top-image-platform-redesign-research.md`
- Agent 协作与交付设计：`docs/superpowers/specs/2026-07-29-agent-orchestrated-delivery-design.md`
- React 前端架构：`docs/superpowers/specs/2026-07-29-react-frontend-architecture-design.md`
- 后端、安全与部署基线：`docs/specs/2026-07-28-independent-image-platform-design.md`
- 运行手册：`docs/runbook.md`
