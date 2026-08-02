# GPT-5.5 执行任务书：Prompt OS 3.3

在执行 agent 那边输入 `/goal `，粘贴下面整段执行。

```text
你是 E:\Project\picturesGenerate 的 GPT-5.5 执行 Agent。唯一任务来源是 docs/superpowers/specs/2026-08-02-prompt-os-3.3-production-node-design.md 与 docs/superpowers/plans/2026-08-02-prompt-os-3.3-implementation-plan.md；旧 3.1/3.2 只当历史输入，不得混用。断点写 PROGRESS.md，真实阻塞写 BLOCKED.md；空也写“无”。不得读取、修改、删除或提交 docs/project/用户操作流程以及相关触发.md。

这活为什么干：当前系统能导入和显示商品，但 Prompt 节点、商品多图、目标语言文案、逐槽进度和正式生成门禁还没真正稳定串起来。目标是让“导入后整理零 AI → 预备生成 N1–N7 → 正式生成 1+8 → 人工审核导出”能稳定生产好看的跨境商品图。

我替领导拍的板：
1. 默认 Shopee 虾皮 / 东南亚通用 / 1:1 / 1K。
2. 一个商品卡只出一套 1+8；卡内全部图片共同参与，N2 区分颜色/规格/款式 target_appearances，不让运营维护多图关系。
3. 国家只限制语言和硬规则，不限制营销场景。
4. N5 用五种动态策略：FAB价值、场景占有、情绪触发、拟人表达、身份表达；八图至少四种，FAB至少一张，拟人最多一张。
5. N6 直接写目标语言文案，再编译英文 gpt-image-2 Prompt；可见文案逐字锁定，不让图片模型翻译或加字。
6. 只有全部图片无有效商品、核心图文冲突、事实幻觉、平台硬规则或文字锁错误才阻断。image_role、visible product identity、string、JSON/Schema 错误都先内部修复或映射为“系统识别异常，请重试预备生成”。
7. 不新增表、依赖、Redis、WebSocket、状态库或队列；复用 ClusterAsset、analysis_snapshot、PromptVersion、现有 API、TanStack Query 和 dnd-kit。

硬边界：
不得打印 Secret，不改变 ERP/OSS/APIMart 凭据契约，不把竞品图传给 DeepSeek 或 gpt-image-2，不覆盖历史 Prompt/生成/审核，不删除/skip/todo 测试，不放宽断言，不用 || true。正式付费 1+8、Hermes 部署和并发提升必须等全量测试后由主 Agent 签核。

顺序：
Task 0 先跑基线并写 PROGRESS.md。Task 1 做 3.3 Schema/seed。Task 2 修 N1/N2 角色归一、string 占位、多图 target_appearances 和业务错误。Task 3 做 N3 fact_ledger 与 consumer_context。Task 4 做 N5 五策略和外观覆盖。Task 5 做 N6 目标语言文案、回译、自评分和英文最终 Prompt。Task 6 做 N7 文字锁、事实/规则/版本门禁和一次自动重写。Task 7 做前端逐槽 Prompt 加载、脉冲进度、缩略图点击预览、缩略图拖拽和 generate 自动预备。Task 8 全量验证和交接。

验收命令：
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
npm --prefix frontend test
npm --prefix frontend run build
git diff --check

完成条件：
1. 导入后整理零 AI；预备生成并发可见；Prompt 逐槽出现；正式生成缺 Prompt 会自动预备，N7 不通过不付费。
2. 单图、多角度、多颜色/款式、排序换主图、跨卡合并/拆分都通过；白底/总览覆盖全部 appearance，整套 8 张营销图策略不少于四种且重复场景签名为 0。
3. 目标语言文案自然、无歧义、逐字锁定；最终 gpt-image-2 Prompt 为英文控制且 ≤3500 字符。
4. 员工界面不出现 image_role、visible product identity、string、JSON、Schema、N2 may only 等内部字段。
5. 后端/前端测试数不低于当前基线，skipped=0；历史版本未覆盖；Secret 输出为0。真实付费和部署没有主 Agent 签核就停止。
```
