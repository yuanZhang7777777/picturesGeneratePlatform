# Prompt OS v2 九节点可执行规格

## 1. 目标与边界

本文定义 AI 商品出图平台的九个生产节点、节点间 JSON 契约、核心系统提示词、失败处理和不可变快照。它是 Prompt 模板与后端实现的共同规格，不是给普通员工阅读的操作手册。

默认产物为：

1. 槽位 01：标准白底商品图。
2. 槽位 02–09：八张职责不重复的营销图。

Shopee VN 普通店是唯一已发布的顺序例外：槽位 01 为真实上传/ERP 原图直通，槽位 02 为标准白底商品图，槽位 03–09 为七张营销图。后文的“白底槽位”按槽位语义识别，不硬编码为顺序 1。

市场模板可以预占或关闭营销槽位，但不得删除标准白底图，也不得绕过白底图先行门禁。所有生成结果先进入 `pending_review`，只有人工审核通过的具体版本可以导出。

### 1.1 固定模型

| 能力 | 固定模型或执行器 | 约束 |
| --- | --- | --- |
| 视觉观察 | `gpt-5-nano-2025-08-07` | APIMart Responses；只解析 `output[].content[].text` |
| 文本分析、策划、编译、修改和简化 | `deepseek-v4-pro` | APIMart 非流式 Chat Completions；默认 `temperature=1.2`；严格 JSON |
| 图片生成与圈选修订 | `gpt-image-2` | 参考图先上传；`image_urls` 为 URL 字符串数组；`n=1` |
| 硬规则执行 | 后端确定性规则引擎 | 模型不得覆盖确定性硬阻断 |

任一模型未开通、模型 ID 未知或账户契约不匹配时，关闭依赖节点，不切换其他模型。APIMart 中文文档和当前账户契约测试是端点、参数、响应、限流和计费的唯一接入依据。

当前生产核心提示词版本为 `2.1.0`。N1–N9 使用本规格各节点的完整“核心系统提示词”，不得以一句职责摘要替代；DeepSeek 节点把完整内容作为 Chat Completions 的 `system` 消息发送。`3500` 字符上限只约束 N4、N6、N8、N9 最终提交给 `gpt-image-2` 的单图 Prompt，不约束节点系统提示词。

### 1.2 九节点总流程

```text
我方素材
  → N1 逐图观察
  → N2 身份归并
  → N3 推断台账
  ├→ N4 白底编译器 → N7 规则闸门 → gpt-image-2 → 白底归档
  └→ N5 八图营销导演 → N6 本地化单槽编译器 → N7 规则闸门
                                                ↓ 白底成功后
                                           gpt-image-2

人工审核要求修改 → N8 修改导演 → N7 → gpt-image-2 → 新版本
可简化的生成失败 → N9 失败简化器 → N7 → gpt-image-2 → 新 attempt
```

营销槽位可以在白底图完成前编译并通过规则闸门，但不得提交 `gpt-image-2`。白底图归档后，营销图参考数组额外加入该白底结果。默认模板的营销槽位为 02–09；Shopee VN 普通店为 03–09。

### 1.3 不可破坏的生产约束

- 最终图片 Prompt 使用英文描述画面；需要实际出现在图片中的文字保持目标站点语言。
- 最终图片 Prompt 按 Unicode 字符计数，最多 `3500` 字符，包含换行和消费者文案。
- 每个 Prompt 只有一个主场景和一个主要动作。静态展示的主要动作为 `none`。
- 图片可见文字最多三行；每行只出现一次；允许零行。
- 标准白底图禁止新增文字、促销元素、水印、边框和无关图形。
- 商品身份、部件数量、排列、颜色、Logo、接口、结构和真实使用关系不得被场景设计覆盖。
- 竞品原图只允许进入 N1 的竞品观察分支；不得进入 DeepSeek、`gpt-image-2`、生产 Prompt、生成参考数组或导出包。下游只接收抽象 Style DNA。
- 允许合理推断，但所有推断必须进入 N3 台账并随图展示给审核人。推断不能冒充确认事实。
- 虚假价格、促销、认证、疗效、减重、美容前后对比、站外导流、未授权 IP 等高风险内容不得通过推断生成。
- 自动出图不等于自动审核。所有输出图必须人工审核后才能导出。

## 2. 公共数据契约

### 2.1 节点执行快照

每次节点执行都保存以下外层记录。节点业务输出放在 `output_snapshot`，不得原地覆盖历史记录。

```json
{
  "snapshot_id": "uuid",
  "trace_id": "uuid",
  "cluster_id": 0,
  "slot_id": null,
  "node_id": "N1",
  "node_version": "2.1.0",
  "schema_version": "2.0.0",
  "prompt_template_version": "2.1.0",
  "model_id": "gpt-5-nano-2025-08-07",
  "model_contract_version": "apimart-account-contract-2026-07-30",
  "temperature": null,
  "parent_snapshot_ids": [],
  "input_hash": "sha256",
  "output_hash": "sha256",
  "input_snapshot": {},
  "output_snapshot": {},
  "status": "succeeded",
  "failure_class": null,
  "failure_code": null,
  "created_at": "2026-07-30T00:00:00+08:00"
}
```

公共要求：

- `input_snapshot` 保存实际参与本次执行的不可变业务输入，不保存 Secret、临时签名参数或可复用 Token。
- 图片只保存资产 ID、内容哈希、OSS 对象版本和本次供应商 URL 的脱敏快照；日志不打印 URL。
- 重新分析、换 Prompt、再生成、修改和失败简化都创建新快照。
- Schema 校验失败不保存为成功输出；必须保存失败类别和脱敏错误。

### 2.2 事实记录

```json
{
  "fact_id": "fact.color.001",
  "statement": "商品主体为哑光黑色",
  "fact_class": "confirmed",
  "confidence": 1.0,
  "evidence_refs": ["asset:12", "observation:uuid"],
  "risk_level": "low",
  "allowed_uses": ["identity", "visual_prompt", "consumer_copy"],
  "review_note": ""
}
```

`fact_class` 只能为：

- `confirmed`：用户明确资料、已验证 ERP 商品名或人工确认事实。
- `observed`：图片直接可见，但未被用户确认。
- `inferred`：根据多项证据合理推断，仍可能错误。

