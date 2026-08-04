# Prompt OS 4.1 GPT-5.5 执行索引

日期：2026-08-04
用途：给后续执行 Agent 的一页当前运行索引。历史设计若仍提到英文 Image2 控制稿、回译或 1+8 默认展示，以本文件和当前代码为准。

## 1. 员工实际路径

- `导入后整理`：只建商品卡，零 AI。员工整理名称、平台/国家覆盖、补充信息、缩略图顺序和跨卡合并。
- `预备生成`：对选中商品跑 N1–N7，商品名为空才调用 N1 主图视觉识别并回填名称/补充信息；商品名已填则跳过付费视觉识别，生成 8 个可编辑生成槽 Prompt，不生图。
- `正式生成`：没有当前 N7 pass Prompt 时自动预备，预备通过后自动继续 8 张生成图；提交给 Image2 的 prompt 等于员工最后编辑的槽位文本。
- `导入并自动出图`：导入后直接走同一条 N1–N7，再正式生成；识别失败只影响当前商品，其他商品继续。

默认配置：`Shopee 虾皮 / 东南亚通用 / 1:1 / 1K`。国家只控制语言、规则和禁用内容，不限制创意场景。

## 2. 一个商品卡怎么理解

一个商品卡默认产出一套 8 张生成图。卡内全部图片共同参与，不需要员工维护“多图关系”。Shopee VN 普通店仍可额外保留真实来源图直通，因此结果可见为“原图直通 + 8 张生成图”。

- 每张图默认一卡。
- 拖入同一卡表示共同生成一套图。
- 第一张是主参考；点击缩略图只预览，拖动排序到第一张才改变主参考。
- 卡内图片可能是角度、包装、细节，也可能是多色、多规格、多款。
- N2 必须输出 `product_family`、`shared_identity_lock`、`target_appearances`。
- 白底图和款式/内容总览必须覆盖全部 `target_appearances`；其他营销图可展示子集，但整套必须覆盖所有外观。
- 参考图里有多个重复商品实例时，不等于每张图都要全部出现；N5 必须用 `subject_plan.visible_unit_count` 写清当前槽位展示几件商品，N6 按这个数量编译。

## 3. 节点变量线

不允许用 `string`、空对象、旧 Prompt、`builtin-v1` 或通用兜底补洞。
DeepSeek 文本节点走官方 OpenAI-compatible 接口，当前执行模型为 `deepseek-v4-flash`，鉴权使用 `DEEPSEEK_API_KEY`，不复用 APIMart key。N5/N6 的 DeepSeek 输出可以是宽松文本：只要模型调用返回了非空文本，即使不是合法 JSON，也要以 `prompt_source="deepseek"` 包进下游 `raw_model_text`，让后续节点继续接收原始营销/生图设计。JSON 格式、字段缺失、普通空泛表达不触发 deterministic fallback；只有模型调用异常或空响应才进入失败/显式 fallback 分支。

```text
N1 主图视觉识别（商品名为空才调用模型）/ valid_asset_ids
→ N2 本地商品身份归并 / shared_identity_lock / target_appearances
→ N3 本地最小事实台账 / allowed_fact_ids
→ N4 本地固定白底 Prompt / white_reference_plan
→ N5 slot_plans / appearance_coverage_plan / copywriting_chain
→ N6 display_prompt / reference_plan
→ N7 n7_pass_snapshot
→ Generation 直传 PromptVersion.prompt_text
```

每个节点快照必须同一种形状：`node/model/temperature/input_fingerprint/system_prompt_version/input/expected_schema/output/normalized_output/quality`。代码下游只读 `normalized_output`；原始 `output` 只用于管理员排障。

## 4. 九节点最小职责

