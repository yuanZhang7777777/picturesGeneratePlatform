# GPT-5.5 执行任务书：Prompt OS 4.1

你是 `E:\Project\picturesGenerate` 的执行 Agent。按本文连续完成 Prompt OS 4.1；不要重新设计需求。先读：

1. `CLAUDE.md`
2. `docs/project/REQUIREMENTS-BOUNDARY-CONFIRMATION.md`
3. `docs/superpowers/specs/2026-08-02-prompt-os-4.1-node-marketing-design.md`
4. `docs/superpowers/plans/2026-08-02-prompt-os-4.1-gpt55-implementation.md`
5. `docs/superpowers/specs/节点prompt设定初稿.md`
6. `docs/project/STATUS.md`

禁止读取、修改、删除或提交：`docs/project/用户操作流程以及相关触发.md`。

## 目标

实现一条能真实跑通的链路：

`导入后整理零 AI → 预备生成 N1–N7 → 9 个 Prompt 可见 → 正式生成缺 Prompt 自动预备 → N7 通过后才付费 → 白底先行 → 8 张营销图 → 人工审核后导出`

默认：`Shopee 虾皮 / 东南亚通用 / 1:1 / 1K`。

## 硬边界

1. 不新增节点、数据表、依赖、Redis、WebSocket 或队列。
2. 不打印 Secret。
3. 不覆盖历史 Prompt、生成、审核或导出版本。
4. 不把 `string`、`image_role`、JSON、Schema、`visible product identity` 这类内部错误显示给员工。
5. 不让员工维护“多图关系”。
6. 一个商品卡只产出一套 1+8；卡内全部图片共同参与。
7. 第一张是主参考；点击缩略图只预览，拖动排序到第一位才改变主参考。
8. 国家只控制语言、规则和禁用内容，不锁死场景。
9. 竞品图不得进入 DeepSeek、GPT Image 2、生产 Prompt、生成参考、导出包或商品事实。
10. 每任务红测、确认红、最小实现、绿测、小提交。

## 必须实现的节点行为

- N1：观察全部图片；只全部无有效商品才阻断。
- N2：输出 `product_family/shared_identity_lock/target_appearances`；多色、多规格、多款要拆目标外观。
- N3：输出事实台账；可见文案只能引用有 `fact_id` 的安全事实。
- N4：白底图覆盖全部关键外观；无新增文字和营销道具。
- N5：八图至少覆盖四种策略：FAB、场景占有、情绪触发、拟人表达、身份表达；FAB 至少一张，拟人默认最多一张。
- N6：直接用目标语言写图片文字；最终给 GPT Image 2 的画面控制用英文；目标语言文字逐行锁死，不允许模型翻译或额外加字。
- N7：文字锁、事实、平台规则、竞品隔离、版本指纹硬门禁；空泛/重复/不顺只重写当前槽一次。
- N8：圈选修改只改目标区域。
- N9：只简化 Prompt 复杂度或可重写安全失败；网络、限流、余额不走 N9。

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
- 目标语言文字在英文生图 Prompt 中逐行锁定。
- 无 N7 pass 不创建付费 Generation。
- 白底失败不跑营销图。
- 技术错误不直接显示给员工。
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