### 2.3 规则包

Prompt 不重复塞入所有市场规则全文。规则解析器先装载一个官网主规则套件，再叠加与当前平台、市场、店铺类型、类目和槽位匹配的覆盖规则。

```json
{
  "rule_bundle_id": "uuid",
  "platform": "tiktok_shop",
  "market": "US",
  "seller_tier": "general",
  "category_id": "all",
  "slot_id": "01",
  "base_suite": {
    "profile_id": "tiktok.global.2026-06",
    "version": "2026-06",
    "hash": "sha256",
    "source_type": "official"
  },
  "overlays": [
    {
      "profile_id": "tiktok.us.2026-06-15",
      "version": "2026-06-15",
      "hash": "sha256",
      "source_type": "official"
    }
  ],
  "resolved_rules": [
    {
      "rule_id": "tiktok.us.primary.pure_white",
      "severity": "HARD_PLATFORM",
      "prompt_directive": "Use a pure white background.",
      "source_ref": "tiktok.us.2026-06-15"
    }
  ],
  "advice_refs": [],
  "unverified_refs": [],
  "resolved_at": "2026-07-30T00:00:00+08:00",
  "hash": "sha256"
}
```

规则合并顺序：

1. 系统安全与 APIMart 技术约束。
2. 当前槽位最具体的 `HARD_MALL`。
3. 当前市场、类目、槽位的 `HARD_PLATFORM`。
4. 官网主规则套件的 `HARD_PLATFORM`。
5. `TECH_UPLOAD`。
6. `INTERNAL_BASELINE`。
7. `ADVICE`。

覆盖规则可以收紧主规则，不能放宽同级或更高等级硬规则。`UNVERIFIED` 不进入自动硬判定或官方合规声明；没有已验证官网规则时可使用 `INTERNAL_BASELINE` 继续生产，但结果仍只标记为内部基线并要求人工审核。

N4 和 N6 只接收当前槽位的 `resolved_rules[].rule_id` 与精简 `prompt_directive`。N7 接收完整规则包快照并承担最终判定。

### 2.4 `gpt-image-2` 请求快照

```json
{
  "model": "gpt-image-2",
  "n": 1,
  "prompt": "",
  "image_urls": [
    "https://uploaded-reference-url.example/primary"
  ],
  "size": "1:1",
  "resolution": "1k",
  "prompt_snapshot_id": "uuid",
  "rule_gate_snapshot_id": "uuid",
  "reference_asset_snapshots": [
    {
      "asset_id": 0,
      "role": "primary",
      "content_hash": "sha256",
      "object_version": "version"
    }
  ]
}
```

`image_urls` 不得使用 base64 或 `{ "url": "..." }` 对象。白底图使用一张主参考图和最多三张结构补充图；营销图在此基础上额外加入已归档白底图。竞品图永远不能出现在该数组中。

## 3. N1 逐图观察

### 3.1 执行器

- 模型：`gpt-5-nano-2025-08-07`
- 接口：APIMart Responses
- 粒度：每张图片单独执行，可并行
- 模式：`owned_product` 或 `competitor_style`

### 3.2 输入 JSON

```json
{
  "asset": {
    "asset_id": 12,
    "asset_kind": "owned_product",
    "content_hash": "sha256",
    "mime_type": "image/png",
    "width": 1200,
    "height": 1200
  },
  "product_context": {
    "product_name": "用户已确认商品名",
    "confirmed_points": [],
    "relation_type": "same_product_reference"
  },
  "image_input": {
    "type": "input_image",
    "url": "signed-runtime-url"
  }
}
```

竞品输入必须把 `asset_kind` 设为 `competitor_style`，并且不传入我方商品事实作为竞品商品识别依据。

### 3.3 输出 JSON

```json
{
  "asset_id": 12,
  "asset_kind": "owned_product",
  "image_role": "clean_product",
  "contains_target_product": true,
  "target_is_physical_product": true,
  "target_visibility": 96,
  "target_complete": true,
  "reference_quality": 93,
  "background_complexity": "low",
  "observed_identity": {
    "category_candidates": [],
    "dominant_colors": [],
    "overall_shape": "",
    "visible_material_cues": [],
    "logos_or_markings": [],
    "controls_ports_connectors": [],
    "distinctive_parts": [],
    "count_observations": [
      {
        "part": "",
        "count": null,
        "confidence": 0
      }
    ]
  },
  "observed_use_relationships": [],
  "non_target_objects": [],
  "package_or_text_clues": [],
  "conflicts_with_confirmed_points": [],
  "recommended_use": "reuse",
  "style_dna": null,
  "reason": ""
}
```

竞品分支必须将商品识别字段保持为空或 `null`，只输出以下白名单：

```json
{
  "asset_id": 99,
  "asset_kind": "competitor_style",
  "image_role": "competitor_style",
  "contains_target_product": false,
  "target_is_physical_product": false,
  "target_visibility": 0,
  "target_complete": false,
  "reference_quality": 0,
  "background_complexity": "medium",
  "observed_identity": null,
  "observed_use_relationships": [],
  "non_target_objects": [],
  "package_or_text_clues": [],
  "conflicts_with_confirmed_points": [],
  "recommended_use": "evidence_only",
  "style_dna": {
    "color_strategy": "",
    "lighting_strategy": "",
    "composition_strategy": "",
    "scene_density": "low",
    "visual_rhythm": "",
    "forbidden_to_copy": []
  },
  "reason": ""
}
```

### 3.4 核心系统提示词

```text
你是商品视觉证据观察器。一次只观察一张图片，不做身份归并、营销策划、事实推断或图片生成。

owned_product 模式：
1. 区分真实商品主体、包装、说明书、配件、人物、手、道具和其他商品。
2. 只记录当前图片直接可见的颜色、轮廓、结构、Logo、接口、按钮、数量和使用接触关系。
3. 包装文字只能作为线索，不能代替实物证据。
4. 与 confirmed_points 冲突时记录冲突，不自行裁决。
5. 不可见或不确定内容使用 null、空字符串或空数组，不得依据品类常识补全。
6. 图片观察不能自动升级为营销卖点。

competitor_style 模式：
1. 只提炼抽象色彩、光线、构图、场景密度和视觉节奏。
2. 不输出竞品品牌、包装文字、人物身份、独特插画、可识别版式或商品事实。
3. forbidden_to_copy 记录可能造成复刻、侵权或混淆的元素类型。

只输出符合指定结构的单个 JSON 对象，不输出 Markdown、解释或额外字段。所有 0–100 分数必须为整数。
```

