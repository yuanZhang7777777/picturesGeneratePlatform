# Dual-Speed AI Product Image Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有技术 MVP 改造成可通过“自动出图 / 导入后整理”两种方式，为每个上传或 ERP 商品稳定生成 1 张白底图和 8 张营销图，并支持人工审核、单张修改、历史版本和本地批量导出。

**Architecture:** 复用现有 Django 领域模型、APIMart 客户端、OSS、ERP 登录和 React 工作台；只补商品准备状态、9 槽 Prompt、按商品生成、人工审核门禁和选择式导出。Prompt Worker 异步完成图片观察与 DeepSeek Prompt，Generation Worker 保持白底图先行并把白底结果加入后续 8 图参考。

**Tech Stack:** Django 5.2、PostgreSQL/SQLite tests、React 19、TypeScript、Vite、TanStack Query、dnd-kit、Caddy、Docker Compose、私有 OSS、APIMart。

## Global Constraints

- 唯一产品规格：`docs/project/REQUIREMENTS-BOUNDARY-CONFIRMATION.md`；唯一设计：`docs/superpowers/specs/2026-07-30-dual-speed-product-platform-design.md`。
- 不新增 Redis、Celery、消息队列、WebSocket、Redux、Zustand、Next.js、微服务或外部通知。
- 竞品原图只能进入 GPT-5 Nano 观察节点，绝不进入 DeepSeek、GPT Image 2 或生成参考数组。
- 普通员工只看自己的项目；浏览器只持有 Django Session Cookie。
- Secret 只在受限 `.env`；不打印、不提交、不写入 Prompt、日志、数据库业务字段或导出包。
- 基线为后端 160 tests、前端 35 tests、skipped 0；测试数只可增加。

---

### Task 1: 冻结 9 槽模板和商品准备状态

**Files:**
- Modify: `platform_app/models.py`
- Create: `platform_app/migrations/0010_dual_speed_preparation.py`
- Modify: `platform_app/management/commands/seed_platform_templates.py`
- Modify: `image_platform/settings.py`
- Test: `tests/test_models.py`
- Test: `tests/test_template_seed.py`

**Interfaces:**
- Produces: `Batch.last_import_mode: "auto" | "organize"`；`Cluster.relation_type`、`preparation_status`、`preparation_error`、`analysis_snapshot`、`auto_generate`；`PromptVersion.output_slot`.

