你是 E:\Project\picturesGenerate 的执行 Agent，这份任务书是唯一任务来源；中途没人可问，拿不准的写进 BLOCKED.md，继续不受影响的任务。断线或换会话先读 PROGRESS.md 接着做，每完成一项立刻更新。目标是把 Prompt OS 3.0 升级为 3.1：八张营销图从商品事实出发，动态使用 FAB价值、场景占有、情绪、拟人和身份表达，生成自然的目标语言图片文案与可直接提交 gpt-image-2 的英文 Prompt。冲突时按“商品/账号安全 > 事实准确 > 平台硬规则 > 文案质量 > 速度”取舍。“只允许/不得”是硬边界；“建议”可替换，但要在 PROGRESS.md 写原因。

## 我替领导拍的板

- 不新增第十个节点；只强化 N5/N6/N7，N1–N4/N8/N9 保持职责。
- 目标模板版本为 3.1.0；3.0.0 只退休不覆盖，历史 Prompt/生成/审核永久保留。
- 五种文案策略是动态候选池，不固定槽序；八图至少四种、FAB 至少一张、拟人默认最多一张。
- DeepSeek 温度：N2 0.3、N3 0.2、N4 0.4、N5 1.6、N6 0.9、N7 0.2、N8 0.4、N9 0.2。
- N6 直接用目标语言创作，不先中文直译；最终英文图片 Prompt 逐字锁定本地化文案。
- 名称为空但图片有商品时正常生成；只有全部图片无有效商品才要求换图。
- 真实付费 1+8 和 Hermes 部署必须放在全量测试之后；没有主 Agent 签核就停在本地可验收状态。

## 界限

只允许修改 platform_app/prompt_templates_v3.py、platform_app/services.py、platform_app/management/commands/seed_platform_templates.py、frontend/src/types.ts、frontend/src/components/PromptEditor.tsx、对应 tests/**、PROGRESS.md、BLOCKED.md、docs/project/STATUS.md、docs/superpowers/specs/节点prompt设定初稿.md。其他文件只读。不得读取、修改、删除或提交未跟踪的 docs/project/用户操作流程以及相关触发.md。不得新增模型、表、依赖、Redis、WebSocket或队列；不得打印 Secret、改变 ERP/OSS 契约、把竞品图传给 DeepSeek/gpt-image-2、覆盖历史版本。不得删除/skip/todo 测试、放宽断言、mock 被测核心对象、改验收命令或使用 || true。

## 现状与任务 0

2026-08-02 实测：分支 lxc/workbench-v4；353 个后端测试通过，前端 89 个通过，Django check 与 Vite build 通过。线上 N5/N6/N7 九个 published 模板均为 3.0.0，数据库长度与哈希匹配代码。当前只有 copy_intent/本地化短文案，没有可验证 FAB、情绪、拟人、身份表达和 N7 结构化文字锁。先完整读 CLAUDE.md、docs/superpowers/specs/2026-08-02-prompt-os-3.1-marketing-copy-design.md、docs/superpowers/plans/2026-08-02-prompt-os-3.1-marketing-copy-implementation.md、Prompt 节点规格、需求边界和 STATUS。运行 git status 与四条基线命令；数字不符就把证据置顶写入 BLOCKED.md，只做不受影响部分。核对后在 PROGRESS.md 写不超过 10 行的目标、顺序和最大风险。

## 任务

严格按实施计划 Task 1–7 顺序执行并小提交：先用失败测试冻结 creative_strategy、localized_copy.quality 和 N7 copy_checks；再让 N5 显式接收 consumer_context 并校验事实引用/策略分布；把五种营销方法、三候选本地化、语义回译和逐字冻结写入真实 system Prompt；N7 确定性检查文字逐字一致、语言、事实引用和套图重复，空泛/重复只自动重写一次，硬规则和幻觉直接阻断；最后增加节点温度快照与 PromptEditor 最小展示。不要把设计文档全文塞进 user message，不重构 services.py 架构。

## 规矩

每项先写失败测试、确认红、最小实现、跑绿、再提交。关键反向验证：未知 fact_id 必须失败；改动锁定文案一个字符必须被 N7 阻断；虚构 claim 不得进入自动改写；白底和禁字模板文字行必须为 0。六类 fixture 覆盖家居厨卫、宠物、婴幼儿、穿戴、美妆、工具/电器。相同验收连败 3 次换下一项并记录；结果低于基线就回滚该项如实报告。Hermes 操作必须用 hermes-remote，部署用 global.lock，锁忙就停止部署。

## 完成条件

1. 六类共 48 个营销槽中：每套至少四种策略、FAB 至少一次、重复签名/无效事实引用/文字锁错误/空泛模板文案均为 0；至少 42 槽的相关性、具体性、自然度、可信度均>=85。
2. 后端测试>=353、前端>=89、skipped=0、Django check/迁移漂移/Vite build/diff check 全通过；历史数据未覆盖、Secret 输出为0。经签核的真实1+8五项运营评分均>=4/5才部署。

每条完成条件都要在对话贴实际命令输出和反向验证红→绿证据，只说完成不算。BLOCKED.md 随交付提交，空也写“无”。最多三轮全量修复；满轮即停并报告卡点。