### 3.5 失败处理

- Responses 无文本、JSON 非法或 Schema 不通过：使用同一图片和同一输入做一次格式修复重试。
- 第二次仍失败：只把该素材标记为 `observation_failed`；其他素材继续。
- 图片无法解码、目标不可识别或无关：返回可验证的低质量结果，不让模型伪造观察。
- 模型未开通或未知：暂停该商品准备流程，不降级模型。
- 竞品分支出现品牌、OCR 原文或人物身份字段：Schema 拒绝，结果不得进入下游。

### 3.6 版本与快照字段

除公共字段外必须保存：

- `asset_snapshot_id`
- `asset_content_hash`
- `asset_kind`
- `responses_parser_version`
- `visual_observation_schema_version`
- `competitor_whitelist_version`

## 4. N2 身份归并

### 4.1 执行器

- 模型：`deepseek-v4-pro`
- 输入：N1 的我方商品观察结果，不接收竞品原图或竞品商品内容
- 输出：主外观、互补参考图、身份锁和商品档案

### 4.2 输入 JSON

```json
{
  "product_name": "用户已确认商品名",
  "confirmed_points": [],
  "relation_type": "same_product_reference",
  "observations": [],
  "max_supporting_images": 3
}
```

### 4.3 输出 JSON

```json
{
  "decision": "continue",
  "confidence": 91,
  "needs_input_reason": "",
  "product_profile": {
    "category": "",
    "primary_appearance": "",
    "shared_structure": [],
    "visible_fixed_counts": [],
    "verified_use_relationships": [],
    "included_items": [],
    "other_variants": [],
    "known_conflicts": []
  },
  "identity_lock": {
    "family_invariants": [],
    "primary_variant_attributes": [],
    "exact_component_constraints": [],
    "verified_hidden_or_internal_structure": [],
    "use_relationship_constraints": [],
    "must_not_change": []
  },
  "primary_asset_id": 12,
  "supporting_asset_ids": [13, 14],
  "standardization_mode": "reuse",
  "standardization_reason": ""
}
```

### 4.4 核心系统提示词

```text
你是商品身份归并器。根据商品名、确认资料和逐图观察结果，选择一个真实主外观并建立不可变身份锁。

1. 商品家族不变量包括核心品类、主体结构、工作或开合机制、关键部件拓扑、装配关系和共有内外结构。
2. 颜色、花纹、普通尺寸、拍摄角度、开合状态和内容物摆放是可变属性，不能单独证明商品身份冲突。
3. 主外观的颜色、纹理、Logo、装饰和外形只能来自 primary_asset_id 及与其明确一致的图片。
4. 其他 SKU 图片只能补充高置信度共有结构，不能把其可变属性混入主外观。
5. 精确数量只能来自 confirmed_points 或清晰、可可靠计数的图像观察。
6. 看不见的内部结构、接口、配件和背面结构不得补全。
7. supporting_asset_ids 最多三张，必须与主图互补，不能重复角度或包含竞品。
8. 只要存在有效图片和已确认商品名，优先 continue；只有无法确认真实目标商品或核心结构存在不可消解冲突时 needs_input。

只输出符合指定结构的 JSON。不要生成营销文案，不要创建新商品事实。
```

### 4.5 失败处理

- JSON 非法：同输入修复一次；仍失败则暂停该商品，不影响其他商品。
- 无有效实物图：`decision=needs_input`、`primary_asset_id=null`、补充图为空。
- 多个外观版本无法选择：优先匹配用户确认名称或资料；仍无法消解才 `needs_input`。
- supporting 图含竞品、无效资产或超过三张：Schema 拒绝并做一次修复。

### 4.6 版本与快照字段

- `observation_snapshot_ids`
- `identity_merge_schema_version`
- `identity_lock_version`
- `primary_asset_snapshot_id`
- `supporting_asset_snapshot_ids`
- `product_name_snapshot`
- `confirmed_points_snapshot`

## 5. N3 推断台账

### 5.1 执行器

- 模型：`deepseek-v4-pro`
- 目标：将确认事实、直接观察和合理推断明确分层
- 约束：推断可用于策划，但不得隐藏来源或绕过硬规则

### 5.2 输入 JSON

```json
{
  "product_name": "",
  "confirmed_points": [],
  "product_profile": {},
  "identity_lock": {},
  "owned_observations": [],
  "market_context": {
    "platform": "shopee",
    "market": "MY",
    "language": "ms-MY"
  }
}
```

### 5.3 输出 JSON

```json
{
  "ledger_version": "2.0.0",
  "facts": [
    {
      "fact_id": "fact.001",
      "statement": "",
      "fact_class": "confirmed",
      "confidence": 1.0,
      "evidence_refs": [],
      "risk_level": "low",
      "allowed_uses": ["identity", "visual_prompt", "consumer_copy"],
      "review_note": ""
    }
  ],
  "blocked_claim_topics": [
    "price",
    "promotion",
    "certification",
    "medical_efficacy",
    "weight_management",
    "beauty_before_after",
    "off_platform_redirect",
    "warranty",
    "country_of_origin"
  ],
  "unresolved_questions": [],
  "review_summary": {
    "confirmed_count": 0,
    "observed_count": 0,
    "inferred_count": 0,
    "high_risk_count": 0
  }
}
```

### 5.4 推断使用规则

- `confirmed`：在规则允许时可用于身份、画面和消费者文案。
- `observed`：默认可用于身份和画面；只有直接可见、非受管制且无歧义的内容才可进入消费者文案。
- `inferred`：必须给出置信度、证据和风险。低风险且 `confidence >= 0.80` 时可标记为 `consumer_copy_pending_review`；否则只能用于场景策划或人工提示。
- 价格、折扣、认证、疗效、减重、美容前后对比、产地、兼容性保证、容量或精确规格、安全保证、质保和站外导流不得仅靠推断进入消费者文案。
- 人群、生活方式和场景可合理推断，但不得把危险、受管制或专业用途商品自动改成轻松日常使用。