- [ ] 写失败测试：新全局模板按顺序产生 9 槽，槽 1 为白底图；旧模板和历史 Generation 不改写；新字段默认 `organize / single_product / pending / false`。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest -o addopts='' -q tests/test_models.py tests/test_template_seed.py`，确认失败来自缺字段/9 槽。
- [ ] 添加最小字段与约束；发布新的 global 9-slot 模板版本。已有生成历史只读，新项目和无生成项目选新模板。
- [ ] 增加 `GENERATION_QUOTAS_ENABLED=False`；关闭每日业务额度，但保留供应商活跃任务上限 500。
- [ ] 重跑聚焦测试与 `manage.py makemigrations --check --dry-run`，均通过后提交 `feat: add dual-speed nine-slot domain state`。

### Task 2: 打通异步商品识别与 9 槽 Prompt

**Files:**
- Modify: `platform_app/services.py`
- Modify: `platform_app/management/commands/run_prompt_worker.py`
- Modify: `platform_app/views.py`
- Modify: `platform_app/urls.py`
- Modify: `image_platform/settings.py`
- Modify: `.env.example`
- Test: `tests/test_prompt_os.py`
- Test: `tests/test_apimart_client.py`
- Test: `tests/test_upload_clusters.py`
- Test: `tests/test_sku_import.py`

**Interfaces:**
- Produces: `request_cluster_preparation(cluster, *, auto_generate)`；`process_prompt_once()`；`regenerate_cluster_prompts(cluster, user)`。
- API: 上传与 SKU import 接受 `mode=auto|organize`；`POST /api/clusters/<id>/prompts/regenerate/`。

- [ ] 写失败测试：上传和 ERP 均创建待准备商品；GPT-5 Nano 严格 JSON 填入名称/事实/身份锁；DeepSeek 为 9 槽创建 PromptVersion；低置信度只阻断当前商品；合并图片重新准备。
- [ ] 写 APIMart payload 测试：Chat Completions 包含 `temperature=1.2`，配置只允许 `0..2`；JSON 解析失败使用同输入修复一次，第二次失败标记该商品 failed。
- [ ] 运行上述四个测试文件，确认红灯。
- [ ] 将 Prompt Worker 占位循环改为每次原子领取一个 `pending` Cluster；不要新建通用工作流引擎。
- [ ] 在 `settings.py` 增加 `APIMART_PROMPT_TEMPERATURE`，默认 `1.2`；将严格 JSON 结果保存到 `analysis_snapshot` 和每槽 PromptVersion。
- [ ] 重跑聚焦测试；再跑 Prompt Worker `--once`，空队列必须输出 `processed=0`；提交 `feat: prepare products with vision and prompt workers`。

### Task 3: 按商品生成并落实白底图参考链

**Files:**
- Modify: `platform_app/services.py`
- Modify: `platform_app/views.py`
- Modify: `platform_app/urls.py`
- Test: `tests/test_generation_queue.py`
- Test: `tests/test_views.py`

**Interfaces:**
- Produces: `ensure_cluster_generations(cluster, user, *, slot_orders=None, force_new=False)`；`regenerate_generation(source, user, prompt_version=None)`.
- API: `POST /api/projects/<id>/generate/ {"cluster_ids":[],"slot_orders":[]}`；`POST /api/generations/<id>/regenerate/`.

- [ ] 写失败测试：同一请求重发不重复建任务；项目生成后仍可导入并生成新商品；成功图可主动生成新 attempt；失败批量重试不碰成功槽。
- [ ] 写顺序测试：每商品先提交槽 1；槽 1 未完成时槽 2–9 不调用供应商；槽 1 完成归档后，其 ResultAsset 路径加入槽 2–9 reference snapshot。
- [ ] 写暂停测试：只取消 queued；submitted/processing 继续轮询并归档；`submit_unknown` 不自动 POST。
- [ ] 运行 `tests/test_generation_queue.py tests/test_views.py`，确认红灯。
- [ ] 用 `(cluster, slot, attempt)` 唯一约束实现幂等，不新增 GenerationRequest；弃用 Batch 单次 `confirmed_generation_key` 门禁但保留旧字段兼容历史。
- [ ] 对明确 429/5xx 使用有限重试；未知受理状态保持人工处置。重跑聚焦测试后提交 `feat: generate nine-slot product sets safely`。

### Task 4: 保留审核门槛并实现修改与选择式导出

**Files:**
- Modify: `platform_app/services.py`
- Modify: `platform_app/views.py`
- Modify: `platform_app/urls.py`
- Test: `tests/test_review_delivery.py`

**Interfaces:**
- Produces: `request_generation_revision(generation, user, *, issue_tags, description, annotations)`。
- API: `POST /api/generations/<id>/revise/`；`POST /api/projects/<id>/export/ {"generation_ids":[]}` 返回 ZIP。

- [ ] 写失败测试：completed 结果未 accepted 不可导出；默认最新审核通过版本；显式 generation IDs 只能选择已通过旧版本；他人项目 ID 返回 404。
- [ ] 写 ZIP 测试：`项目名_日期/商品名__SKU/01..09_槽位.原扩展名` 和 UTF-8 `导出清单.csv`；ZIP 不调用 storage.save。
- [ ] 写圈选修改测试：批注坐标校验不减弱，修改创建新 PromptVersion/Generation，旧结果不覆盖。
- [ ] 运行 `tests/test_review_delivery.py` 确认红灯；复用现有批注逻辑，不删除旧 Review 表和历史 API。
- [ ] 实现 revision/export 接口，保留 accepted 导出过滤并移除 OSS ZIP 持久化；提交 `feat: export selected approved results`。

### Task 5: 重做 React 双速工作区与结果页

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/layout.tsx`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/pages/Dashboard.tsx`
- Replace: `frontend/src/pages/ProjectGrouping.tsx`
- Replace: `frontend/src/pages/Studio.tsx`
- Replace: `frontend/src/pages/Production.tsx`
- Remove route only: `frontend/src/pages/Review.tsx`
- Create: `frontend/src/components/ImportPanel.tsx`
- Create: `frontend/src/components/ProductCard.tsx`
- Create: `frontend/src/components/PromptEditor.tsx`
- Create: `frontend/src/components/ResultGrid.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/__tests__/App.test.tsx`
- Test: `frontend/src/__tests__/api.test.ts`

**Interfaces:**
- Consumes: Tasks 2–4 JSON contracts.
- Produces: `/projects/:id` 统一工作区；`/projects/:id/results` 生产与结果页；不再导航到 `/review`.

- [ ] 写失败测试：两个导入按钮；自动模式提交后直接准备/生成；整理模式停在商品卡；拖拽合并可撤销；生成按钮显示商品/图片数。
- [ ] 写结果测试：9 槽、默认全选最新审核通过图、取消选择、历史版本、再生成、圈选修改、选择式 ZIP。
- [ ] 运行 `npm --prefix frontend test` 确认新增测试失败。
- [ ] 复用 TanStack Query、dnd-kit 和现有 API 错误处理；删除预检按钮、确认弹窗、审核导航与“通过”动作，不增加状态库。
- [ ] 结果页保持 3 秒/15 秒轮询规则；桌面 Chrome/Edge 在 1280px 宽无横向页面溢出。
- [ ] 运行前端测试和构建，提交 `feat: deliver dual-speed product workspace`。

### Task 6: 管理、规则与运行保护

**Files:**
- Modify: `platform_app/admin.py`
- Modify: `platform_app/services.py`
- Modify: `tests/test_auth_permissions.py`
- Modify: `tests/test_template_seed.py`
- Modify: `docs/research/2026-07-29-platform-image-rule-source-register.md`

- [ ] 写失败测试：普通员工看不到模型/队列配置；刘学城管理员可管理 Prompt 模板和发布规则；未知市场使用 global 9-slot 模板。
- [ ] Shopee/TikTok 未核实规则保持 draft；只发布有官方来源、站点、日期和版本的规则。不得把调研摘要当自动合规承诺。
- [ ] 保持用户项目隔离；管理员永久删除敏感项目时不新增员工可见审计流程。
- [ ] 运行权限与模板测试并提交 `feat: align admin controls and market templates`。

### Task 7: 全量回归、真实节点 smoke 与香港服务器部署

**Files:**
- Create: `platform_app/management/commands/smoke_apimart_nodes.py`
- Modify: `tests/test_apimart_client.py`
- Modify: `docs/runbook.md`
- Modify: `docs/project/STATUS.md`
- Modify: `PROGRESS.md`
- Modify: `BLOCKED.md`

- [ ] 管理命令内部用 Pillow 生成无文字测试商品图；每个模型只调用一次，只输出节点、HTTP/任务状态、耗时和结果哈希，不输出密钥、完整响应或签名 URL。
- [ ] 反向验证：假 Key 运行必须非零退出且日志无 Key；恢复受限环境后真实三节点 smoke 必须成功。
- [ ] 跑全量：后端测试数 `>=160`、前端 `>=35`、skipped `0`，Django check、迁移检查、前端 build、`git diff --check` 全部通过。
- [ ] 部署前声明 Hermes `project_key=global mode=deploy target=/opt/independent-image-platform locks=global.lock`；非阻塞取得 `.codex_locks/global.lock`，备份现有 Compose/.env/数据库元数据后部署。
- [ ] 服务器依次完成迁移、seed、health、ERP 登录、1 个 SKU、OSS 归档、自动 1+8、圈选修改和本地 ZIP smoke。真实并发先保持 2。
- [ ] 公网 IP + 端口在员工设备未信任 HTTPS 证书前只保留测试账号与非敏感素材；不得宣称已可给 100 人正式使用。
- [ ] 更新状态与运行手册，提交 `docs: record dual-speed production verification`。

## Final Verification

```powershell
$env:USE_SQLITE_FOR_TESTS='1'
$env:APIMART_FAKE_MODE='1'
.\.venv\Scripts\python.exe -m pytest -o addopts='' -q
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

完成标准：上传和 ERP 两条入口都能选择自动/整理模式；每个正常商品产生 1+8 共 9 张，只有人工审核通过结果可以选择式下载，历史版本与对象权限不退化；服务器真实三模型、OSS、ERP 和完整商品 smoke 有脱敏证据。
