# 项目控制板

最后更新：2026-08-03
维护者：主 Agent
状态：`lxc/workbench-v4` 本轮 R0-13 已部署到预览；Prompt OS runtime 提升到 4.1.0，N5/N6 新增商品展示数量、构图、灯光材质和文字版式主题契约，工作台新增暂停入口并把商品卡编辑收敛为“名称 + 补充信息”。Prompt OS 4.1 的真实付费 1+8、50 张质量压测、ERP/OSS 浏览器 smoke 和全量历史测试宽门禁收口仍未完成。

## 当前目标

按 [`2026-07-31-phased-delivery-roadmap.md`](../superpowers/plans/2026-07-31-phased-delivery-roadmap.md) 严格串行完成阶段 0–6。本轮先补阶段 2 的项目内配置和商品覆盖，以及阶段 3 的生成硬门禁；两者的全量测试、浏览器 E2E、真实 ERP/OSS smoke 与新的真实 1+8 验收均未完成。

## 阶段状态

| 阶段 | Owner | 文件边界 | 验证 | 状态 | 下一门禁 |
| --- | --- | --- | --- | --- | --- |
| 0 基线与任务重排 | 主 Agent | 只读代码、测试、服务器状态与路线图 | 后端 193 passed；前端 45 passed；Vite build passed；远端数据库与五服务健康 | 已完成 | 开发分支基线 Git `e764b0d`；线上版本未改 |
| 1 批量整理工作台 | 前端体验 + 后端删除/归档 | `frontend/src/**`、相关 Django 模型/API/测试；Agent 文件不重叠 | 后端 206 passed；前端 51 passed；Vite build、浏览器图片/文件夹 E2E、迁移 `0013`、五服务和公网健康通过 | 已完成 | 启动阶段 2 前先冻结 ERP/市场配置接口 |
| 2 ERP 与市场配置 | ERP/配置 + 前端市场选择 | 名称式新建项目、项目默认与商品覆盖、两套导入 | 本地回归通过并部署；真实 ERP 登录、1 SKU、OSS E2E 待运营验证 | 已部署待人工验收 | 真实员工浏览器 SKU/OSS smoke |
| 3 Prompt OS 运行契约 | Prompt OS + 规则输入 | N1–N7 严格 Schema、当前证据、Generation 硬门禁和 Worker CAS | 后端 335、前端 83、Vite build、远端迁移/模板发布/健康通过 | 已部署待质量验收 | 新一轮真实 1+8 与运营质量评价 |
| 4 生成审核导出闭环 | 生成审核 + QA | 白底门禁、修订、审核、ZIP | 六类 54 图质量基准 | 未开始 | 阶段 3 通过 |
| 5 规则/竞品/Prompt Lab | 规则 + Prompt Lab | 规则包、竞品隔离、模板发布 | 来源追溯、隔离、发布回滚 | 未开始 | 阶段 4 通过 |
| 6 正式发布与容量 | 性能 + QA/运维 | HTTPS、调度、限流、备份、监控 | 5→20→50→100 人与 2→500 分级压测 | 未开始 | 阶段 5 通过 |

旧 P0 任务和 2026-07-28 至 2026-07-30 的实施计划仅保留为已部署基线与历史证据，不再决定执行顺序。`docs/project/用户操作流程以及相关触发.md` 明确不在当前路线图维护范围内。

## 角色会话

| 角色 | 会话职责 | 当前交付 |
| --- | --- | --- |
| 主 Agent | 优先级、集成、风险与发布签核 | 本控制板、交付规格、任务拆分 |
| 产品与 Prompt OS | 商品事实、身份锁、Brief、套图意图、验收与变更控制 | Prompt OS 领域契约 |
| 前端体验 | React 工作台、信息架构、交互和可见状态 | 工作台与创作台体验 |
| 后端平台 | API、数据、权限、商品准备、任务状态、版本与存储 | 可被前端消费的受限接口 |
| 平台规则/合规 | 官方规则、模板、审核 checklist、来源与版本 | 可发布的规则/模板输入 |
| QA/发布 | 自动回归、手工验收、安全门禁、预览与发布报告 | 假模式验收和发布证据 |

## 当前任务契约