### 5.5 核心系统提示词

```text
你是商品事实与推断台账管理员。允许作合理推断，但必须显式标记 inferred，给出证据、置信度、风险和允许用途。

1. confirmed 只来自 confirmed_points 或明确人工确认。
2. observed 只来自图片直接可见内容，不得把视觉线索写成已确认性能。
3. inferred 可以覆盖可能的材质、功能、规格、卖点、人群和场景，但不得伪装成 confirmed 或 observed。
4. 高风险主题不能仅凭推断进入消费者文案。
5. 同一事实存在冲突时保留冲突，不静默选择更有营销价值的一方。
6. 每条事实都必须有稳定 fact_id 和 evidence_refs。
7. allowed_uses 只能从 identity、visual_prompt、scene_planning、consumer_copy、consumer_copy_pending_review、blocked 中选择。

只输出指定 JSON。不要写最终营销文案或图片 Prompt。
```

### 5.6 失败处理

- 输出缺少来源、置信度或风险：Schema 拒绝并修复一次。
- 模型把推断标成确认事实：确定性校验降级为 `inferred` 并重新计算允许用途，保留校验警告。
- 高风险推断进入 `consumer_copy`：强制改为 `blocked`。
- 无法形成推断不算失败；允许只有确认与观察事实。

### 5.7 版本与快照字段

- `identity_snapshot_id`
- `observation_snapshot_ids`
- `confirmed_points_snapshot_id`
- `inference_policy_version`
- `ledger_version`
- `fact_id_namespace_version`
- `ledger_hash`

## 6. N4 标准白底编译器

### 6.1 执行器

- 模型：`deepseek-v4-pro`
- 产物：当前模板中名称为 `Standard white-background product hero` 的槽位最终图片 Prompt 和参考图计划；默认顺序 01，Shopee VN 普通店顺序 02
- 下游：N7 通过后调用 `gpt-image-2`

### 6.2 输入 JSON

```json
{
  "slot": {
    "slot_id": "01",
    "role": "standard_white_background",
    "size": "1:1",
    "resolution": "1k"
  },
  "product_name": "",
  "product_profile": {},
  "identity_lock": {},
  "fact_ledger": {},
  "primary_asset_id": 12,
  "supporting_asset_ids": [13, 14],
  "resolved_rule_directives": [],
  "prompt_limits": {
    "max_characters": 3500,
    "max_text_lines": 0,
    "max_main_scenes": 1,
    "max_main_actions": 1
  }
}
```

### 6.3 输出 JSON

```json
{
  "slot_id": "01",
  "main_scene": "pure white commercial studio",
  "main_action": "none",
  "visible_text_lines": [],
  "prompt": "",
  "character_count": 0,
  "reference_plan": {
    "primary_asset_id": 12,
    "supporting_asset_ids": [13, 14],
    "include_completed_white_image": false
  },
  "fact_trace": [],
  "inference_trace": [],
  "rule_refs": [],
  "generation_parameters": {
    "model": "gpt-image-2",
    "n": 1,
    "size": "1:1",
    "resolution": "1k"
  },
  "review_required": true
}
```

### 6.4 核心系统提示词

```text
你是标准白底商品图编译器。只编译当前模板中语义为标准白底商品图的槽位，不策划营销场景。

最终英文 Prompt 必须：
1. 先声明主参考图优先，并准确锁定商品轮廓、颜色、Logo、接口、精确部件数量、排列、比例和已验证结构。
2. 只包含一个纯白商业摄影棚场景，主要动作为 none。
3. 商品完整、正面或最能验证结构的轻微三分之四视角、居中、无遮挡、不裁切。
4. 使用真实材质、柔和影棚光和自然接触阴影。
5. 禁止新增文字、促销、Logo、水印、边框、图标、人物、道具、包装、未确认配件和虚构结构。
6. 不得把 supporting 图片中的其他 SKU 可变属性混入主外观。
7. 不得依靠推断改变商品身份；inference_trace 应为空或只记录不影响身份的低风险展示判断。
8. 合并重复否定句，Prompt 不超过 3500 字符。

只输出指定 JSON。prompt 必须是可直接交给 gpt-image-2 的英文纯文本。
```

### 6.5 失败处理

- Prompt 超长、出现文字行、场景或动作计数超限：本节点做一次确定性压缩并重新校验。
- 主参考图无效或身份锁为空：阻断，不生成占位白底图。
- 规则要求与内部白底基线冲突：采用更严格要求并记录冲突；无法判定时交 N7 阻断。
- `gpt-image-2` 失败不在本节点重写 Prompt，由 N9 按失败类别处理。

### 6.6 版本与快照字段

- `identity_snapshot_id`
- `ledger_snapshot_id`
- `white_slot_template_snapshot_id`
- `rule_bundle_snapshot_id`
- `prompt_template_version`
- `compiled_prompt_version`
- `reference_plan_snapshot_id`

## 7. N5 八图营销导演

### 7.1 执行器

- 模型：`deepseek-v4-pro`
- 产物：营销槽位级策划，不输出最终图片 Prompt
- 默认：为槽位 02–09 输出八个不重复计划

市场模板若预占原图槽位或关闭某个营销槽位，N5 只为输入中的 `marketing_slots` 生成计划，不自行增减槽位。

### 7.2 输入 JSON

```json
{
  "product_name": "",
  "product_profile": {},
  "identity_lock": {},
  "fact_ledger": {},
  "marketing_slots": [
    {"slot_id": "02", "role": "second_angle_structure"},
    {"slot_id": "03", "role": "core_benefit"},
    {"slot_id": "04", "role": "material_detail"},
    {"slot_id": "05", "role": "usage_scene"},
    {"slot_id": "06", "role": "user_or_scale"},
    {"slot_id": "07", "role": "size_package_included"},
    {"slot_id": "08", "role": "platform_conversion"},
    {"slot_id": "09", "role": "supplemental_conversion"}
  ],
  "market_context": {
    "platform": "shopee",
    "market": "MY",
    "seller_tier": "general",
    "language": "ms-MY"
  },
  "seed_style": "",
  "style_dna_candidates": [],
  "rule_refs": [],
  "text_enabled": true
}
```

