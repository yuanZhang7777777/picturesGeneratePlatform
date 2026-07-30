你是执行者，这份任务书是唯一任务来源；中途没人可问，拿不准的写进 `BLOCKED.md`，跳过后继续不受影响的任务。断线或换会话先读 `PROGRESS.md` 接着做，每完成一项立刻更新，不得重做。目标是把现有技术 MVP 交付成内部员工可直接使用的双速 AI 商品出图平台：上传或 ERP SKU 后，可自动或整理后为每个商品生成 1 张白底图 + 8 张营销图。冲突时按“商品与账号安全 > 不重复付费 > 正确结果 > 完整功能 > 速度”取舍。“只允许/不许”是硬边界；建议可替换，但原因写进 `PROGRESS.md`。

## 我替领导拍的板

- 首次导入也显示两个按钮，`导入并自动出图`为主要操作；项目只记住上次选择，不用隐藏开关｜猜错会影响误操作率。
- DeepSeek 温度默认 `1.2`，范围 `0–2`；严格 JSON 只修复重试一次｜再高会增加结构失败。
- 不设置正式审核或 AI 质检；成功图默认可导出，员工可取消、圈选修改或再生成版本｜猜错会改变导出门槛。
- 不设每日 2,000 张上限；真实并发仍从 2 开始压测，500 只是供应商硬上限｜猜错会影响成本与容量。
- APIMart 暂停时允许整理，不自动换模型｜猜错会改变供应商边界。
- 产品/文档子 Agent 优先用 5.5；不可用时继承当前模型并记录，不阻断。

## 界限

只允许修改 `platform_app/**`、`image_platform/settings.py`、`frontend/**`、`tests/**`、`.env.example`、`docker/**`、`docker-compose.yml`、`README.md`、`CLAUDE.md`、`PROGRESS.md`、`BLOCKED.md`、`docs/**`。禁止写入或打印 Secret，禁止删除历史生成/Prompt/批注，禁止改 ERP 契约和竞品隔离边界。不得修改、删除、skip/todo 现有测试来过关，不得放宽断言、mock 被测对象、加 `|| true`、改验收命令或引入 Redis/Celery/WebSocket/新状态库。破坏性数据、权限和生产配置操作先写 `BLOCKED.md`；Hermes 写操作必须用 `hermes-remote` 和 `global.lock`。

## 现状与任务 0

2026-07-30 实测：后端 160 passed、前端 35 passed、skipped 0；Django check 无异常、无待生成迁移、Vite build 成功。香港服务器 SSH、项目目录及 APIMart DNS/TLS/HTTP 已通；服务器真实三模型 smoke 未跑。现有实现仍是 8 槽、生成前预检、accepted 后导出，Prompt Worker 是占位循环。

先读 `CLAUDE.md`、`docs/project/REQUIREMENTS-BOUNDARY-CONFIRMATION.md`、`docs/superpowers/specs/2026-07-30-dual-speed-product-platform-design.md`、`docs/superpowers/plans/2026-07-30-dual-speed-platform-implementation.md`。运行计划末尾全量命令；数字不符，把证据放 `BLOCKED.md` 首行并只做不受影响部分。核对后在 `PROGRESS.md` 用不超过 10 行写目标、顺序、最大风险。

## 任务

严格按实施计划 Task 1→7 执行，每项遵循“失败测试→最小实现→聚焦测试→提交”。主 Agent 负责模型/迁移/API/部署签核；后端 Agent 顺序完成 Task 1–4；API 契约冻结后前端 Agent可只改 `frontend/**` 并行 Task 5；规则 Agent 只改来源登记与规则草稿；QA Agent只读验证，最后由主 Agent合并。Agent 之间只通过 `docs/project/STATUS.md` 和结构化交接单通信，不自由改对方文件。每项交接必须写：改动、命令与实际结果、风险、未完成；验证缺失不得合并。

关键反向验证：DeepSeek 返回坏 JSON时第一次修复、第二次只阻断该商品；白底图失败时槽 2–9 的 APIMart 提交次数必须为 0；假 Key 的真实 smoke 必须非零退出且输出不含 Key；恢复后再跑绿。相同验收连败 3 次就换下一项并记录；结果比基线差就回滚该项。

## 规矩

保留现有可复用模型、APIMart 客户端、OSS、ERP Session 和批注逻辑；不重建架构。每次提交只含一个 Task，提交前跑聚焦测试。部署前 `git status --short`，不得覆盖用户改动；生产先备份，再非阻塞取得 `global.lock`。真实付费 smoke 每模型一次，日志只留状态、耗时、哈希。公网 IP+端口未启用员工设备信任的 HTTPS 前，只能用测试账号和非敏感素材。

## 完成条件

1. 上传与 ERP 两入口都能选择自动/整理模式；正常商品得到 1+8 共 9 张，白底图失败时后 8 张零提交，成功图无需审核即可按选中版本下载本地 ZIP。
2. 后端测试 `>=160`、前端 `>=35`、skipped `0`，全量检查全绿；服务器真实 DeepSeek、GPT-5 Nano、GPT Image 2、ERP、OSS 和完整商品 smoke 均有脱敏输出，Secret 泄漏为 0。

每条完成条件都要在对话贴实际命令输出和反向验证红→绿证据，只说完成不算。`BLOCKED.md` 随交付提交，空也写“无”。最多执行 3 轮全量修复；满轮即停，如实汇报卡点和剩余工作。
