# 独立 AI 商品出图平台协作规则

## 当前方向

- 产品以“批量生产型商品图平台”为主，单商品创作台为辅；不要把它做成自由画布或表单集合。
- 运营语言使用“项目、商品/SKU 分组、输出图、版本、创意 Brief”；不要把 Batch、Cluster、Attempt 等技术词暴露给普通用户。
- 每张上传图片默认形成一个商品分组；图片拖入同一分组即共同产出一套图，不让员工维护“多图关系”。分组内第一张图片是主参考，员工可拖动横向缩略图排序；N1 分析全部图片，N2 内部归并角度并识别颜色/款式外观。
- 每次上传或 ERP 导入都明确选择“导入并自动出图”或“导入后整理”；整理模式只保存素材和商品卡，员工点击“预备生成”前不得调用 AI；自动模式与预备模式共用 N1–N7、1+8 生成、结果修改和本地导出链路。

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
- 正式素材、SKU 拉取图、生成结果和历史版本写入私有 OSS 前缀；导出 ZIP 临时生成并由浏览器下载到员工本地，不在服务器或 OSS 长期保留。
- 新项目默认“Shopee / 东南亚通用 / 1:1 / 1K”。自动模式和正式生成在 Prompt 缺失或过期时自动运行 N1–N7 后继续；整理导入本身保持零 AI。失败只重做失败项，旧版本不可覆盖。
- 未生成商品可以删除；有 Prompt、生成或审核历史的商品只允许归档。归档商品不得再次进入 Prompt、生成、审核或导出。
- 默认模板第 1 槽是标准白底产品主图，完成归档后才提交第 2–9 槽。Shopee VN 普通店例外为“第 1 槽真实来源图直通、第 2 槽白底、第 3–9 槽营销图”；调度必须按白底槽位语义而非固定顺序识别门禁，并把白底结果加入后续生成参考。
- 平台规则、套图模板、Prompt 与参考图在生成时必须可追溯到快照；未验证的规则不得宣称合规。

## Prompt OS 与结果边界

- 生成指令使用已确认事实、直接观察和显式披露的合理推断；每条推断必须保存置信度、风险、证据和允许用途。价格、认证、疗效、减重、美容前后对比、站外导流等高风险内容不得仅靠推断进入图片。
- 竞品图只能通过已批准的 `gpt-5-nano-2025-08-07` 视觉观察器提炼为抽象的构图、节奏或风格策略；绝不进入生成参考图数组、`gpt-image-2`、`deepseek-v4-pro`、生产 Prompt、导出包或商品事实来源。
- APIMart 中文文档与账户契约测试是模型 ID、端点、参数、响应、限流和计费的唯一接入依据；上游模型文档只作能力参考，发生冲突时以 APIMart 为准。
- APIMart 当前契约：`deepseek-v4-pro` 文本节点走非流式 Chat Completions；`gpt-5-nano-2025-08-07` 视觉观察走 Responses，文本从 `output[].content[].text` 提取；`gpt-image-2` 生成前先 `/v1/uploads/images` 上传我方参考图，`image_urls` 使用 URL 字符串数组，不使用 base64 或 `{url: ...}` 对象数组。
- Prompt OS v3 的 N1–N4/N8/N9 共用事实链，N5–N7 分为 generic、shopee、tiktok 三套营销链。生产模板必须保留完整角色、输入边界、营销/事实规则和严格 JSON 约束；DeepSeek 节点通过实际 `system` 消息接收完整模板。不得用一句职责摘要替代；`3500` 字符上限只约束最终单图 Prompt。
- Prompt OS 4.1 是下一轮 GPT-5.5 唯一执行基线；3.1–4.0 只保留为历史推演。4.1 的关键边界是：导入整理零 AI、正式生成缺 Prompt 自动预备、一卡多图共同产出一套 1+8、N2 输出 `product_family` 与 `target_appearances`、N5 按 FAB/场景占有/情绪触发/拟人表达/身份表达动态策划，并输出“商品事实→用户结果→使用画面→情绪触发→文案角度”的 `copywriting_chain`；N6 先生成三条目标语言文案候选并自评，再把选中文案逐字锁进英文生图 Prompt；N7 采用宽门禁，平台建议、文案质量、普通语言风险和身份不完整只进人工复核提示，只有 Prompt 超长、版本过期、无可用我方参考、竞品图误入参考和高风险可见内容才硬阻断；节点失败先内部归一化，不能把 `image_role`、Schema、JSON、`string`、英文模型错误暴露给员工。
- Prompt Worker 负责商品视觉理解、结构化 Brief/Prompt 和 9 槽 PromptVersion；Generation Worker 负责异步生图、白底图先行、轮询和归档。
- 同一卡内全部图片共同定义一套图：N2 在 `analysis_snapshot` 保存 `target_appearances`，N5 为逐槽计划保存 `appearance_ids`；白底与款式总览覆盖全部目标外观，其余槽位可选子集，但整套必须覆盖全部外观。N6 为每槽选择覆盖目标外观所需的最少参考图。
- 输出图生成成功后即可进入选择式 ZIP，默认勾选每个槽位最新完成图；人工审核/圈选修改只影响质量判断、修订和历史版本选择，不再作为导出门槛。取消选择、技术失败重做、主动再生成和圈选修改都创建或选择明确版本，不覆盖历史。