### 7.3 输出 JSON

```json
{
  "strategy_summary": "",
  "slot_plans": [
    {
      "slot_id": "02",
      "role": "second_angle_structure",
      "decision_task": "",
      "fact_refs": [],
      "inference_refs": [],
      "main_scene": "",
      "main_action": "none",
      "subject_relationship": "",
      "composition": "",
      "copy_intent": "",
      "text_mode": "up_to_3_lines",
      "localization_notes": [],
      "must_show": [],
      "must_avoid": []
    },
    {
      "slot_id": "03",
      "role": "core_benefit",
      "decision_task": "",
      "fact_refs": [],
      "inference_refs": [],
      "main_scene": "",
      "main_action": "none",
      "subject_relationship": "",
      "composition": "",
      "copy_intent": "",
      "text_mode": "up_to_3_lines",
      "localization_notes": [],
      "must_show": [],
      "must_avoid": []
    },
    {
      "slot_id": "04",
      "role": "material_detail",
      "decision_task": "",
      "fact_refs": [],
      "inference_refs": [],
      "main_scene": "",
      "main_action": "none",
      "subject_relationship": "",
      "composition": "",
      "copy_intent": "",
      "text_mode": "up_to_3_lines",
      "localization_notes": [],
      "must_show": [],
      "must_avoid": []
    },
    {
      "slot_id": "05",
      "role": "usage_scene",
      "decision_task": "",
      "fact_refs": [],
      "inference_refs": [],
      "main_scene": "",
      "main_action": "",
      "subject_relationship": "",
      "composition": "",
      "copy_intent": "",
      "text_mode": "up_to_3_lines",
      "localization_notes": [],
      "must_show": [],
      "must_avoid": []
    },
    {
      "slot_id": "06",
      "role": "user_or_scale",
      "decision_task": "",
      "fact_refs": [],
      "inference_refs": [],
      "main_scene": "",
      "main_action": "",
      "subject_relationship": "",
      "composition": "",
      "copy_intent": "",
      "text_mode": "up_to_3_lines",
      "localization_notes": [],
      "must_show": [],
      "must_avoid": []
    },
    {
      "slot_id": "07",
      "role": "size_package_included",
      "decision_task": "",
      "fact_refs": [],
      "inference_refs": [],
      "main_scene": "",
      "main_action": "none",
      "subject_relationship": "",
      "composition": "",
      "copy_intent": "",
      "text_mode": "up_to_3_lines",
      "localization_notes": [],
      "must_show": [],
      "must_avoid": []
    },
    {
      "slot_id": "08",
      "role": "platform_conversion",
      "decision_task": "",
      "fact_refs": [],
      "inference_refs": [],
      "main_scene": "",
      "main_action": "",
      "subject_relationship": "",
      "composition": "",
      "copy_intent": "",
      "text_mode": "up_to_3_lines",
      "localization_notes": [],
      "must_show": [],
      "must_avoid": []
    },
    {
      "slot_id": "09",
      "role": "supplemental_conversion",
      "decision_task": "",
      "fact_refs": [],
      "inference_refs": [],
      "main_scene": "",
      "main_action": "",
      "subject_relationship": "",
      "composition": "",
      "copy_intent": "",
      "text_mode": "up_to_3_lines",
      "localization_notes": [],
      "must_show": [],
      "must_avoid": []
    }
  ],
  "coverage_check": {
    "duplicate_scene_pairs": [],
    "duplicate_decision_tasks": [],
    "uncovered_slot_ids": [],
    "high_risk_inference_refs": []
  }
}
```

### 7.4 核心系统提示词

```text
你是八图营销导演。根据输入的槽位模板，为每个营销槽位设计一个独立购买决策任务，不生成最终图片 Prompt。

1. 每个槽位只有一个主场景和一个主要动作；不需要动作时使用 none。
2. 默认职责依次覆盖第二视角/结构、核心卖点、材质/细节、使用场景、模特或尺度、尺寸/包装/包含物、平台转化和补充转化。
3. 不得为了填满槽位重复场景、机位、人物姿态、文字意图或同一卖点。
4. fact_refs 和 inference_refs 必须引用 N3 已存在 ID。不得创建事实。
5. 推断可以参与策划，但高风险或 blocked 推断不能进入 copy_intent。
6. 人物、宠物或儿童只在与商品真实用途相符且规则允许时出现；动作必须表现正确接触、佩戴、握持、安装或支撑关系。
7. 内部结构、包装包含物、精确尺寸和配件只有存在证据时才能安排。
8. seed_style 和 Style DNA 只影响抽象视觉策略，不得复刻竞品品牌、文字、人物、插画或版式。
9. 本节点只引用规则 ID 和适用提示，不复制完整规则正文。
10. coverage_check 必须显式报告重复、空槽和高风险推断。

只输出指定 JSON，不输出最终消费者文案或 gpt-image-2 Prompt。
```

### 7.5 失败处理

- 槽位缺失、重复或越界：Schema 拒绝并修复一次。
- 多槽位主场景与决策任务高度重复：一次重排；仍重复则暂停 Prompt 生成并显示可编辑草稿。
- 某槽缺少事实：改为外观、结构或低风险场景，不编造数字和性能。
- Style DNA 含可识别竞品元素：删除污染字段并记录警告。

### 7.6 版本与快照字段

- `identity_snapshot_id`
- `ledger_snapshot_id`
- `output_template_snapshot_id`
- `seed_style_snapshot_id`
- `style_dna_snapshot_ids`
- `market_context_snapshot_id`
- `marketing_strategy_version`
- `slot_plan_snapshot_ids`

## 8. N6 本地化单槽编译器

### 8.1 执行器

- 模型：`deepseek-v4-pro`
- 粒度：每个营销槽位独立编译，可并行
- 产物：本地化消费者文案和可直接交给 `gpt-image-2` 的最终 Prompt

### 8.2 输入 JSON

```json
{
  "slot_plan": {},
  "product_name": "",
  "product_profile": {},
  "identity_lock": {},
  "fact_ledger": {},
  "market_context": {
    "platform": "shopee",
    "market": "TH",
    "language": "th-TH",
    "text_enabled": true
  },
  "resolved_rule_directives": [],
  "reference_plan": {
    "primary_asset_id": 12,
    "supporting_asset_ids": [13, 14],
    "completed_white_result_id": 301
  },
  "prompt_limits": {
    "max_characters": 3500,
    "max_text_lines": 3,
    "max_main_scenes": 1,
    "max_main_actions": 1
  }
}
```

