# Prompt OS 4.1 GPT-5.5 执行索引

日期：2026-08-02  
用途：给后续 GPT-5.5 执行 Agent 的一页索引。它不是新版本；若与详细设计冲突，以 `docs/superpowers/specs/2026-08-02-prompt-os-4.1-node-marketing-design.md` 的 `4.0.2 GPT-5.5 执行用最终节点蓝图` 为准。

## 1. 员工实际路径

- `导入后整理`：只建商品卡，零 AI。员工整理名称、国家、补充资料、单品风格、缩略图顺序和跨卡合并。
- `预备生成`：对选中商品跑 N1–N7，补空商品名，生成商品身份、事实台账和 1+8 Prompt，不生图。
- `正式生成`：没有当前 N7 pass Prompt 时自动预备，预备通过后自动继续白底和营销图生成。
- `导入并自动出图`：导入后直接走同一条 N1–N7，再正式生成；识别失败只影响当前商品，其他商品继续。

默认配置：`Shopee 虾皮 / 东南亚通用 / 1:1 / 1K`。国家只控制语言、规则和禁用内容，不限制创意场景。

## 2. 一个商品卡怎么理解

一个商品卡只产出一套 1+8。卡内全部图片共同参与，不需要员工维护“多图关系”。

- 每张图默认一卡。
- 拖入同一卡表示共同生成一套图。
- 第一张是主参考；点击缩略图只预览，拖动排序到第一张才改变主参考。
- 卡内图片可能是角度、包装、细节，也可能是多色、多规格、多款。
- N2 必须输出 `product_family`、`shared_identity_lock`、`target_appearances`。
- 白底图和款式/内容总览必须覆盖全部 `target_appearances`；其他营销图可展示子集，但整套必须覆盖所有外观。

## 3. 节点变量线

不允许用 `string`、空对象、旧 Prompt、`builtin-v1` 或通用兜底补洞。

```text
N1 owned_observations / valid_asset_ids
→ N2 product_family / shared_identity_lock / target_appearances / primary_asset_id
→ N3 fact_ledger / allowed_fact_ids
→ N4 white_prompt_version / white_reference_plan
→ N5 slot_plans / appearance_coverage_plan / copywriting_chain
→ N6 localized_copy / final_english_image_prompt / reference_plan
→ N7 n7_pass_snapshot
→ Generation
```

每个节点快照必须同一种形状：`node/model/temperature/input_fingerprint/system_prompt_version/input/expected_schema/output/normalized_output/quality`。代码下游只读 `normalized_output`；原始 `output` 只用于管理员排障。

## 4. 九节点最小职责

| 节点 | 只做什么 | 必须产出 | 阻断边界 |
| --- | --- | --- | --- |
| N1 逐图观察 | 每张图片变成可见证据 | 有效图片、候选名称、可见结构 | 全部图片都无商品才阻断 |
| N2 身份归并 | 把一卡多图整理成商品家族和目标外观 | 商品名、身份锁、`target_appearances` | 全部无商品；人工名称与实物核心冲突 |
| N3 事实台账 | 区分 confirmed/observed/inferred | 可引用事实、风险、用途 | 高风险事实无法移除 |
| N4 白底编译 | 写标准白底图 Prompt | 白底 Prompt、参考图计划 | 无我方参考图或白底硬规则无法满足 |
| N5 营销导演 | 把事实翻译成八张购买任务 | `slot_plans`、`copywriting_chain` | 重复/低质先重写；仍不能覆盖外观才阻断 |
| N6 单槽编译 | 写目标语言文案和英文生图 Prompt | 三候选文案、选中文案、最终英文 Prompt | 语言错、事实错、文字锁错 |
| N7 规则闸门 | 付费前最后门禁 | pass 快照或中文阻断原因 | 付费风险、商品错误、文案错误 |
| N8 修改导演 | 圈选意见变成最小差量 | 差量 Prompt | 会破坏身份锁或硬规则 |
| N9 失败简化 | 只处理可安全简化的 Prompt | 更短等价 Prompt | 网络、余额、限流、未知提交不处理 |

## 5. 文案与创意规则

N5 不写最终图中文字，先设计购买冲动。每套至少覆盖四种策略，必须至少一张 FAB，拟人默认最多一张：

1. `fab_value`：商品事实 → 优势 → 用户收益。
2. `scene_ownership`：让用户脑中出现“我正在用”的画面。
3. `emotion_shift`：把麻烦、尴尬、不安转成轻松、安心、体面。
4. `personification`：商品用自然口吻说一句话。
5. `identity_signal`：让商品代表审美、身份、品位或生活方式。

每槽必须有：

```text
商品事实 fact_refs
→ 用户任务 user_job
→ 用户结果 value_translation
→ 使用画面 scene_brief
→ 情绪触发 emotional_trigger
→ 文案意图 copy_intent
→ 画面签名 composition_signature
```

N6 再直接用目标语言写图片文字，生成 3 个候选并自评。最终给 GPT Image 2 的画面控制必须是英文，目标语言文字逐行锁死：不翻译、不改写、不增删。

## 6. 员工错误边界

这些只能进日志/管理员排障，不能给员工看：`image_role`、`visible product identity`、JSON、Schema、字段名、模型英文原文、`string`。

员工只看五类中文结果：

- `AI 正在识别 / Prompt 生成中 / 正在生成`
- `请换一张更清楚的商品图`
- `请补充商品名称或用途`
- `登录已过期，请重新登录`
- `系统识别异常，请重试预备生成`

字段别名、大小写差异、示例值 `string` 先内部归一化；同一卡有一张有效商品图就继续。

## 7. 工作台边界

- 顶部配置一行：平台、国家、比例、分辨率、项目风格提示词；比例和分辨率分开选。
- 图片/文件夹和 ERP SKU 同屏常驻；导入按钮不藏进抽屉。
- 商品卡一行约 5 张，主图 `object-contain`，不裁掉商品全貌。
- 缩略图横排，溢出才滚动；点击预览，拖动排序，跨卡拖动合并。
- 右侧固定侧浮层展示商品身份和 Prompt，不改变商品卡网格。
- 长列表滚动时固定操作条常驻：已选数量、预备生成、正式生成、生产结果。
- 预备/生成必须显示脉冲进度，例如 `N1 2/6`、`N6 4/8`。

## 8. 执行文件

按顺序读：

1. `CLAUDE.md`
2. `docs/project/REQUIREMENTS-BOUNDARY-CONFIRMATION.md`
3. 本文件
4. `docs/superpowers/specs/2026-08-02-prompt-os-4.1-node-marketing-design.md`
5. `docs/superpowers/plans/2026-08-02-prompt-os-4.1-gpt55-implementation.md`
6. `docs/project/LEADER-GOAL-PROMPT-OS-4.1.md`

禁止读取、修改、删除或提交：`docs/project/用户操作流程以及相关触发.md`。
