# Task 2 交付报告：审核、修改版本与安全交付 API

## 交付范围

- 新增 `ReviewFeedback`、`ReviewAnnotation` 与迁移 `0004_review_delivery.py`。
- `Generation.review_status` 增加 `changes_requested`，保留旧 `rejected` 值用于兼容。
- 新增人工审核服务：
  - 仅 `completed` generation 可审核。
  - `accept` 只更新审核状态，不创建新 attempt。
  - `changes_requested` 保存不可变反馈/相对坐标批注，并创建同一 cluster/slot 的 `attempt + 1`。
  - 新 attempt 使用新的 `PromptVersion`，保留原 Prompt、商品事实、身份锁、产品参考图、模板和规则快照。
  - 圈选坐标、颜色和覆盖层不进入 Prompt、`reference_snapshot` 或 Prompt 输入快照。
  - 技术重试仍只接受 `failed`，复用原 `PromptVersion`；修改请求不走技术重试。
- 新增同源 JSON API：
  - `GET /api/csrf/`
  - `GET /api/workspace/snapshot/`
  - `POST /api/projects/`
  - `GET /api/projects/<id>/snapshot/`
  - `POST /api/projects/<id>/assets/`
  - `POST /api/projects/<id>/preflight/`
  - `POST /api/projects/<id>/confirm/`
  - `GET /api/projects/<id>/export/`
  - `GET /api/assets/<id>/media/`
  - `GET /api/results/<id>/media/`
  - `POST /api/generations/<id>/review/`
- 保留全部旧 batch URL、分组、优化、确认和技术重试路由。
- 管理后台可查看审核反馈和批注，字段只读且禁止后台新增/删除。

## 安全与数据边界

- 工作区和项目快照按 owner 隔离，平台管理员可查看全部项目。
- 快照不返回额度、供应商任务/负载、原始 Prompt、供应商 URL 或 raw JSON。
- 图片 URL 只指向对象权限保护的站内媒体 API。
- 媒体路径必须位于 `MEDIA_ROOT` 下且匹配对象自己的
  `originals/<batch>` 或 `results/<batch>/<cluster>/<slot>/<attempt>` 前缀；
  绝对路径、反斜杠、`..`、空字节、越界解析和不存在文件均返回 404。
- ZIP 只读取每个 cluster/slot 最新 attempt；仅 `completed + accepted` 可进入导出。
- ZIP 路径只使用 project/cluster/slot/attempt/result 安全 ID，不使用用户原文件名。
- accept、changes requested、export 均写 `AuditEvent`。
- 请求修改或技术重试会把项目恢复为 `queued`，避免前端把仍有新任务的项目误判为终态。

## TDD 证据

先新增 `tests/test_review_delivery.py`，首次运行：

```text
7 failed
```

失败原因为新增路由和审核模型尚不存在。实现后目标测试转绿；随后增加终态轮询与 GET-only
合同断言并再次确认红灯，再做最小修复。

最终验证：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_review_delivery.py -q
# 7 passed

.\.venv\Scripts\python.exe -m pytest -q
# 53 passed
```

覆盖 owner/admin/other-user 隔离、CSRF、已发布模板/规则、快照脱敏、受控媒体、
路径穿越、accept/export、最新 accepted attempt、规范化批注、修改版本快照隔离、
不可变审核记录，以及技术失败重试与质量修改分离。

## 文件

- `platform_app/models.py`
- `platform_app/services.py`
- `platform_app/views.py`
- `platform_app/urls.py`
- `platform_app/admin.py`
- `platform_app/migrations/0004_review_delivery.py`
- `tests/test_review_delivery.py`

## 风险与未完成项

- 未调用真实供应商、未部署、未修改前端或 Docker。
- 默认 PostgreSQL 环境在生成迁移时连接超时；迁移使用
  `USE_SQLITE_FOR_TESTS=1` 生成，并由 pytest 的迁移数据库完整验证。
- README、runbook、STATUS 与架构规格需要主 Agent 在集成交付时同步；
  本任务按文件边界未修改这些权威文档。

## Integration fix round 1

- 前端合并与 Brief 保存需要提交 `expected_version`，workspace/project snapshot 的每个
  SKU 现返回对应 `Cluster.version`。
- 旧 batch snapshot 原本已返回 cluster version，保持不变。
- 严格 TDD：先新增 snapshot 断言并确认因 `KeyError: 'version'` 红灯，再在视图层复用
  现有 serializer 补字段；未修改模型、服务、路由或前端。
- 验证：Task 2 测试 7 项通过；全量 pytest 53 项通过。

## Review fix round 2

- 新增 `DailyGenerationUsage` 及 `0005_daily_generation_usage.py`：
  - 组织和用户每日各一条持久用量行。
  - `select_for_update` 按 `batch → cluster → org usage → user usage` 固定顺序锁定。
  - confirm、技术 retry、质量 revision 共用原子额度预留；超额事务整体回滚。
- follow-up attempt 共用 cluster/slot 门禁；已有更新或运行中 attempt 时返回可理解 400，
  不再依赖 `MAX + 1` 碰唯一约束后产生 500。
- `changes_requested` 要求非空描述或有效 issue tag；circle 必须完整位于相对坐标画布内；
  审核反馈/批注禁止 instance update/delete，后台继续只读。
- revision 以不可变 `PromptVersion.prompt_text` 及其 input/source 自有商品参考快照为权威；
  只有 legacy null PromptVersion 才回退 Generation 字段。
- 项目和旧 batch snapshot 只返回受控失败文案，内部 `failure_reason` 仍留在受限后台。
- 媒体守卫拒绝其他 batch/result 前缀、prefix symlink 和 resolved 越界路径。
- 导出改为 `TemporaryFile + FileResponse` 流式响应；单结果上限 25 MiB，总量上限
  500 MiB，超限返回 400，不再使用 `BytesIO.getvalue()` 复制完整 ZIP。
- 严格 TDD：新增合同首次运行 8 项失败，分别命中上述缺口；实现后 Task 2 测试
  13 项通过，全量 pytest 59 项通过，Django check 无问题，迁移无漂移。