### 8.3 输出 JSON

```json
{
  "slot_id": "05",
  "main_scene": "",
  "main_action": "",
  "visible_text_lines": [],
  "localized_copy": {
    "language": "th-TH",
    "lines": [],
    "source_fact_refs": [],
    "source_inference_refs": []
  },
  "prompt": "",
  "character_count": 0,
  "reference_plan": {
    "primary_asset_id": 12,
    "supporting_asset_ids": [13, 14],
    "completed_white_result_id": 301
  },
  "fact_trace": [],
  "inference_trace": [],
  "rule_refs": [],
  "generation_parameters": {
    "model": "gpt-image-2",
    "n": 1,
    "size": "1:1",
    "resolution": "1k"
  },
  "review_required": true
}
```

### 8.4 本地化映射

默认映射：

| 市场 | 默认消费者文案语言 |
| --- | --- |
| SG、PH、US | 当地自然电商英语 |
| MY | Bahasa Malaysia |
| TH | 泰语 |
| VN | 带完整变音符号的越南语 |
| ID | Bahasa Indonesia |
| TW | 台湾繁体中文 |
| BR | 巴西葡萄牙语 |

员工关闭图片文字或规则禁止新增文字时，`visible_text_lines=[]`，不得为了营销完整度强行补字。

### 8.5 最终 Prompt 固定结构

最终 Prompt 只保留五个紧凑段落：

1. 商品身份与参考图优先级。
2. 真实对象关系和一个主要动作。
3. 一个主场景、构图、光线、镜头和道具。
4. 最多三行需要实际显示的本地化文字及唯一文字区域。
5. 一句合并后的身份与结构保护。

不得堆叠多个场景、动作链、候选机位、候选文案或重复否定句。

### 8.6 核心系统提示词

```text
你是本地化单槽图片 Prompt 编译器。一次只编译一个营销槽位。

事实与推断：
1. 商品身份、精确数量、排列、颜色、Logo、接口、结构和真实使用关系以 identity_lock 为最高优先级。
2. 文案和画面事实只能引用 fact_ledger 中允许用途包含 consumer_copy、consumer_copy_pending_review、visual_prompt 或 scene_planning 的记录。
3. inferred 内容必须保留 inference_trace；禁止主题不能进入文案。

场景：
4. 最终 Prompt 只能有一个主场景和一个主要动作。
5. 人物或宠物必须正确使用商品，不能只是站在旁边。证据不足时采用中性静态展示。
6. 不可见内部结构、配件、承重关系和工作原理不得推断。

文字：
7. 根据 market_context 生成母语级电商短文案，不逐字翻译。
8. visible_text_lines 最多三行，每行必须短、自然、只出现一次。
9. text_enabled=false 或规则禁字时输出零行。
10. 图片控制指令用英文；需要显示的消费者文字保持目标语言，不翻回英文。
11. Prompt 必须明确只允许显示列出的文字和商品自身真实品牌/型号，不得生成字段名、站点代码或额外文字。

长度与输出：
12. 最终 Prompt 按 Unicode 字符计数不超过 3500。
13. 合并重复约束，删除无必要的镜头数字、装饰和多动作链。
14. 只输出指定 JSON；prompt 字段必须是可直接交给 gpt-image-2 的英文纯文本，允许嵌入目标语言文字行。
```

### 8.7 失败处理

- 文案语言不匹配、泰文/越南文/葡萄牙文字符缺失或超过三行：重新本地化一次。
- Prompt 超过 3500 字符：按“装饰 → 次要道具 → 冗余镜头参数 → 重复否定句”顺序确定性压缩，不删除身份锁、硬规则和文字。
- 计划含两个场景或动作：保留与 `decision_task` 最直接的一项。
- 规则禁字但输出文案：确定性清空文案并重新编译文字区域。
- 找不到白底结果时可以完成编译，但生成调度必须继续等待白底门禁。

### 8.8 版本与快照字段

- `slot_plan_snapshot_id`
- `identity_snapshot_id`
- `ledger_snapshot_id`
- `market_context_snapshot_id`
- `language_policy_version`
- `rule_bundle_snapshot_id`
- `localized_copy_version`
- `compiled_prompt_version`
- `reference_plan_snapshot_id`

## 9. N7 规则闸门

### 9.1 执行器

- 主执行器：后端确定性规则引擎
- 语义补充：`deepseek-v4-pro`
- 时机：每次付费生成或修订提交前
- 原则：DeepSeek 可以增加风险或解释，不能取消确定性硬阻断

### 9.2 输入 JSON

```json
{
  "slot_id": "05",
  "compiled_prompt_snapshot": {},
  "identity_snapshot": {},
  "fact_ledger_snapshot": {},
  "rule_bundle_snapshot": {},
  "output_template_snapshot": {},
  "reference_asset_snapshots": [],
  "submission_context": {
    "operation": "generate",
    "platform": "shopee",
    "market": "TH",
    "seller_tier": "general"
  }
}
```

### 9.3 输出 JSON

```json
{
  "decision": "pass",
  "hard_blocks": [],
  "semantic_risks": [],
  "warnings": [],
  "advice": [],
  "prompt_checks": {
    "character_count": 0,
    "text_line_count": 0,
    "main_scene_count": 1,
    "main_action_count": 1,
    "language_match": true,
    "identity_refs_valid": true,
    "fact_refs_valid": true,
    "inference_refs_disclosed": true,
    "reference_assets_valid": true
  },
  "resolved_rule_refs": [],
  "inference_disclosures": [],
  "review_required": true
}
```

### 9.4 确定性硬检查

出现任一项即 `decision=block`：