## 安全与部署

- 密钥、密码、OSS 凭据、供应商 token 只能存在本地/服务器 `.env`；不得写入仓库、文档、前端、日志或聊天摘要。
- 真实付费调用、真实 OSS、生产部署、账号策略或网络入口变更属于发布门禁，需主 Agent 明确签核。
- Hermes 操作只使用 `ssh hermes-remote`；部署属于全局写操作，先声明操作并获取 `.codex_locks/global.lock`。
- React 生产构建由 Caddy 同源提供静态资源，Caddy 只代理 Django 的 API、认证、后台、健康检查、迁移期 `/batches/` 和 Django 静态资源；浏览器不保存认证 token。
- 规则运行时装载平台官网主规则包，再叠加已验证的市场/店铺覆盖；没有站点覆盖时复用平台官网主规则包并标记 fallback。只有记录来源、版本和核对日期的规则可作为官方硬规则，未验证项不得宣称自动合规。

## 权威文档

- 业务边界确认：`docs/project/REQUIREMENTS-BOUNDARY-CONFIRMATION.md`（A–N 已确认，是唯一产品边界）；`SYSTEM-BOUNDARY-CONFIRMATION.md` 只保留原始答题记录
- 当前产品设计：`docs/superpowers/specs/2026-07-30-dual-speed-product-platform-design.md`
- 当前唯一推进计划：`docs/superpowers/plans/2026-07-31-phased-delivery-roadmap.md`
- Prompt OS v3 九节点契约：`docs/superpowers/specs/节点prompt设定初稿.md`
- Prompt OS 4.1 节点与营销生成设计：`docs/superpowers/specs/2026-08-02-prompt-os-4.1-node-marketing-design.md`（下一轮 GPT-5.5 唯一执行基线）
- Prompt OS 4.1 GPT-5.5 实施计划：`docs/superpowers/plans/2026-08-02-prompt-os-4.1-gpt55-implementation.md`
- Prompt OS 4.1 GPT-5.5 执行索引：`docs/project/PROMPT-OS-4.1-EXECUTION-INDEX.md`（一页版节点/链路/错误/UI 边界）
- GPT-5.5 Prompt OS 4.1 执行任务书：`docs/project/LEADER-GOAL-PROMPT-OS-4.1.md`
- Prompt OS 3.4 节点与执行设计：`docs/superpowers/specs/2026-08-02-prompt-os-3.4-node-execution-design.md`（历史设计，已由 4.1 收口）
- Prompt OS 3.4 GPT-5.5 实施计划：`docs/superpowers/plans/2026-08-02-prompt-os-3.4-gpt55-implementation.md`（历史计划）
- GPT-5.5 Prompt OS 3.4 执行任务书：`docs/project/LEADER-GOAL-PROMPT-OS-3.4.md`（历史任务书）
- Prompt OS 4.0 节点生产设计：`docs/superpowers/specs/2026-08-02-prompt-os-4.0-node-production-design.md`（历史设计，已由 4.1 收口）
- Prompt OS 4.0 GPT-5.5 实施计划：`docs/superpowers/plans/2026-08-02-prompt-os-4.0-gpt55-implementation.md`（历史计划）
- GPT-5.5 Prompt OS 4.0 执行任务书：`docs/project/LEADER-GOAL-PROMPT-OS-4.0.md`（历史任务书）
- Prompt OS 3.2 节点与工作流设计：`docs/superpowers/specs/2026-08-02-prompt-os-3.2-node-workflow-design.md`
- Prompt OS 3.2 实施计划：`docs/superpowers/plans/2026-08-02-prompt-os-3.2-node-workflow-implementation.md`
- GPT-5.5 执行任务书：`docs/project/LEADER-GOAL-PROMPT-OS-3.2.md`
- Prompt OS 3.3 生产节点设计：`docs/superpowers/specs/2026-08-02-prompt-os-3.3-production-node-design.md`（历史设计，已由 3.4 收口）
- Prompt OS 3.3 实施计划：`docs/superpowers/plans/2026-08-02-prompt-os-3.3-implementation-plan.md`
- GPT-5.5 Prompt OS 3.3 执行任务书：`docs/project/LEADER-GOAL-PROMPT-OS-3.3.md`（历史任务书）
- Prompt OS 3.1 营销文案设计：`docs/superpowers/specs/2026-08-02-prompt-os-3.1-marketing-copy-design.md`（历史设计，3.2 已补工作台/状态/错误/多图链路）
- Prompt OS 3.1 实施计划：`docs/superpowers/plans/2026-08-02-prompt-os-3.1-marketing-copy-implementation.md`（历史计划）
- GPT-5.5 旧执行任务书：`docs/project/LEADER-GOAL-PROMPT-OS-3.1.md`（历史任务书）
- 主 Agent 任务书：`docs/project/LEADER-GOAL-DUAL-SPEED-PLATFORM.md`
- 产品方向与调研：`docs/research/2026-07-29-top-image-platform-redesign-research.md`
- Agent 协作与交付设计：`docs/superpowers/specs/2026-07-29-agent-orchestrated-delivery-design.md`
- React 前端架构：`docs/superpowers/specs/2026-07-29-react-frontend-architecture-design.md`
- 后端、安全与部署基线：`docs/specs/2026-07-28-independent-image-platform-design.md`
- 运行手册：`docs/runbook.md`