| ID | Owner | 目标 | 输入契约 | 输出契约 | 文件边界 | 验证 | 状态 | 阻塞 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C0-01 | 主 Agent | 固定运营术语与体验路线 | 业务确认 | 统一的“项目、商品/SKU、输出图、版本、Brief”术语 | 协作/产品文档 | 文档交叉核对 | 已完成 | 无 |
| C0-02 | 主 Agent | 盘点前端、后端、部署基线 | 现有仓库与运行手册 | 可复用能力和生产缺口 | 盘点/运行文档 | 只读核查 | 已完成 | 无 |
| C0-03 | 主 Agent | 建立主 Agent 项目治理 | 产品方向 | 角色、交接和签核规则 | `CLAUDE.md`、本控制板 | 规则与软链核对 | 已完成 | 无 |
| C0-04 | 主 Agent | 确认前端技术栈 | 交互/性能目标 | React + TypeScript + Vite、TanStack Query、dnd-kit 与同源 Django API | 前端架构文档 | 架构审阅 | 已完成 | 无 |
| C0-05 | 主 Agent | 收口精简需求边界 | 已填写 A–N 与双速流程决定 | 唯一边界、最终设计、实施计划和 `/goal` 任务书 | 需求、设计、计划、控制板 | 编号/矛盾/范围/命令自检 | 已完成 | 无 |
| T2-01 | 前端体验 + 后端平台 | ERP 图片入口、退出登录与单/多图选择 | 当前 ERP 登录会话、受限图片下载、Django `POST /logout/` 与现有上传入口 | ERP 图片归档、成功后跳转登录页且失败可见、单/多图和文件夹选择入口 | `platform_app` ERP 下载/认证、`frontend/src/api.ts`、`layout.tsx`、`ImportPanel.tsx`、聚焦测试 | 后端 207 passed；前端 56 passed；生产真实 JPEG 下载、会话清除、UI 标签与健康检查通过 | 已部署（Git `27a580f`） | 真实员工浏览器 SKU/OSS smoke 待验收 |
| R0-01 | 主 Agent + 后端 + Prompt OS + 前端体验 | 显式预备生成、商品工作台与 Prompt OS v3 | 已确认 v3 实施计划、现有项目/商品/API、旧长 Prompt 原文 | 通用电商/东南亚通用默认值；整理导入零 AI；预备 N1–N7；常驻导入区；放大商品卡；整行展开卡片；Prompt 标题中文槽位化；generic/shopee/tiktok 营销链；当前证据硬门禁 | Django、`frontend/`、Prompt 模板与五份权威文档；不改 `用户操作流程以及相关触发.md` | 后端 335 passed；前端 83 passed；本轮工作台 11 passed；Vite build；远端迁移、模板发布、Django check、健康检查通过 | 已部署（Git `1ef34e1`） | 真实员工 ERP/OSS 浏览器 E2E 与新一轮付费 1+8 待运营验收 |
| R0-11 | 主 Agent + 前端体验 | 普通员工界面中文化与登录样式修复 | 用户要求商品卡、1+8 提示词、结果页、登录页和系统状态不要暴露英文技术词；补充反馈要求 Prompt 框必须是画面提示词、营销图要有目标语言图片文案、选择按钮靠近生成入口 | 结果槽位英文模板名映射为中文；Prompt 编辑面板隐藏内部英文生图指令，只显示中文画面提示词和目标语言文案；商品卡把常见英文识别结果转为中文；`Product is being prepared` 等错误转为中文；顶部选择按钮移到右下角固定生成条；营销图 fallback 默认生成 1–3 行目标语言可见文案 | `frontend/src/**`、Prompt 模板、相关测试、本控制板 | 前端 Prompt/Workbench 21 passed；后端本地化 Prompt 3 passed；Django check passed；Vite build passed；`git diff --check` passed；本地迁移 dry-run 两次超时但本轮无模型改动；远端 Compose build/migrate/seed/check 与 `/health/ready` 通过 | 已部署到预览（Git `94e8489`）；回退备份 `/opt/independent-image-platform-backups/20260803_104657-94e8489-pre-r011-prompt-controls` | 未跑真实 50 张付费压测；真实付费生成质量与 ERP/OSS 浏览器 smoke 未验收 |
| R0-12 | 主 Agent + Prompt OS + 前端体验 | 正向视觉导演提示词与开放创意链路修复 | 用户最新反馈：最终给 Image2/运营编辑的提示词不能是“真实动作”等空泛指导，不能把营销节点输出成字段摘要、防错清单、文字安全区或负向列表；图片提示词应包含画面内容、构图、灯光、质感、动作、版式文字，并给模型保留营销创意空间 | 前端只展示后端提供的中文 `displayPrompt` 或人工中文稿，没有中文终稿时保持空白占位，不再自动生成“画面/主体/动作/构图”摘要；N6 fallback 输出正向中文广告图导演稿；N5/N6 模板要求先发散创意视觉方案，再把运营可见 `display_prompt` 写成自然段正向画面提示词，负向清单和风险留给内部/N7 | `frontend/src/components/PromptEditor.tsx`、`platform_app/services.py`、`platform_app/prompt_templates_v3.py`、Prompt OS 4.1 规格、相关测试、本控制板 | 前端 Prompt 测试 9 passed；后端 N6 聚焦测试 3 passed；模板测试 12 passed；Django check passed；Vite build passed；`git diff --check` passed；全量 `tests/test_prompt_os.py tests/test_prompt_templates_v3.py` 仍有 15 个既有 N1/N2/旧 worker 断言失败；远端 Compose build/migrate/seed/check、服务状态、`/health/ready` 通过 | 已部署到预览（Git `70ecb34`）；回退备份 `/opt/independent-image-platform-backups/20260803_114312-70ecb34-pre-r012-positive-visual-prompts` | 未跑真实付费 1+8 和 50 张质量压测；全量 Prompt OS 历史测试中的 N1/N2 身份归一化失败仍待单独收口 |
| R0-13 | 主 Agent + Prompt OS + 前端体验 + 后端平台 | Prompt OS 4.1 运行契约、商品卡两字段和暂停控制 | 用户最新反馈：N5/N6 仍会生成“画面围绕参考图/生活小任务”空话、玩偶源图三只会被强制每张都三只、文字版式缺审美；同时预备生成点击不灵敏、单图/多选缺暂停、AI 识别后商品卡应自动更新且只保留名称和补充信息 | `PROMPT_OS_VERSION=4.1.0`；N5 slot plan 新增 `subject_plan.visible_unit_count`、`composition_plan`、`style_plan`、`text_layout_theme`、`copywriting_chain`；N6 缺失或空泛 `display_prompt` 时重写为正向中文广告图导演稿；最终 prompt 出口压缩到 3500；N5 消费者上下文合并 N1 观察事实；工作台 `pause/` API 支持暂停选中商品或单张生成；商品卡只编辑名称和补充信息，识别到的颜色、款式、结构、包含物、身份锁和风格要求合并进补充信息 | `platform_app/prompt_templates_v3.py`、`platform_app/services.py`、`platform_app/views.py`、`platform_app/urls.py`、`platform_app/models.py`、`frontend/src/**` 相关组件/API/测试、Prompt OS 4.1 文档、本控制板 | `tests/test_prompt_templates_v3.py` 13 passed；N5/N6/Prompt Worker 聚焦 9 passed；`tests/test_views.py` 23 passed；前端 `App`/`workbench-v3` 52 passed；`npm --prefix frontend run build` passed；`manage.py check` passed；`git diff --check` passed；远端 migrate/seed/check、4.1 模板发布校验、五服务状态和 `/health/ready` 通过；完整 `tests/test_prompt_os.py` 仍剩 10 条既有 N1/N2/旧 worker 断言失败 | 已部署到预览；回退备份 `/opt/independent-image-platform-backups/20260803_134807-40717c4-pre-r013-promptos41-pause` | 真实付费 1+8、50 张质量压测、ERP/OSS 浏览器 smoke 和全量 Prompt OS 历史测试收口待后续任务 |
| R0-02 | 执行 Agent（主 Agent 签核） | Workbench v4 与一卡多外观 1+8 | `e98ad9f`、R0-01 API/ClusterAsset/analysis_snapshot、2026-08-01 确认边界 | 常驻导入、大卡、真实缩略图排序/跨卡移动、固定侧浮层；`asset_order`；`target_appearances`/逐槽 `appearance_ids`；generate 自动准备续跑；2026-08-02 追加紧凑配置、同屏导入、国家下拉、SKU 加载态、预备进度、技术错误脱敏和过期会话退出跳转 | 六份权威文档、`CLAUDE.md`、现有 Django/React 与测试；禁止触碰未跟踪操作流程文档 | 前端 88 passed；Vite build；Django check；diff check；远端 Compose/迁移/seed/五服务/健康/公网健康通过 | 技术预览已部署（Git `29ef250`） | 50 商品、真实员工 ERP/OSS 与新付费 1+8 仍待验收；回退备份 `/opt/independent-image-platform-backups/20260802_144804-29ef250` |
| R0-03 | GPT-5.5 执行 Agent（主 Agent 验收） | Prompt OS 3.1 营销文案与创意场景 | 3.0.0 运行模板、3.1 设计规格、五种营销转换法、六类质量基准 | N5 `creative_strategy`、N6 目标语言文案与质量记录、N7 结构化文案锁、节点温度、员工可见策略/文案；2026-08-02 追加缩略图点击预览、仅缩略图拖拽、乐观移动、短缓存和 N3 引用归一化 | 仅计划列出的 Prompt/服务/编辑器/测试/状态文件；不触碰未跟踪操作流程文档 | Targeted N3/UI passed；frontend workbench review/v3 22 passed；Vite build passed；Django check passed；media tests passed；远端 Compose/迁移/seed/五服务/健康/公网健康通过 | 已重新部署到预览（Git `6a917ac`） | 真实付费 1+8、ERP/OSS employee-browser smoke、50 商品场景和全量发布门禁未完成；回退备份 `/opt/independent-image-platform-backups/20260802_193552-6a917ac` |
| R0-04 | GPT-5.5 执行 Agent（主 Agent 验收） | Prompt OS 3.2 节点与工作台链路 | 3.2 设计、3.2 实施计划、5.5 任务书、当前 Workbench v4 预览问题 | 常驻同屏导入、一行配置、缩略图点击预览/拖拽排序、固定浮层、预备并发进度、技术错误脱敏、N1/N2 多图变量总线、N5/N6 强营销文案、generate 自动预备续接 | `frontend/src/**`、`platform_app/services.py`、`platform_app/prompt_templates_v3.py`、Prompt worker、对应测试与状态文档；禁止触碰未跟踪操作流程文档 | 目标：全量测试不低于当前基线、skipped 0；浏览器覆盖单图/多角度/多颜色/排序/合并/拆分/整理零AI/正式生成自动预备 | 设计与任务书完成，待 GPT-5.5 执行 | 真实付费、ERP/OSS smoke 与 Hermes 部署需主 Agent 签核 |
| R0-05 | GPT-5.5 执行 Agent（主 Agent 验收） | Prompt OS 3.3 生产节点设计与执行计划 | 用户最新确认、3.1 营销文案、3.2 工作台/多图链路、优秀 Style DNA 样例、公开视频文案方法 | 历史设计：导入整理零AI；预备/正式生成统一 N1–N7；一卡多图 `target_appearances`；五策略营销导演；目标语言文案逐字锁；英文 `gpt-image-2` Prompt；内部错误脱敏；5.5 `/goal` 任务书 | `docs/superpowers/specs/2026-08-02-prompt-os-3.3-production-node-design.md`、`docs/superpowers/plans/2026-08-02-prompt-os-3.3-implementation-plan.md`、`docs/project/LEADER-GOAL-PROMPT-OS-3.3.md` | 文档自检；已由 R0-08 收口 | 历史完成 | 真实付费、ERP/OSS smoke 与 Hermes 部署需主 Agent 签核 |
| R0-06 | GPT-5.5 执行 Agent（主 Agent 验收） | Prompt OS 3.4 节点与执行计划 | 3.3 设计、用户最新多图/交互/文案确认、APIMart GPT Image 2 参考图契约、公开视频文案方法 | 历史设计：每图默认一卡；卡内多图共同定义一套 1+8；N2 输出商品家族和目标外观；国家只控语言/规则；N5 五策略营销；N6 目标语言直写与英文生图 Prompt；N7 文字锁和一次重写；员工错误脱敏；5.5 任务书 | `docs/superpowers/specs/2026-08-02-prompt-os-3.4-node-execution-design.md`、`docs/superpowers/plans/2026-08-02-prompt-os-3.4-gpt55-implementation.md`、`docs/project/LEADER-GOAL-PROMPT-OS-3.4.md` | 文档自检；已由 R0-08 收口 | 历史完成 | 真实付费、ERP/OSS smoke 与 Hermes 部署需主 Agent 签核 |
| R0-07 | GPT-5.5 执行 Agent（主 Agent 验收） | Prompt OS 4.0 节点生产设计与执行计划 | 3.4 设计、用户最新确认、优秀 Style DNA 样例、公开视频文案方法、APIMart GPT Image 2 参考图契约 | 下一轮执行基线：导入整理零 AI；正式生成缺 Prompt 自动预备；一卡多图共同定义一套 1+8；N2 输出目标外观；N5 五策略购买心理；N6 目标语言文案逐字锁进英文 Prompt；N7 硬门禁和一次低风险重写；前端脉冲进度和员工错误脱敏；5.5 `/goal` 任务书 | `docs/superpowers/specs/2026-08-02-prompt-os-4.0-node-production-design.md`、`docs/superpowers/plans/2026-08-02-prompt-os-4.0-gpt55-implementation.md`、`docs/project/LEADER-GOAL-PROMPT-OS-4.0.md` | 文档自检；下一步由 5.5 按计划跑红绿测试、全量验证和签核门禁 | 设计与计划完成，待 GPT-5.5 执行 | 真实付费、ERP/OSS smoke 与 Hermes 部署需主 Agent 签核 |
| R0-08 | GPT-5.5 执行 Agent（主 Agent 验收） | Prompt OS 4.1 节点与营销生成设计 | 4.0 设计、用户最新多图/交互/文案确认、公开视频五段位文案方法、优秀 Style DNA 样例、APIMart GPT Image 2 文档、OpenAI/Shopify 生成指导 | 下一轮唯一执行基线：导入整理零 AI；正式生成缺 Prompt 自动预备；一卡多图共同定义一套 1+8；N1–N7 以节点 IO 表和变量交接线贯通；N2 输出商品家族和目标外观；每个节点模板必须有六段硬结构；N5 按 FAB、场景占有、情绪触发、拟人表达、身份表达动态策划，并输出购买任务、情绪触发、画面签名和 `copywriting_chain`；N6 目标语言直写、三候选自评并锁进英文生图 Prompt；N7 采用宽门禁，只硬阻断技术不可提交和高风险可见内容，低风险只重写当前槽一次或进入审核提示；节点失败先归一化，员工只看中文可行动错误；工作台一行配置、同屏导入、固定操作条和右侧浮层；执行索引和 5.5 任务书 | `docs/project/PROMPT-OS-4.1-EXECUTION-INDEX.md`、`docs/superpowers/specs/2026-08-02-prompt-os-4.1-node-marketing-design.md`、`docs/superpowers/plans/2026-08-02-prompt-os-4.1-gpt55-implementation.md`、`docs/project/LEADER-GOAL-PROMPT-OS-4.1.md` | 文档自检；下一步由 5.5 按计划跑红绿测试、全量验证和签核门禁 | 设计与计划已最终收口，待 GPT-5.5 执行 | 真实付费、ERP/OSS smoke 与 Hermes 部署需主 Agent 签核 |
| R0-09 | 主 Agent | 生成链路宽门禁、中文运营界面与 Image2 提交包 | 用户最新反馈：英文技术词、`copy.literal_lock`、结构化异常和过严规则影响出图；人工编辑提示词需要在提交 Image2 前转译；目标国家可见文案需要当地语言自检 | 员工可见错误统一中文；普通结构化/文案/规则问题只进入审核提示，不阻断出图；高风险词不再展示给普通员工；PromptVersion 仍保存员工可读内容，提交 Image2 时封装英文控制指令、目标语言策略和文案流畅性自检；旧队列缺 PromptVersion 时失败可重试不再炸 worker | `platform_app/services.py`、`platform_app/prompt_templates_v3.py`、前端错误/Prompt 展示组件与相关测试；不新增文档、不新增依赖 | 目标后端/前端用例、Django check、Vite build、远端 Compose build/migrate/seed/up、健康检查与公网 health 通过 | 已部署到预览；回退备份 `/opt/independent-image-platform-backups/20260803_025329-pre-r0-09` | 真实付费生成质量、50 张压力与全量历史测试收口未完成 |
| R0-10 | 主 Agent | 1+8 提示词体验、中文进度、轻规则校验与营销质量修复 | 用户最新反馈：提示词折叠、N1/N2 技术状态、国家不醒目、身份卡英文不可编辑、N7 太慢、不同商品复用空泛文案 | 9 槽提示词默认展开；员工只看中文策划/文案/身份字段；状态映射为业务中文；东南亚通用国家高亮提醒；身份字段可编辑；N7 默认快速本地检查；N5 负责中文营销策划与购买理由，N6 负责中文画面稿、本地语言文案和系统英文提交指令 | `frontend/src/components/ProductCard.tsx`、`frontend/src/components/PromptEditor.tsx`、`frontend/src/pages/ProjectGrouping.tsx`、`platform_app/services.py`、`platform_app/prompt_templates_v3.py`、聚焦测试 | 前端 Prompt/Workbench 聚焦测试通过；后端模板/Prompt worker/N7 轻校验聚焦测试通过；Django check、Vite build、远端 Compose build/migrate/seed/check、内外网 health 通过 | 已部署到预览；回退备份 `/opt/independent-image-platform-backups/20260803_034208-r010-prompt-display` | 不做严格合规模式；真实付费质量、50 张压力、ERP/OSS 浏览器 smoke 和全量历史测试改基线仍未完成 |
| P0-01 | 产品与 Prompt OS | Prompt OS v2 九节点、事实与身份锁 | 九节点规格、官网规则包 | N1 逐图观察、N2–N6 分析/编译、N7 确定性闸门、N8 修改导演、N9 失败简化；完整 2.1.0 核心提示词通过实际 system/视觉指令发送；推断台账和不可变快照 | `platform_app/services.py`、节点规格、测试 | 历史节点 smoke 已有；本轮发现新生成链路可能绕过合格 PromptVersion/N7，正在以 R0-01 重构修复 | 被 R0-01 收紧 | 新硬门禁、Worker CAS、质量基准与真实验收待完成 |
| P0-02 | 前端体验 | 可用上传入口、推断台账和结构化 Prompt 编辑 | React 工作台与 Prompt OS v2 API | 图片/文件夹/拖拽、失败项重传、事实/推断/规则阻断展示、结构化 Prompt 保存 | `frontend/src/**` | 前端 45 passed、Vite build passed；登录态 multipart 图片/WebP/TXT 上传、OSS 读回与预览 passed | 已部署 | 浏览器原生文件夹选择 smoke 待人工执行 |
| P0-03 | 后端平台 | 商品准备、9 图生成、修订与选择式导出 | Prompt OS v2 与商品资料接口 | 分文件上传结果、结构化 PromptVersion、白底门禁、Shopee VN 原图直通、人工审核后导出、历史版本保留 | Django 应用、迁移、测试、环境模板 | 后端 193 passed；迁移 0012、商品名/关系保存、PostgreSQL 行锁和准备重试 passed | 已部署 | 真实 ERP 员工账号成功登录/SKU 导入 smoke 待验收 |
| P0-04 | 平台规则/合规 | 官网主规则包与已验证站点覆盖 | Shopee/TikTok 官方来源 | Shopee/TikTok 官网主规则 fallback；Shopee VN、TW Mall、TikTok US 覆盖；全局内部基线 | seed 命令、规则登记册、测试 | 模板/规则回归 passed；线上 9 个 2.1.0 Prompt 模板发布 | 已部署 | 当前预览生产不因平台图片建议阻断 AI 出图；如未来要“官方合规模式”，再单独启用严格阻断 |
| P0-05 | QA/发布 | 真实预览发布门禁与运维收口 | Compose、Caddy、现有测试 | 同源静态部署、运行手册、验证证据；APIMart smoke 命令只输出节点状态、耗时和哈希，假 key 非零且不泄漏 | `platform_app/management/commands/smoke_apimart_nodes.py`、`docs/runbook.md`、`PROGRESS.md`、`BLOCKED.md` | 远端 Compose config/build/up/seed/health passed；HTTP/DOM smoke passed；日志无密钥明文 | 已完成（预览已部署） | HTTP 临时入口仍非正式 HTTPS 发布 |
| P0-06 | 主 Agent + 平台/QA | APIMart 真实契约与费用门禁 | APIMart 中文文档、公开测试图、精确目标模型 | 三模型的真实包络适配、结构化输出、异步任务恢复与质量基准证据 | 视觉/Prompt/图像适配器、测试、运行文档 | 历史低并发 smoke 记录保留；该记录不证明 R0-01 当前证据门禁、营销差异或新工作台已验收 | 待 R0-01 后复验 | 新真实 1+8、审核导出和分级并发待完成 |

