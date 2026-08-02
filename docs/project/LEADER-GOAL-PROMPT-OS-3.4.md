你是 E:\Project\picturesGenerate 的 GPT-5.5 执行 Agent。这份任务书是唯一来源；断点先读写 PROGRESS.md，真实阻塞写 BLOCKED.md。目标：按 Prompt OS 3.4 把工作台、N1–N9、营销文案、目标语言文字锁和正式生成跑通。

我替领导拍的板：
- 默认 Shopee 虾皮 / 东南亚通用 / 1:1 / 1K。国家只管语言和规则，不锁死国家场景。
- 一个商品卡出一套 1+8；卡内多图共同参与，可是角度、颜色、规格、款式、包装或细节；不让员工填多图关系。
- 第一张图是主参考；点击缩略图只预览，拖动排序到第一位才改主参考。
- 只有全部图片都无有效商品才阻断；image_role、string、JSON、Schema 属系统问题，脱敏为“系统识别异常，请重试预备生成”。
- N5 温度 1.6，N6 温度 0.9，N2/N3/N7 低温。

界限：
- 只改 docs/project/STATUS.md、PROGRESS.md、BLOCKED.md、platform_app/prompt_templates_v3.py、platform_app/services.py、platform_app/management/commands/seed_platform_templates.py、frontend/src/components/ProductCard.tsx、frontend/src/components/PromptEditor.tsx、frontend/src/types.ts、对应 tests。
- 禁止读取/修改/提交 docs/project/用户操作流程以及相关触发.md。
- 不新增表、依赖、Redis、WebSocket、新队列；不打印 Secret；不覆盖历史 Prompt/生成/审核；不 skip/todo/delete 测试，不放宽断言。

任务0：跑 git status、后端 pytest、Django check、makemigrations dry-run、前端 test、Vite build。把基线数字和最大风险写 PROGRESS.md；对不上写 BLOCKED.md。

任务1：Prompt OS 3.4 契约。红测后实现：PROMPT_OS_VERSION=3.4.0；N2 有 product_family/shared_identity_lock/target_appearances；N5 有 creative_strategy/appearance_ids/scene_brief/copy_intent；N6 有 localized_copy/back_translation/copy_quality/final_english_image_prompt；N7 有 copy_checks/fact_checks/appearance_coverage_checks/rewrite_reasons。提交。

任务2：N1/N2 多图身份。红测覆盖：部分图片坏不阻断、image_role 别名归一、string 不进商品名、多色多款生成多个 target_appearances、全部无商品才阻断。用一个共享 normalizer 修根因。提交。

任务3：缩略图排序与拖拽范围。红测覆盖：点击缩略图只换大图预览；只有缩略图条能拖；排序写 asset_order；跨卡移动使旧预备失效。复用现有 API，不加表。提交。

任务4：N3/N5 创意策略总线。把用户文案方法写进 N5：FAB、场景占有、情绪、拟人、身份表达。红测覆盖：八槽至少四种策略且含 FAB、拟人默认最多一张、每槽引用已知事实/允许推断、整套覆盖全部外观、国家不强制场景。提交。

任务5：N6 本地化文案与英文生图 Prompt。红测覆盖：VN/TH/SEA 语言策略、最终 Prompt 是英文、目标语言文字逐行精确出现一次、Style DNA 不新增事实、长度 <=3500。PromptEditor 逐槽加载中文槽名、可见文案和最终 Prompt。提交。

任务6：N7 闸门与一次重写。红测覆盖：文字改一字阻断、未知 fact ref 阻断、空泛/重复文案只重写当前槽一次且不显示用户错误、硬规则不重写、无当前 N7 pass 不创建付费 Generation。提交。

任务7：生成编排和进度。正式生成缺/过期 Prompt 时自动预备，通过后继续；白底失败不跑营销图，营销失败只重做失败槽。前端立即显示脉冲进度，按钮常驻视口可见。提交。

规矩：每任务先红测、确认红、最小实现、绿测、提交。同一验收连败 3 次停该项写 BLOCKED.md。任何想顺手改的非白名单问题只记录，不动。

完成条件：
- 全量 pytest、Django check、makemigrations dry-run、前端 test、Vite build、git diff --check 通过，数量不低于任务0，skipped=0。
- 假模式或真实模式跑通单图、多角度、多色/多款、排序换主图、整理零 AI、正式生成自动预备、9 Prompt 逐项出现、无 N7 pass 不付费、白底门禁、人工审核后导出。
