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