## 当前决定

| 日期 | 决定 | 原因 | 影响 |
| --- | --- | --- | --- |
| 2026-08-03 | 运营可见图片提示词必须是正向视觉导演稿 | 字段摘要、防错清单、负向约束和“文字不遮挡/预留安全区”会让图片模型生成空泛画面或大块纯色文字区，不能承载营销设计 | `display_prompt` 写成自然段，覆盖画面内容、构图、镜头、灯光、材质、使用关系、文字版式和购买情绪；前端没有后端中文终稿时不再自造提示词；内部风险仍由 N7/提交包处理 |
| 2026-08-03 | N7 改为宽门禁，平台规则和质量问题默认不阻断出图 | 运营目标是先稳定产出图片，再由人工审核筛选；过多平台/文案/身份检查会让普通商品卡死在预备阶段 | 只有 Prompt 超长、版本过期、无可用我方参考、竞品图误入参考、价格/折扣、认证/奖项、医疗/疗效、100%/绝对承诺、站外导流、未授权 IP、危险/色情/暴力/仇恨/儿童安全等高风险才硬阻断；其他问题进入审核提示 |
| 2026-08-03 | 普通员工界面不展示英文技术词；Image2 提交前增加英文控制包和本地化自检 | 员工是中文用户，`image_role`、`evidence_refs`、`copy.literal_lock`、`price/certification/medical` 等内部词会造成误判；人工改中文提示词后仍需让 Image2 收到可执行英文指令 | 前端只显示中文状态；最终提交给 Image2 的请求包含英文生成指令、目标国家可见文案语言、引号文案逐字锁和流畅/歧义自检；中文商品名和内部备注不得直接渲染为图片文字 |
| 2026-08-03 | R0-10 只维护一份控制板并收口用户可见体验 | 用户要求不要再分散新文档；当前最影响试用的是提示词体验和预备/生成状态，而不是新增系统能力 | 未完成项写入本控制板；普通员工界面删除折叠的高级英文 Prompt；国家选择高亮提醒；规则校验默认轻量；提示词链路改为中文营销策划 → 中文画面稿/目标语言文案 → 系统英文生图指令 |
| 2026-08-03 | 商品覆盖模板按 Generation 自身槽位提交，员工不看内部提交异常 | 黄玩偶 0/9 失败发生在提交 APIMart 前：项目默认模板与商品覆盖模板不一致时，封存逻辑用项目模板反查商品槽位导致查询失败 | 提交锁依赖改为锁定当前 Generation 已绑定的 OutputSlot/OutputTemplate；用户可见失败文案改为“本张出图未成功，可直接重试”，不再提示联系管理员 |
| 2026-08-03 | N5 先发散营销场景，N6 按槽位选择功能部件或完整套装 | 餐具套装样图暴露出 fallback Prompt 把托盘/盒子当成每张营销图主角，且八图容易退回同一暖色家居 flatlay | N5 先从多种场景族和 FAB/场景占有/情绪/拟人/身份策略中选择购买理由；N6 在使用/生活方式/细节图中优先展示当前动作需要的功能部件，盒子/托盘/包装只作为辅助；白底/总览/包含物槽位才清晰展示完整套装 |
| 2026-08-02 | N7 文案锁和普通事实引用问题不再硬阻断出图 | 运营当前优先是稳定生成；`copy.literal_lock`、普通 `copy.unknown_fact_ref` 属于文案质量/引用稳定性问题，不应让商品停在预备受阻 | 两者改为 `warnings`；平台建议、主图禁字和普通规则风险也只做审核提示；文案问题由重写、少放文字或人工审核兜底 |
| 2026-08-02 | PromptEditor 显示策略、目标语言文案和最终 Prompt | 员工需要看到每张图为什么这样设计，而不是只看到一段英文生图指令 | 每槽显示中文策略标签、购买任务、目标语言图片文案、语义回译和可编辑最终 Prompt；历史无 3.1 metadata 的 Prompt 保持旧布局 |
| 2026-08-02 | Prompt OS 3.1 Task 5 节点温度进入运行快照 | N5 需要更强创意发散，N6 需要可控本地化表达，N7/N3 等规则节点需要低温稳定判断 | DeepSeek 文本节点按 N2 0.3、N3 0.2、N4 0.4、N5 1.6、N6 0.9、N7 0.2、N8 0.4、N9 0.2 调用；PromptVersion 与 Prompt OS 节点快照记录实际温度 |
| 2026-08-02 | Prompt OS 4.1 最终节点蓝图冻结 | 用户要求把每个节点真正设计清楚，后续直接交给 GPT-5.5 执行 | 4.1 设计新增绑定的 `4.0.2 GPT-5.5 执行用最终节点蓝图`：逐节点定义唯一职责、输入、输出、继续/阻断边界、变量交接线、system prompt 六段骨架和员工错误边界；4.1 计划与任务书同步指向该蓝图 |
| 2026-08-02 | Prompt OS 4.1 统一节点调用与快照形状 | 用户要求真正设计好每个节点，而不是继续靠短摘要 Prompt 或临时字段拼接 | 每个节点执行统一保存 `node/model/temperature/input_fingerprint/system_prompt_version/input/expected_schema/output/normalized_output/quality`；下游只读 `normalized_output`，原始模型输出只给管理员排障 |
| 2026-08-02 | Prompt OS 4.1 新增 GPT-5.5 执行索引 | 用户要求先把全部重要点设计好，后续切到 5.5 执行 | 新增 `docs/project/PROMPT-OS-4.1-EXECUTION-INDEX.md`，把员工路径、一卡多图、节点变量线、九节点职责、五策略文案、错误边界、工作台边界和执行文件压成一页，避免执行 Agent 在历史版本间发散 |
| 2026-08-02 | Prompt OS 4.1 补充节点失败归一化表 | 用户要求先说明各节点什么情况会失败，并避免 `image_role`、Schema、JSON、`string` 这类技术词让运营误以为图片不可识别 | 5.5 执行时必须先内部归一化模型字段/示例值；单张坏图不阻断同一卡；只有全部无商品、人工名称冲突、事实/规则硬冲突才阻断当前商品；普通员工只看中文可行动错误 |
| 2026-08-02 | Prompt OS 4.1 最终设计冻结 | 用户要求先真正设计好每个节点和计划书，后续交给 GPT-5.5 执行 | 4.1 设计新增付费生成前唯一门禁、员工错误边界和“节点模板不是职责摘要”验收；GPT-5.5 只执行 4.1 Task 0–9，不再新建 4.x 设计 |
| 2026-08-02 | Prompt OS 4.1 取代 4.0 成为下一轮 GPT-5.5 唯一执行基线 | 用户要求真正设计每个节点和计划书，并把公开视频文案方法、优秀 Style DNA、多图外观、目标语言文字锁和错误脱敏收口到一份可执行文档 | 5.5 后续优先读取 4.1 设计、计划和任务书；3.1–4.0 只作为历史推演 |
| 2026-08-02 | Prompt OS 4.1 节点提示词三次收口 | 用户要求每个节点真正设计清楚，尤其是营销文案和场景创意不能再是短职责摘要 | 节点模板必须包含六段硬结构；N5 增加 `copywriting_chain`；N6 增加三候选目标语言文案自评；5.5 执行计划和任务书同步更新 |
| 2026-08-02 | Prompt OS 4.1 文档二次收口 | 用户要求 5.5 执行前先把节点、工作台和计划书全部设计清楚 | 4.1 设计新增节点 IO 表、N1→N7 变量交接线、双路径触发、固定侧浮层、同屏导入、卡内多图多款、N5 购买任务链、N6 英文 Prompt 骨架和 N7 三类阻断；4.1 计划和任务书同步更新 |
| 2026-08-02 | 退出登录按幂等处理 | 用户点击退出时，session/token/CSRF 过期本身就说明当前会话不能继续，前端不应要求再重试一次退出 | `logoutUser()` 在 CSRF 启动或 logout POST 遇到登录跳转、401 或 403 时视为退出完成并跳 `/login/`；500 等真实服务错误仍显示失败 |
| 2026-08-02 | 商品卡是一套图生产单元，N2 区分商品家族与目标外观 | 同一卡内可能是多角度，也可能是不同颜色/规格/款式，不能硬压成单一外观，也不能要求运营维护多图关系 | N1 读全部图；N2 输出共有身份锁与 `target_appearances`；N5/N6 用 `appearance_ids` 覆盖整套 1+8；点击缩略图只预览，拖动到第一位才改变主参考 |
| 2026-08-02 | Prompt Worker 预备生成支持可配置并发 | 多商品预备不应在一个 worker 里完全串行，且现有 CAS 已能避免重复认领 | `run_prompt_worker --concurrency` 默认 16，可用 `PROMPT_WORKER_CONCURRENCY` 调整；不引入 Redis、WebSocket 或新队列 |
| 2026-08-02 | Prompt OS 3.1 使用五种动态营销策略而非固定槽位文案 | 现有 N5/N6 能安排场景和短文案，但 FAB、心理所有权、情绪、拟人和身份表达没有结构化契约，容易退回空泛模板话术 | N5 负责事实到购买心理和场景，N6 负责目标语言成品与英文生图编译，N7 负责文字锁、事实、语言和重复度；八图至少四种策略、FAB至少一张、拟人默认最多一张 |
| 2026-08-02 | Prompt OS 3.2 作为 GPT-5.5 后续唯一执行设计 | 3.1 只解决营销文案，但用户当前痛点还包括预备触发、拖拽范围、侧浮层、Prompt 逐槽加载、多图外观和错误脱敏 | 新执行以 3.2 设计/计划/任务书为准；3.1 文档只保留为历史设计输入 |
| 2026-08-02 | Prompt OS 3.3 曾作为 GPT-5.5 推荐执行基线 | 3.2 已覆盖链路，但仍需把每个节点的参数、失败边界、五策略文案、目标语言文字锁和 Style DNA 使用方式写到可执行粒度 | 已由 4.1 收口；3.3 只作为历史输入 |
| 2026-08-02 | Prompt OS 3.4 曾取代 3.3 | 用户继续确认了多图外观、预备/正式生成、目标语言锁、错误脱敏和 5.5 执行边界，3.3 仍有执行歧义 | 已由 4.1 收口；3.4 只作为历史输入 |
| 2026-08-02 | Prompt OS 4.0 曾取代 3.4 | 用户要求重新把每个节点、变量、计划书和执行边界设计清楚，避免旧文档分散导致执行偏差 | 已由 4.1 收口；4.0 只作为历史输入 |
| 2026-08-02 | 项目顶部配置压成一行，国家使用中文下拉，技术识别错误不直接暴露给运营 | 运营主要需要快速选平台、国家、比例、分辨率和提示词；模型 Schema/角色错误属于系统内部问题 | 图片/文件夹与 ERP SKU 保持同屏；SKU 按“加载 SKU”触发；商品卡更紧凑并使用 `object-contain`；预备/正式生成立即显示脉冲进度；可见错误只保留“换图/补充信息/重试”这类动作 |
| 2026-08-01 | 生成操作改为滚动常驻，导入区图片/文件夹与 ERP SKU 同屏显示 | 商品很多时不应滑回顶部；导入是高频动作，不应藏在页签或抽屉里 | 顶部只保留项目配置和批量选择；右下固定显示预备生成、正式生成和生产结果；导入后整理继续作为主按钮 |
| 2026-08-01 | N1/N2 对常见模型输出做生产归一化 | 真实模型会把商品图写成 `product/product_detail/packaging`，也会把 Schema 示例 `string` 回传 | 角色别名映射到生产枚举；N2 占位身份用 N1 商品名、类别和外观兜底；仍无有效商品才阻断 |
| 2026-07-31 | 市场/国家只限定语言与硬规则，不限定营销场景 | 固定国家场景会压制创意并导致套图同质化 | N5/N6 按商品事实、购买问题、项目/单品风格与 Style DNA 生成场景；消费者可见文案按站点语言先固定，给 `gpt-image-2` 的控制指令保持英文 |
| 2026-07-31 | Prompt OS 不接受 Schema 占位值 `string` 作为商品事实 | 真实预备生成中模型把示例 Schema 的 `string` 当输出，导致商品名、身份卡和类别被污染并阻断生成 | N1/N2 归一化统一拒绝或清空占位字符串；阻断态不再写入 AI 占位商品名；项目 snapshot 对历史污染值脱敏；PromptVersion 记录 `deepseek-v4-pro`，图片请求快照记录 `gpt-image-2` |
| 2026-07-31 | DeepSeek 文本节点默认温度调为 `1.6` | 营销策划和风格转译需要更强创意发散 | 仍限制在 APIMart `0–2` 范围内；服务器 `.env` 可覆盖 |
| 2026-07-31 | 添加商品区常驻页面，导入后整理为主按钮 | 运营导入素材是高频动作，不应先打开弹窗；整理模式是默认安全路径 | 图片/文件夹和 ERP SKU 直接在项目页操作；“导入后整理”使用主按钮，“导入并自动出图”为次按钮；商品卡放大并显示完整参考图、预备进度条和业务化 Prompt 槽位名 |
| 2026-07-31 | 整理导入零 AI，预备生成才运行 N1–N7 | 员工需要先整理商品，上传本身不应消耗识别调用；当前链路又在 N1–N7 前创建任务并使用通用兜底 Prompt | 新项目默认 Shopee/东南亚通用/1:1/1K；整理模式只保存素材；预备生成补名称、身份卡和 1+8 Prompt；正式生成缺少当前 N7 合格 PromptVersion 时先自动准备并续接，BLOCKED/FAILED 仍阻断 |
| 2026-07-31 | 项目默认采用继承，商品只保存显式覆盖 | 运营需要先批量配置再按少数商品改市场，且修改默认不能覆盖个别修改 | 生效顺序为商品覆盖 → 项目默认 → 全球通用；历史 Prompt/结果/审核永不改写 |
| 2026-07-31 | 工作台改为紧凑顶部、常驻导入区和放大商品卡 | 上传/ERP 需要高频可见入口，抽屉、弹层和列表不利于批量视觉确认 | 图片与 ERP 为页面常驻页签；卡片直接编辑名称、平台、国家、补充信息与单品风格；点击卡片在当前整行展开身份卡和九槽 Prompt |
| 2026-08-01 | 一卡全部图片共同定义一套图，工作台使用固定侧浮层 | 角度、颜色和款式图片需要共同参与生产，展开卡不能造成网格重排 | 第一张为主参考并可排序；N1 读全部图，N2 保存目标外观，N5 分配逐槽外观并校验整套覆盖，N6 选最少参考图；白底/款式总览覆盖全部外观；侧浮层固定于视口 |
| 2026-07-31 | Prompt OS v3 共用事实链并按平台拆营销链 | 2.1.0 生产模板只有约 400–800 字，未完整迁移旧工作流的身份、数量拓扑、使用关系和本地化规则 | N1–N4/N8/N9 共用；N5–N7 发布 generic/shopee/tiktok 变体；系统 Prompt 不受 3500 字限制，最终单图 Prompt 仍受限 |
| 2026-07-29 | 批量生产为主，单商品创作台为辅 | 适配内部运营和每日大量生成 | 不做首版自由画布 |
| 2026-07-29 | 一 Agent 一会话，主 Agent 统一集成 | 保留角色上下文并防止决策分散 | 以本控制板为唯一事实来源 |
| 2026-07-29 | 运营前端采用 React + TypeScript + Vite | 创作台、拖拽、队列和审核需要可组合状态与高质量交互 | Django 保留为同源 API、Session、任务与后台；不引入 Next.js 或额外后端基础设施 |
| 2026-07-29 | 竞品图只用于抽象策略，不可作为生成参考 | 防止商品身份、版权与合规风险 | 原图仅可到批准的 `gpt-5-nano-2025-08-07` 视觉观察器；Prompt OS、前端生成请求和 `gpt-image-2` 均不得传递竞品图 |
| 2026-07-30 | 生成完成后必须人工审核通过才可导出 | 运营确认只导出审核通过图片，驳回/修改要保留历史 | 未审核或要求修改的图不得进 ZIP；员工可选历史已通过版本、圈选修改或主动再生成 |
| 2026-07-29 | APIMart API 活跃异步任务硬上限为 500 | 支撑目标吞吐，但不将供应商 API 上限误作当前服务器能力 | 运行默认 `MAX_ACTIVE_GENERATIONS=50`；按 50、100、250、500 分级压测，观察 429/5xx、P95、归档成功率和数据库连接后再提高 |
| 2026-07-30 | 正式入口使用公网 IP + 端口，不使用来源 IP 白名单 | 内部员工直接登录 | 真实 ERP 密码登录前必须启用员工设备信任的 HTTPS；HTTP 只可做测试预览 |
| 2026-07-30 | ERP 登录成功的全部用户可进入平台，刘学城是唯一初始管理员 | 运营希望沿用 ERP 权限校验和个人账号查询 | 平台创建本地影子用户；管理员身份由 `PLATFORM_ADMIN_ERP_USERS` 中的 ERP 登录名决定 |
| 2026-07-30 | 默认 1+8 共 9 张、1:1、1k | 白底标准图同时作为后续营销图的一致性参考 | 第 1 槽无营销文字；槽 2–9 只在白底图完成并归档后提交 |
| 2026-07-30 | 默认套图第 1 张为标准白底产品图 | 保证商品身份和跨平台一致性 | 白底结果与原始我方图片共同作为后续营销图参考；Shopee VN 普通店使用下一行例外 |
| 2026-07-30 | Shopee VN 普通店采用“真实原图 + 白底 + 7 营销图” | 官方规则要求保留卖家真实产品图 | 槽位 1 原图复制到结果前缀且不调用生图；槽位 2 是白底门禁；槽位 3–9 等待白底 |
| 2026-07-30 | 合理推断进入显式推断台账 | 仅靠商品名和图片也要形成可用营销方案，同时不能把推断伪装成事实 | 保存 confirmed/observed/inferred、置信度、风险、证据和用途；高风险声明硬阻断 |
| 2026-07-30 | 规则按“平台官网主规则 + 已验证站点覆盖”装载 | 无需为每个国家重复维护整套规则 | 未配置站点复用平台官网主规则并标记 fallback；当前预览生产不因平台图片建议阻断，官方合规模式另行开关 |
| 2026-07-30 | 文字按站点默认语言生成并允许员工修改 | 同时满足本地化和简化操作 | 主图无营销文字；其他图文字只能来自已证实商品事实 |
| 2026-07-29 | 闲时允许用户临时借用并发 | 在全局上限内提高资源利用率 | 基础每人 10 个活跃任务；有其他用户排队时恢复公平轮转 |
| 2026-07-29 | 竞品版式结构可作为 Style DNA 借鉴 | 满足同等商业视觉策略需求 | 可复用画面层级、版式、色彩、光线和场景密度；竞品原图仅发送给 `gpt-5-nano-2025-08-07` 观察器，不得传给生成、文本、Prompt 或导出链路，也不得复制商标、包装、人物、原文案及逐像素页面 |
| 2026-07-30 | 上传与 ERP 是并列入口，每次都选自动出图或导入后整理 | 覆盖一张图一个商品和多图合并两类实际工作 | 两种模式共用识别、Prompt、1+8、结果、修改和本地导出 |
| 2026-07-30 | 导出按员工选中的审核通过版本、商品名称与 SKU 交付 | 员工需要默认全选又能排除不满意图 | 默认最新通过版本；可选历史通过版本；ZIP 按“商品名称__SKU/01–09 槽位”组织 |
| 2026-07-30 | 拖入目标集群即完成图片分配 | 运营希望批量整理时不被逐次弹窗打断，且多图不一定只是角度图 | 默认一图一集群；放下即合并并支持撤销。集群单独标记“同商品参考”或“多色/多款组合”，一个集群只产出一套图 |
| 2026-07-30 | ERP SKU 与图片/文件夹是并列一级入口 | 两种来源最终都要完成相同的商品确认、Prompt 和生图流程 | ERP 侧只消费 SKU、`productName` 与 `pic`；销售、库存、成本等字段不进入平台或 Prompt |
| 2026-07-30 | 文件夹 TXT 与 ERP 入口文本框都只提供“种子风格提示词” | 一个文件夹通常对应同一店铺风格，完整生产 Prompt 应由各商品节点生成 | 有内容时默认作为项目级风格；为空时由营销 Prompt 节点生成建议；每个商品生成独立可编辑 Prompt，仍不得虚构事实 |
| 2026-07-30 | 导出 ZIP 只下载到员工本地 | 员工需要批量交付文件，但服务器和 OSS 不需要重复长期保存压缩包 | 临时生成所选成功版本 ZIP 后由浏览器保存；OSS 永久保留原图、结果和历史 |
| 2026-07-30 | 商品资料服务改为 ERP 登录用户自己的 Token 查询 | 运营希望登录和后续查询都按 ERP 账号权限校验 | 登录时调用 `ERP_LOGIN_URL`；所有 ERP 登录成功用户可进入平台，平台只创建影子用户绑定项目归属。SKU 查询使用服务端 session 中的当前用户 Token，仅传 `skuList`，只消费 SKU、`productName`、`pic` |
| 2026-07-30 | ERP 与 SKU 商品资料主机使用 `103.198.125.2` | 现有日更和服务器 smoke 均证明该主机可达 | 服务器 `.env` 已切到 `103.198.125.2:16777` |
| 2026-07-29 | Prompt OS 固定使用 APIMart 的 DeepSeek V4 Pro、GPT-5 Nano 视觉观察器与 GPT Image 2 | 让自动视觉理解可用，同时隔离竞品素材与生成链路 | `gpt-5-nano-2025-08-07` 只将我方源图、竞品图或生成候选图变为 Schema 校验的视觉事实包；`deepseek-v4-pro` 处理所有文本语义节点和结构化 Prompt；`gpt-image-2` 只生成/修订图片。竞品原图只到视觉观察器，绝不进入 DeepSeek、GPT Image 2、生产 Prompt、生成任务或导出。P0 必须以 APIMart 中文文档和账户验证三个精确模型 ID，失败时阻断而不降级 |
| 2026-07-29 | APIMart 视觉观察器首次真实契约成功，不等于质量通过 | 区分“接口可调用”与“商品视觉事实可靠” | 使用公开测试图的两次最小付费调用确认 `gpt-5-nano-2025-08-07`、`input_image` 和严格 JSON 路径可用；下一步须以带人工标注的商品基准集衡量事实准确率，未通过前不得作为自动审核结论 |
| 2026-07-30 | APIMart 基础节点已在服务器真实打通 | 本地与服务器三节点均已完成最小真实 smoke，且真实 1+8 项目 9/9 完成 | `deepseek-v4-pro` 走非流式 Chat Completions；`gpt-5-nano-2025-08-07` 走 Responses，文本从 `output[].content[].text` 提取；`gpt-image-2` 先上传参考图，再用 URL 字符串数组 `image_urls` 提交生成；下一步只做质量基准与分级并发压测 |
| 2026-07-30 | 当前改造按 Tasks 1–7 顺序执行 | 核心 Django 服务文件存在共享写入点，盲目并行会冲突 | 后端 Tasks 1–4 串行；API 冻结后前端 Task 5 可并行；规则和 QA 不改共享实现 |
| 2026-07-30 | Prompt OS 核心提示词发布为 2.1.0 完整版 | 2.0.0 种子错误地只保存一句职责摘要，且 DeepSeek system 角色仍使用通用句 | N1–N9 发布完整节点角色、事实边界、营销方法、硬规则和 JSON 输出约束；旧版本保留但退役；3500 字符限制仅用于最终单图 Prompt |
| 2026-07-31 | 商品名称允许为空且不显示系统占位文案 | 上传商品通常只有图片，员工不应先删除“名称待确认”才能编辑 | 界面只用 Placeholder 提示 AI 会识别；ERP/人工名称优先；图片不足以建立商品身份时只阻断当前商品 |
| 2026-07-30 | N5 兼容真实 APIMart 包络并只修复一次 Schema | DeepSeek 实测会返回 `plans`、`slot_plans` 或 `slots` 等不同字段 | 按槽位名/编号归一化；缺槽时追加一次固定 Schema 修复，仍不合格才失败，不循环付费 |
| 2026-07-29 | APIMart 的真实账号包络优先于文档示例 | 同一供应商不同模型端点的外层 JSON 已出现不一致，不能依赖单一伪造包络 | `deepseek-v4-pro` 账户实测为顶层 OpenAI Chat Completions 包络，非文档示例的 `code/data`；适配器必须按端点/模型显式解析并以真实 fixture 回归。GPT Image 2 已实测 `submitted → processing → completed`、结果 URL 与费用字段，完成后必须立即受限下载归档；`cancelled` 视为终态 |
| 2026-08-02 | 预备生成与图片生成并发调度上线 | 多商品点击预备/正式生成后，运营应同时看到多个商品进入队列和加载态，而不是误以为只有第一张在工作 | N1/N2 对真实模型短 JSON、0–1 评分和不完整外观分配做归一化；Prompt Worker 默认 `16` 并发，Generation Worker 默认 `32` 线程；`MAX_ACTIVE_GENERATIONS=50` 控制真实同时生图，`GENERATION_USER_ACTIVE_SOFT_LIMIT=10` 做用户公平借用 |
| 2026-08-02 | VN 等非中文国家禁止中文内部变量进入图片可见文字 | 线上样图在越南站把中文商品名渲染进图片标题，且参考图复刻过重 | 最终 GPT Image 2 Prompt 只允许 N6 锁定的目标语言文案作为可见文字；非中文市场会清理中文商品名/事实变量；参考图只锁商品身份和包含物，营销图强制新场景、新机位和新构图 |
| 2026-08-02 | 营销 Agent 必须驱动套图文案，身份锁只防换货不限制创意 | 玩偶样图暴露出 N5/N6 fallback 写死包装/居家场景、空文案和非目标图片污染商品事实的问题 | N3 confirmed points 改用商品名、身份事实和目标图观察事实；N5 fallback 删除无证据包装图并按 FAB/场景代入/情绪/拟人/身份表达生成购买任务；N6 fallback 生成目标语言可见文案；最终 `gpt-image-2` Prompt 以正向描述为主，不再堆叠负向数量示例 |

