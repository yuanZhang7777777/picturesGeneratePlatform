# GPT-5.5 执行任务书：Prompt OS 3.2 节点与工作台链路

在执行 agent 那边输 `/goal `，粘贴下面整段。

```text
你是 E:\Project\picturesGenerate 的 GPT-5.5 执行 Agent。先读 CLAUDE.md、docs/project/STATUS.md、docs/project/REQUIREMENTS-BOUNDARY-CONFIRMATION.md、docs/superpowers/specs/2026-08-02-prompt-os-3.2-node-workflow-design.md、docs/superpowers/plans/2026-08-02-prompt-os-3.2-node-workflow-implementation.md、docs/superpowers/specs/节点prompt设定初稿.md，再按计划 Task 0–7 连续执行。断点写 PROGRESS.md，真实阻塞写 BLOCKED.md。

这活为什么干：
当前系统能导入和生成，但预备/正式生成、商品卡拖拽、多图关系、Prompt文案和员工可见错误没有真正串起来。目标是让“导入后整理零AI → 预备生成N1–N7 → 正式生成1+8 → 审核导出”稳定可用。

硬边界：
1. 禁止读取、修改、删除或提交 docs/project/用户操作流程以及相关触发.md。
2. 不新增数据表、Redis、WebSocket、消息队列、前端依赖或第三方文件选择器。
3. 不写入或打印任何 Secret，不覆盖历史 PromptVersion、Generation、审核记录。
4. 整理导入、拖拽、排序、普通编辑不得调用 AI。
5. 自动出图和正式生成缺 Prompt 时必须自动跑 N1–N7；N7 不通过不得付费生成。
6. 内部字段错误不得给员工看：image_role、visible product identity、N2 may only、JSON Schema、string 都只能映射成业务中文。
7. 一张图默认一卡；拖进同一卡即共同生成一套 1+8，不显示多图关系，不让员工额外确认。
8. 第一张缩略图是主参考；点击缩略图只切换大图，拖到第一位才换主参考。
9. N1 分析同一卡全部图；N2 输出 product_family、shared_identity_lock、target_appearances。不同颜色/规格/款式要区分 appearance；白底/款式总览覆盖全部 appearance。
10. 真实付费 1+8、真实 ERP/OSS smoke、Hermes 部署必须等主 Agent 签核。

我替领导拍的板：
- 默认项目配置是 Shopee 虾皮 / 东南亚通用 / 1:1 / 1K。
- 顶部平台、国家、比例、分辨率、项目风格提示词同一行；比例和分辨率分开选。
- 图片/文件夹与 ERP SKU 常驻同屏；导入后整理是主按钮，导入并自动出图是次按钮。
- 商品卡一行尽量 5 个左右，大图 object-contain，下面横向真实缩略图，只有缩略图区能拖。
- 详情用右侧固定浮层，不改变商品网格；右侧只显示每槽任务和 Prompt，不显示未生成图片预览。
- 文案采用五种策略：FAB价值翻译、场景占有、情绪触发、拟人表达、身份表达。八张营销图至少四种策略，FAB至少一张，拟人默认最多一张。
- N6 直接写目标国家语言文案，并把这些文字逐字锁进英文 gpt-image-2 Prompt；不要让图像模型自由翻译或加字。

执行顺序：
Task 0 冻结基线：git status、后端全量、前端全量、Django check、Vite build；写 PROGRESS 开工回执。
Task 1 前端布局：一行配置、同屏导入、右下常驻预备/正式生成/结果按钮。
Task 2 商品卡拖拽：点击缩略图预览；只缩略图区拖；排序更新 asset_order；跨卡合并/空白拆分。
Task 3 预备状态：多商品并发、逐商品进度、Prompt逐槽加载、技术错误脱敏。
Task 4 N1/N2 多图变量总线：product_family、shared_identity_lock、target_appearances、primary/supporting assets。
Task 5 N5/N6 Prompt：把商品事实翻译成购买任务、情绪、场景和身份表达；引入 Style DNA 结构但不复制源内容。
Task 6 N7和正式生成：文案逐字锁、事实/规则/版本检查；generate 缺 Prompt 自动 prepare 后续接生成。
Task 7 全量验收：全量测试、浏览器闭环、文档更新；部署和真实付费只在主 Agent 签核后执行。

防作弊验收：
- 不许 skip/todo/删测试/放松断言/|| true。
- 测试数不得低于 Task 0，skipped 必须为 0。
- 必须有失败测试先红再绿。
- 不得用通用兜底 Prompt 创建 Generation。
- 不得把技术字段作为员工错误文案。
- 不得触碰禁止文档。

完成条件：
1. 单图、多角度、多颜色/款式、排序换主图、跨卡合并/拆分、整理零AI、预备并发、Prompt逐槽出现、正式生成自动预备全部通过。
2. 后端全量、Django check、迁移漂移、前端全量、Vite build、git diff --check 通过；提交小步提交，最后报告提交号、测试输出、预览链接/未部署原因、回退点和未完成项。
```

跑完回来说一声，主 Agent 复验。
