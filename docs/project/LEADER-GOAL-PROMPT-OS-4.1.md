# GPT-5.5 执行任务书：Prompt OS 4.1

你是 `E:\Project\picturesGenerate` 的执行 Agent。按本文连续完成 Prompt OS 4.1；不要重新设计需求。先读：

1. `CLAUDE.md`
2. `docs/project/REQUIREMENTS-BOUNDARY-CONFIRMATION.md`
3. `docs/superpowers/specs/2026-08-02-prompt-os-4.1-node-marketing-design.md`
4. `docs/superpowers/plans/2026-08-02-prompt-os-4.1-gpt55-implementation.md`
5. `docs/superpowers/specs/节点prompt设定初稿.md`
6. `docs/project/STATUS.md`

禁止读取、修改、删除或提交：`docs/project/用户操作流程以及相关触发.md`。

不要新建 4.2/4.3 设计，不要再讨论流程取舍；4.1 设计和计划就是本次唯一基线。

开工先写 `PROGRESS.md`：当前分支、基线提交、已存在脏文件、理解的执行顺序、最大风险。遇到真实阻塞写 `BLOCKED.md`，不要在聊天里等人。每完成一个 Task 立刻小提交；提交前只 stage 当前 Task 文件，不要把已有脏改带进去。

## 目标

实现一条能真实跑通的链路：

`导入后整理零 AI → 预备生成 N1–N7 → 9 个 Prompt 可见 → 正式生成缺 Prompt 自动预备 → N7 通过后才付费 → 白底先行 → 8 张营销图 → 人工审核后导出`

默认：`Shopee 虾皮 / 东南亚通用 / 1:1 / 1K`。

工作台也必须一起收口：顶部平台、国家、比例、分辨率、项目风格提示词同一行；图片/文件夹和 ERP SKU 同屏常驻；商品卡一行约 5 张；右侧详情浮层不改变网格；长列表滚动时仍有固定操作条可预备/正式生成。

## 硬边界

1. 不新增节点、数据表、依赖、Redis、WebSocket 或队列。
2. 不打印 Secret。
3. 不覆盖历史 Prompt、生成、审核或导出版本。
4. 不把 `string`、`image_role`、JSON、Schema、`visible product identity` 这类内部错误显示给员工。
5. 不让员工维护“多图关系”。
6. 一个商品卡只产出一套 1+8；卡内全部图片共同参与。
7. 卡内图片可能是角度、包装、细节，也可能是多色、多规格、多款；不要让员工维护“多图关系”。
8. 第一张是主参考；点击缩略图只预览，拖动排序到第一位才改变主参考。
9. 国家只控制语言、规则和禁用内容，不锁死场景；UI 写“国家”，不要写 `market`。
10. 竞品图不得进入 DeepSeek、GPT Image 2、生产 Prompt、生成参考、导出包或商品事实。
11. 每任务红测、确认红、最小实现、绿测、小提交。
12. 每个节点 system prompt 必须有：角色与唯一职责、输入变量、决策规则、质量评分、JSON 约束、失败归一化；不能只写一句职责摘要。
13. 节点失败先分类：模型字段/示例值先内部归一化；单张坏图不阻断同一卡；只有全部无商品、人工名称冲突、事实/规则硬冲突才阻断当前商品；员工只看中文可行动错误。

## 必须实现的节点行为

- N1：观察全部图片；只全部无有效商品才阻断。
- N2：输出 `product_family/shared_identity_lock/target_appearances`；多色、多规格、多款要拆目标外观。
- N3：输出事实台账；可见文案只能引用有 `fact_id` 的安全事实。
- N4：白底图覆盖全部关键外观；无新增文字和营销道具。
- N5：八图至少覆盖四种策略：FAB、场景占有、情绪触发、拟人表达、身份表达；FAB 至少一张，拟人默认最多一张。
- N5：每槽必须有 `user_job/value_translation/scene_brief/emotional_trigger/copy_intent/composition_signature/appearance_ids`，还要有 `copywriting_chain`：商品事实→优势→用户结果→使用画面→情绪触发→文案角度。不能只写“核心卖点图”。
- N6：直接用目标语言写图片文字；先生成 3 个文案候选并按事实安全、语言流畅、场景贴合、购买冲动评分选择；最终给 GPT Image 2 的画面控制用英文；目标语言文字逐行锁死，不允许模型翻译或额外加字；预备浮层展示“是什么图 + Prompt”，不展示未生成图片预览。
- N7：文字锁、事实、平台规则、竞品隔离、版本指纹硬门禁；空泛/重复/不顺只重写当前槽一次；技术字段错误统一脱敏成可操作提示。
- N8：圈选修改只改目标区域。
- N9：只简化 Prompt 复杂度或可重写安全失败；网络、限流、余额不走 N9。

## 失败显示规则

不得把 `image_role must...`、`visible product identity cannot...`、JSON、Schema、字段名、模型英文原文直接显示给员工。字段别名、`string`、大小写差异先修一次；修不好才显示“系统识别异常，请重试预备生成”。图片确实看不出商品时才显示“请换一张更清楚的商品图或补充商品名称/用途”。

## 节点交接线

必须按这一条变量线实现，任何一步缺值或过期都不能兜底生成：

`N1 owned_observations/valid_asset_ids → N2 product_family/shared_identity_lock/target_appearances/primary_asset_id → N3 fact_ledger → N4 white_prompt_version → N5 slot_plans/appearance_coverage_plan → N6 localized_copy/final_english_image_prompt/reference_plan → N7 n7_pass_snapshot → Generation`

禁止用 `string`、空对象、旧 PromptVersion、`builtin-v1` 或通用 Prompt 补洞。

## 执行顺序

按 `docs/superpowers/plans/2026-08-02-prompt-os-4.1-gpt55-implementation.md` 的 Task 0–9 执行。每个 Task 独立提交。失败 3 次仍不能推进时写 `BLOCKED.md`，说明命令、错误、已排除原因、下一步需要谁处理。

## 验收

必须覆盖：

- 单图商品。
- 同商品多角度。
- 多色/多款组合。
- 排序换主参考。
- 跨卡合并和拆分。
- 整理导入零 AI。
- 正式生成自动预备。
- 9 个 Prompt 逐项出现。
- Prompt 里文案不是空泛模板话术。
- N5 每槽都有购买任务、用户收益、情绪/身份触发和画面签名。
- N5 每槽都有 `copywriting_chain`，且原始事实来自 N3 `fact_id`。
- N6 每槽保存 3 个文案候选和选中原因。
- 目标语言文字在英文生图 Prompt 中逐行锁定。
- 员工界面不出现 `string`、`image_role`、JSON、Schema 或 `market`。
- 无 N7 pass 不创建付费 Generation。
- 白底失败不跑营销图。
- 技术错误不直接显示给员工。
- 单图坏、字段别名、Schema 示例值不会让整批或整卡失败。
- 人工审核后导出。

最终运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

不跑真实付费 APIMart、不部署 Hermes，除非主 Agent 明确签核。