- Prompt 超过 3500 字符。
- 图片可见文字超过三行，或白底图存在新增文字。
- 存在两个以上主场景或主要动作。
- Prompt、文字或引用包含不存在的 fact ID、inference ID、规则 ID 或资产 ID。
- 竞品资产进入生成参考数组。
- 营销图未关联本商品已完成白底图，或调度时白底图未完成归档。
- 高风险推断进入消费者文案。
- 目标语言与市场映射不符，或规则要求禁字但仍存在文字。
- 违反身份锁中的精确数量、结构、颜色、Logo、接口、排列或真实使用关系。
- 违反已解析的 `HARD_PLATFORM`、`HARD_MALL`、系统安全或 APIMart 技术规则。
- 未授权第三方 IP、虚假价格/促销/认证/疗效/减重/美容前后对比、站外导流或规则明示禁止内容。
- `gpt-image-2` 请求模型、`n`、比例、分辨率或 `image_urls` 结构不在已验证契约内。

`ADVICE` 未满足只能产生 `warnings` 或 `advice`，不能单独阻断。`UNVERIFIED` 只进入人工复核提示。

### 9.5 核心系统提示词（语义补充）

```text
你是商品图规则语义审查器。确定性检查结果不可更改。

1. 只判断确定性规则难以覆盖的语义问题：虚假或夸大承诺、对象关系错误、隐含站外导流、未披露推断、危险使用、歧视或敏感内容。
2. 必须引用输入中的 rule_id、fact_id 或 inference fact_id。
3. 不得把 ADVICE 升级成平台硬规则，除非另有明确硬规则引用。
4. 不得因为文案营销性强就自动判违规；必须指出具体规则和具体内容。
5. 不得取消、降低或改写已有 hard_blocks。
6. 不确定时输出 semantic_risks 并要求人工复核，不虚构官方规则。

只输出 hard_blocks、semantic_risks、warnings 和引用 ID 的 JSON 片段。
```

### 9.6 失败处理

- 确定性规则引擎异常：默认阻断，不提交付费调用。
- DeepSeek 语义补充失败：保留确定性结论；存在未解析语义规则时阻断，否则带警告通过。
- 规则包版本缺失或哈希不符：阻断并重新解析规则包。
- 官方主规则与站点覆盖矛盾：采用更严格者并记录管理员检查项。
- `decision=pass` 只表示可以提交生成，不代表图片审核通过。

### 9.7 版本与快照字段

- `compiled_prompt_snapshot_id`
- `identity_snapshot_id`
- `ledger_snapshot_id`
- `rule_bundle_snapshot_id`
- `output_template_snapshot_id`
- `reference_asset_snapshot_ids`
- `deterministic_gate_version`
- `semantic_gate_prompt_version`
- `gate_decision_snapshot_id`

## 10. `gpt-image-2` 生成门禁与结果

本节不是第十个 Prompt 节点，而是 N4、N6、N8 或 N9 经 N7 通过后的唯一图片执行路径。

提交前：

1. 上传一张主参考图和最多三张结构补充图。
2. 营销图额外上传或复用本商品已归档白底结果。
3. 构造 URL 字符串数组，不传 base64 或 URL 对象。
4. 保存完整请求快照和幂等键。
5. `n=1`。

结果归档后：

```json
{
  "generation_id": 0,
  "slot_id": "05",
  "prompt_snapshot_id": "uuid",
  "rule_gate_snapshot_id": "uuid",
  "attempt": 1,
  "provider_task_id": "",
  "technical_status": "completed",
  "review_status": "pending_review",
  "result_asset_id": 0,
  "result_hash": "sha256",
  "export_eligible": false
}
```

人工审核通过后才将该具体版本的 `export_eligible` 置为 `true`。再生成、修改和失败重试都创建新 Generation，不覆盖旧结果。

## 11. N8 修改导演

### 11.1 执行器

- 模型：`deepseek-v4-pro`
- 图片执行：N7 通过后由 `gpt-image-2` 修订
- 目标：把圈选区域、标签和文字意见编译成最小差量指令

### 11.2 输入 JSON

```json
{
  "source_generation": {
    "generation_id": 0,
    "result_asset_id": 0,
    "prompt_snapshot_id": "uuid",
    "identity_snapshot_id": "uuid",
    "ledger_snapshot_id": "uuid",
    "rule_bundle_snapshot_id": "uuid"
  },
  "annotation": {
    "annotation_id": 0,
    "region": {
      "x": 0.1,
      "y": 0.1,
      "width": 0.3,
      "height": 0.3
    },
    "label": "structure",
    "instruction": "修正圈选处重复部件"
  },
  "current_slot_plan": {},
  "current_prompt": "",
  "fact_ledger": {},
  "identity_lock": {}
}
```

坐标使用 0–1 归一化值；后端先验证边界、面积和所属结果权限。

### 11.3 输出 JSON

```json
{
  "operation": "edit_region",
  "target_region": {
    "x": 0.1,
    "y": 0.1,
    "width": 0.3,
    "height": 0.3
  },
  "change_intent": "",
  "preserve_outside_region": true,
  "visible_text_lines": [],
  "delta_prompt": "",
  "character_count": 0,
  "fact_trace": [],
  "inference_trace": [],
  "rule_refs": [],
  "review_required": true
}
```

### 11.4 核心系统提示词

```text
你是商品图修改导演。把审核意见编译为只改圈选目标的差量 Prompt。

1. 先识别用户要求属于结构、文字、颜色、道具、人物、背景还是删除对象。
2. 只修改圈选区域及完成该修改所需的最小邻接区域。
3. 圈外商品身份、精确部件数量、颜色、Logo、接口、构图、人物、文字和光线保持不变。
4. 用户要求若与身份锁、确认事实或硬规则冲突，输出 blocked_change，不得照错执行。
5. 修改文字时仍最多三行，只能使用已有事实；不得顺带改写未被要求的其他文字。
6. 不得借修改新增配件、功能、内部结构、促销、认证或高风险推断。
7. delta_prompt 不重复完整原 Prompt，只写目标变化、必须保留项和必要硬规则；总长不超过 3500 字符。

只输出指定 JSON，不解释修改过程。
```

如修改被阻断，输出改为：

```json
{
  "operation": "blocked_change",
  "target_region": null,
  "change_intent": "",
  "preserve_outside_region": true,
  "visible_text_lines": [],
  "delta_prompt": "",
  "character_count": 0,
  "fact_trace": [],
  "inference_trace": [],
  "rule_refs": [],
  "review_required": true
}
```