| 节点 | 只做什么 | 必须产出 | 阻断边界 |
| --- | --- | --- | --- |
| N1 逐图观察 | 每张图片变成可见证据 | 有效图片、候选名称、可见结构 | 全部图片都无商品才阻断 |
| N2 身份归并 | 把一卡多图整理成商品家族和目标外观 | 商品名、身份锁、`target_appearances` | 全部无商品；人工名称与实物核心冲突 |
| N3 事实台账 | 区分 confirmed/observed/inferred | 可引用事实、风险、用途 | 高风险事实无法移除 |
| N4 白底编译 | 写标准白底图 Prompt | 白底 Prompt、参考图计划 | 无我方参考图或白底硬规则无法满足 |
| N5 营销导演 | 把事实翻译成八张购买任务 | `slot_plans`、视觉主题、具体瞬间、版式方向、`copywriting_chain` | 重复/低质先重写；仍不能覆盖外观才阻断 |
| N6 图片提示词设计 | 写员工可编辑的最终中文导演稿，并把目标国家可见文字直接写进稿内 | `display_prompt`、参考图计划；`prompt_text=display_prompt` | 模型调用失败或空响应；无可提交参考 |
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
→ 视觉主题 visual_theme
→ 具体瞬间 specific_moment
→ 审美取向 aesthetic_point_of_view
→ 版式方向 typography_direction
→ 商品范围 subject_plan.product_scope / visible_unit_count
→ 构图计划 composition_plan
→ 灯光材质 style_plan
→ 文字版式主题 text_layout_theme
→ 情绪触发 emotional_trigger
→ 文案意图 copy_intent
→ 画面签名 composition_signature
```

N6 只输出运营可编辑的 `display_prompt`。它必须写成完整广告图导演稿，继承 N5 的视觉主题、具体瞬间、商品范围、可见数量、审美取向、构图、灯光材质和版式方向，并把目标国家图片文字直接写进稿内，例如：`画面可见文字逐字渲染：xxx / xxx`。文字计划要精确到语言、行数、位置、字体气质、字号层级、行距、颜色、占画面比例和背景处理方式；文字默认使用透明叠字、自然留白或轻阴影，不做大块实心横幅。不再生成英文控制稿，不做中文转英文，不做英文回译，不输出 `text_layout_theme` JSON。

Usage、使用、功能、操作、穿戴、佩戴、手持类槽位必须在 N5 `subject_plan.person_presence` 写出真人/手部/身体局部/用户/宠物如何实际接触、拿起、佩戴、携带、摆放或操作商品。Model/scale、模特、比例、尺度类槽位必须写出真人、手部、宠物或真实空间尺度线索，说明它如何帮助买家判断大小、适配对象或使用尺度。N6 的 `display_prompt` 必须完整保留这层关系，正式生图直传该中文导演稿。

## 6. 员工错误边界

这些只能进日志/管理员排障，不能给员工看：`image_role`、`visible product identity`、JSON、Schema、字段名、模型英文原文、`string`。

员工只看五类中文结果：

- `AI 正在识别 / Prompt 生成中 / 正在生成`
- `请换一张更清楚的商品图`
- `请补充商品名称或用途`
- `登录已过期，请重新登录`
- `系统识别异常，请重试预备生成`

字段别名、大小写差异、示例值 `string` 先内部归一化；同一卡有一张有效商品图就继续。

N5/N6 的 deterministic fallback 只允许测试、demo 或开发无模型环境通过 `PROMPT_OS_ALLOW_FALLBACK=true` 显式启用；生产默认关闭。`PromptVersion.structured_output.prompt_source="fallback"` 在 fallback 关闭时不能进入正式生图。N5/N6 模型调用失败或空响应时，员工只看：`提示词生成失败，请重试预备生成`。

## 7. 工作台边界

- 顶部配置一行：平台、国家、比例、分辨率、项目风格提示词；比例和分辨率分开选。
- 图片/文件夹和 ERP SKU 同屏常驻；导入按钮不藏进抽屉。
- 商品卡一行约 5 张，主图 `object-contain`，不裁掉商品全貌。
- 商品卡的商品描述只保留“商品名称”和“补充信息”；平台/国家作为生成配置覆盖项保留，颜色、款式、风格、身份锁和识别事实合并进补充信息，不再拆成多个描述字段。
- 商品名称为空时，N1/AI 识别出的名称要自动回填到商品卡；商品名称已由员工填写时跳过付费视觉识别，只用名称、补充信息和参考图继续 N5/N6。英文识别短语进入商品卡前要转成中文运营文本。
- 缩略图横排，溢出才滚动；点击预览，拖动排序，跨卡拖动合并。
- 右侧固定侧浮层展示商品身份和 Prompt，不改变商品卡网格。
- 长列表滚动时固定操作条常驻：已选数量、预备生成、正式生成、生产结果。
- 预备/生成必须显示脉冲进度，例如 `N1 2/6`、`N6 4/8`。
- 预备和生成中的单图、单商品或多选商品都必须能暂停；暂停只取消当前 active/pending 工作，不覆盖历史版本。
- 生产结果页图片使用浏览器 lazy/async 加载，避免 8 图和历史版本一次性抢占首屏加载。
- 生产结果页以“完成且有图”为导出门槛，默认勾选每个槽位最新完成图；人工审核不再控制 ZIP 导出，只用于质量判断、圈选修改和版本管理。
- 生产结果页必须能看到当前选中结果图当时的 `generation.prompt_text`，用于排查哪条提示词生成了哪张图。

## 8. 执行文件

按顺序读：

1. `CLAUDE.md`
2. `docs/project/REQUIREMENTS-BOUNDARY-CONFIRMATION.md`
3. 本文件
4. `docs/superpowers/specs/2026-08-02-prompt-os-4.1-node-marketing-design.md`
5. `docs/superpowers/plans/2026-08-02-prompt-os-4.1-gpt55-implementation.md`
6. `docs/project/LEADER-GOAL-PROMPT-OS-4.1.md`

禁止读取、修改、删除或提交：`docs/project/用户操作流程以及相关触发.md`。