## 阻塞与风险

| 项目 | 影响 | Owner | 处理条件 |
| --- | --- | --- | --- |
| R0-01 已部署但尚未完成运营质量验收 | 代码门禁已上线，但不能据此宣称新 Prompt 的营销质量已达标 | 主 Agent + Prompt OS | 运营使用同一商品完成预备生成与新一轮真实 1+8，记录可用性和重复场景问题 |
| Shopee/TikTok 并非所有国家都有独立覆盖 | 只能声明平台官网主规则 fallback，不能宣称该国家完整自动合规 | 平台规则/合规 | 有新官方证据时增加站点覆盖；无资料时继续复用平台官网主规则 |
| TikTok Shop US 官方禁止数字渲染 | AI 生成结果不能宣称为其官方合规商品图 | 平台规则/合规 + 产品 | 当前预览生产只做审核提示，不阻断出图；若未来启用官方合规模式，再按独立开关硬阻断 |
| ERP 员工浏览器登录、SKU 与 OSS 完整链路未自动验证 | 商品查询和 ERP 图片下载已验证，但还不能宣称所有员工账号与 OSS 归档均已验收 | 后端平台 + QA/发布 | 用浏览器登录一个真实 ERP 员工账号并导入 1 个真实 SKU，确认预览与 OSS 对象 |
| 上传请求已在服务端成功但客户端丢失响应 | 浏览器无法判断首个分片是否已落库，人工重试同一文件可能重复创建商品 | 后端平台 + QA/发布 | 正式断网恢复前增加上传会话/文件幂等键；当前阶段只承诺正常响应及已知部分成功后的失败项重传 |
| 动态公平并发已启用但尚未压到 500 | 当前预览默认 `MAX_ACTIVE_GENERATIONS=50`，不是 500 满配 | 后端平台 + QA/发布 | 观察 429/5xx、P95、归档成功率和数据库连接后再提升到 100/250/500 |
| HTTP 临时入口 | 不能承载真实 ERP 密码和 100 名员工素材 | QA/发布 | 公网 IP + 端口启用员工设备信任的 HTTPS 与账号安全；不使用来源 IP 白名单 |
| 本机无 Docker CLI | 本地不能复现 Compose 构建 | QA/发布 | 服务器 Docker Compose 已作为当前发布验证环境；本地仍只跑单元/构建测试 |
| APIMart 账户模型目录、兼容参数或响应封装与上游模型资料不同 | 不能依据原厂资料直接开发或发布 | 后端平台 + QA/发布 | 以 APIMart 中文文档及受限账户的精确模型 ID、图片输入、输出 Schema、错误/限流契约测试为准 |

## 更新协议

每次交接只更新相关任务行，并附：owner、目标、输入/输出契约、文件边界、验证命令/结果、状态、风险和下一个 owner。直接聊天得到的接口或需求决定，必须在同一工作回合回写到本文件或相应规格后才生效。跨 Agent 不得并行修改同一文件；只有主 Agent 可以合并任务或签核发布。