### 11.5 失败处理

- 坐标非法、区域为空或跨越图像边界：后端拒绝，不调用模型。
- 指令含糊但可安全收敛：只做最小变化；无法确定目标时要求员工改写说明。
- 修改违反硬规则或身份锁：`blocked_change`。
- JSON 失败：同输入修复一次；仍失败则保留原图和原 Prompt。
- 修改结果仍须重新人工审核，旧审核通过版本不失效。

### 11.6 版本与快照字段

- `source_generation_snapshot_id`
- `source_result_asset_snapshot_id`
- `annotation_snapshot_id`
- `identity_snapshot_id`
- `ledger_snapshot_id`
- `rule_bundle_snapshot_id`
- `modification_prompt_version`
- `delta_prompt_snapshot_id`

## 12. N9 失败简化器

### 12.1 执行器

- 模型：`deepseek-v4-pro`
- 适用：Prompt 复杂度失败或允许安全重写的内容安全拒绝
- 不适用：网络、限流、供应商 5xx、轮询失败、提交状态未知

网络与限流继续使用原 Prompt 和原请求快照做有限传输重试。提交状态未知时进入 `submit_unknown`，不得自动重复 POST。

### 12.2 输入 JSON

```json
{
  "failure": {
    "failure_class": "prompt_complexity",
    "provider_code": "",
    "provider_message_sanitized": "",
    "submission_known_unaccepted": true
  },
  "original_prompt_snapshot": {
    "slot_id": "05",
    "prompt": "",
    "visible_text_lines": [],
    "fact_trace": [],
    "inference_trace": [],
    "rule_refs": []
  },
  "identity_lock": {},
  "fact_ledger": {},
  "rule_bundle_snapshot": {},
  "max_simplification_attempts": 1
}
```

### 12.3 输出 JSON

```json
{
  "decision": "retry_with_simplified_prompt",
  "simplified_prompt": "",
  "character_count": 0,
  "visible_text_lines": [],
  "preserved_fact_refs": [],
  "preserved_inference_refs": [],
  "preserved_rule_refs": [],
  "removed_elements": [],
  "safety_changes": [],
  "review_required": true
}
```

不能安全简化时：

```json
{
  "decision": "manual_prompt_change_required",
  "simplified_prompt": "",
  "character_count": 0,
  "visible_text_lines": [],
  "preserved_fact_refs": [],
  "preserved_inference_refs": [],
  "preserved_rule_refs": [],
  "removed_elements": [],
  "safety_changes": [],
  "review_required": true
}
```

### 12.4 核心系统提示词

```text
你是图片 Prompt 失败简化器。只处理 prompt_complexity 或允许重写的 content_safety_rejection。

必须保留：
1. 商品身份、精确数量、排列、主颜色、Logo、接口和真实使用关系。
2. 硬规则和允许显示的消费者文案；文案逐行原样保留且最多三行。
3. 一个主场景和一个主要动作。
4. 原 fact_trace、inference_trace 和 rule_refs 的可追溯性。

必须删除或合并：
5. 重复否定句、次要道具、冗余镜头数字、复杂装饰、多阶段动作、候选场景和候选机位。
6. 内容安全失败时删除非必要的敏感人物、危险动作或不当场景，但不得用同义词、隐喻或拼写变形规避安全规则。
7. 若敏感内容是商品本身、确认事实或主要销售意图且无法安全删除，返回 manual_prompt_change_required。
8. 简化后必须明显短于原 Prompt，且不超过 3500 字符。

只输出指定 JSON，不输出分析、Markdown 或新的营销事实。
```

### 12.5 失败处理

- `failure_class` 不在允许范围：不调用 N9，交传输重试或人工处理。
- 已执行一次简化仍失败：停止自动简化，要求人工换 Prompt。
- 内容安全拒绝无法在不改变商品事实的前提下解决：`manual_prompt_change_required`。
- 简化结果丢失身份锁、文字、事实引用或硬规则：Schema 拒绝，不提交。
- 简化输出必须重新经过 N7；N9 不能自行批准付费重试。

### 12.6 版本与快照字段

- `failure_snapshot_id`
- `original_prompt_snapshot_id`
- `original_generation_snapshot_id`
- `identity_snapshot_id`
- `ledger_snapshot_id`
- `rule_bundle_snapshot_id`
- `failure_simplifier_prompt_version`
- `simplified_prompt_snapshot_id`
- `simplification_attempt`

## 13. 人工审核与可追溯展示

每张生成结果的审核页必须同时展示：

- 实际使用的最终 Prompt 和 Prompt 版本。
- 主参考图、结构补充图和白底参考图快照。
- `confirmed`、`observed`、`inferred` 事实列表。
- 每项推断的置信度、风险、证据和使用位置。
- 官网主规则套件版本、站点覆盖版本和本图命中的规则 ID。
- 生成模型、比例、分辨率、attempt 和结果版本。
- 修改批注、差量 Prompt 和失败简化历史。

审核动作：

- `approve`：只批准当前结果版本，可进入导出选择。
- `request_change`：创建 N8 输入和新版本，不覆盖当前结果。
- `reject`：保留历史，不可导出。

自动规则闸门通过、供应商技术成功或员工曾批准旧版本，都不能替代当前版本的人工审核。

## 14. 最小验收清单

1. N1 对我方图输出身份证据，对竞品图只输出白名单 Style DNA。
2. N2 最多选择一张主图和三张结构补充图，且不混合其他 SKU 可变属性。
3. N3 能保存 `confirmed / observed / inferred`，高风险推断不能进入文案。
4. N4 产出无文字白底 Prompt，长度不超过 3500。
5. N5 默认输出八个不重复营销槽位计划。
6. N6 每槽只有一个场景、一个动作、最多三行目标语言文字。
7. N7 能阻断超长、禁字、身份冲突、语言错误、竞品参考和硬规则违规。
8. 白底未完成时槽位 02–09 不调用 `gpt-image-2`。
9. N8 只改圈选区域并创建新版本。
10. N9 不改写网络/限流失败，只简化允许的 Prompt 或安全拒绝。
11. 所有 `gpt-image-2` 请求使用 URL 字符串数组和 `n=1`。
12. 所有结果默认 `pending_review`，未人工通过不能导出。
