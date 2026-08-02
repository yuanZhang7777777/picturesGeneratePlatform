# Prompt OS 运行链路、任务定位与节点参数

## 任务不会靠顺序匹配

每个商品卡都有自己的 `cluster_id`。卡里的每张图片是 `asset_id`，实际文件位置是 `storage_path`。预备生成后，每个输出槽位会生成一条 `PromptVersion`，里面保存：

- `output_slot_id`：第几张输出图。
- `prompt_text`：最终给 `gpt-image-2` 的英文生图 Prompt。
- `input_snapshot / structured_output / evaluation`：当时的商品版本、平台、国家、规则、N7 结果。
- `reference_snapshot`：这张图实际要传给生图模型的我方参考图列表。

正式生成时，每张输出图是一条 `Generation`：

- 本地定位：`generation.id + cluster_id + output_slot_id + prompt_version_id`。
- 供应商定位：提交 APIMart 后写入 `provider_task_id`。
- 轮询回图：只按 `provider_task_id` 查供应商状态，再更新对应 `Generation` 行。

所以 A 图、B 图不会因为谁先返回而串图；返回顺序不参与定位。

## OSS 与模型识别的关系

OSS 只负责私有存储，不等于模型能直接读取图片。N1 识别的实际链路是：

`OSS storage_path → 后端临时文件 → APIMart /v1/uploads/images → APIMart /v1/responses input_image → gpt-5-nano-2025-08-07 文本 JSON`

如果 N1 返回空文本或坏 JSON，系统使用已上传图片作为商品参考继续，不把 `image_role`、`visible product identity`、`JSON`、`Schema` 等内部错误展示给普通员工。2026-08-02 线上 smoke：同一张“餐具套装”图片通过当前链路在约 12 秒返回了 `Wooden Cutlery Set with Spoon and Chopsticks in Case`，确认 nano 能识别已上传参考图。

## 多图同卡的真实规则

- 每张上传图默认一个商品卡。
- 用户把多张图拖进同一卡，表示这些图共同产出一套 `1 + 8`。
- 第一张只是优先主参考，不是强制决定全部生成。
- N1 会逐张看完同一卡全部图。
- N2 负责区分：
  - 同一外观的不同角度/细节；
  - 不同颜色；
  - 不同规格；
  - 不同款式。
- N5 给每个营销槽分配 `appearance_ids`：
  - 白底/总览可覆盖全部外观；
  - 单一使用场景、细节图、卖点图可以只用一个代表款；
  - 整套九张图最终覆盖所有目标外观。
- N2 的身份锁只用于防止商品变成别的商品。它不是场景、机位、文案、模特、道具或营销创意的硬限制。
- 明显不是当前商品的参考图只能作为干扰或风格线索处理，不得把里面的场景文字、面条、街景、包装等内容写进当前商品的 confirmed facts。

生成参考图规则：

- 有已完成白底图：营销图优先带白底图，再带当前槽位需要的商品参考图。
- 没有白底图：营销图直接带当前槽位需要的商品参考图，不等待白底。
- 不把竞品图传给生图模型。

## 节点输入输出

| 节点 | 模型 | 输入 | 输出 | 普通用户看到 |
|---|---|---|---|---|
| N1 素材观察 | `gpt-5-nano-2025-08-07` | 单张图片、`asset_id`、人工/ERP 商品名、补充资料 | 是否有商品、候选名、可见结构、颜色、材质、图片角色、可用性 | 只看到“识别中/识别异常/请换图或补充名称” |
| N2 商品身份 | `deepseek-v4-pro` | 全部 N1 观察、商品名、补充资料、图片顺序 | 商品名、身份锁、主参考、辅助参考、`target_appearances` | 商品身份卡，可修改 |
| N3 事实台账 | `deepseek-v4-pro` | N1/N2、人工事实 | `confirmed / observed / inferred` 事实、证据、用途边界 | 默认不展开，审核/高级编辑可看 |
| N4 白底 Prompt | `deepseek-v4-pro` | 商品身份、事实、平台规则、尺寸 | 标准白底产品图 Prompt | 第 1 张 Prompt |
| N5 套图策划 | `deepseek-v4-pro` | 商品身份、事实、平台/国家、项目/单品风格、8 个营销槽 | 每槽购买任务、场景、文案策略、`appearance_ids` | 中文槽位标题和策划 |
| N6 单图编译 | `deepseek-v4-pro` | 单槽 N5 计划、事实、语言、规则、参考图计划 | 英文生图 Prompt、锁定的目标语言文案 | 每张图 Prompt，可编辑 |
| N7 规则检查 | `deepseek-v4-pro` + 确定性规则 | 最终 Prompt、参考图、规则快照 | 通过/阻断/警告 | 只显示可操作中文错误 |
| N8 审核修改 | `deepseek-v4-pro` | 圈选坐标、问题标签、文字说明、原 Prompt | 修改差量 Prompt | 修改说明 |
| N9 失败简化 | `deepseek-v4-pro` | 供应商安全/复杂度失败、原 Prompt | 简化后的重试 Prompt | “已简化后重试” |

实际 system Prompt 源文件是 `platform_app/prompt_templates_v3.py`，数据库发布后由 `PromptNodeTemplate` 保存版本。运行时发送的是完整模板，不是文档摘要。

## 最终生图 Prompt 语言边界

- `prompt_text` 的图片控制指令保持英文，方便 `gpt-image-2` 执行。
- 商品名、商品事实和身份卡里的中文只作为内部理解变量；非中文国家不得把这些中文变量渲染成图片标题或角标。
- 可见文案只允许来自 N6 的 `visible_text_lines / localized_copy.lines`。越南站等非中文语言若模型返回中文可见行，编译层会丢弃该行，只保留目标语言行。
- 没有可见文案时，最终 Prompt 必须明确“不要新增标题、商品名、标签、说明、乱码或水印”。
- 参考图只锁商品身份、包含物、款式和比例；营销图必须换新场景、机位、光线、背景和摆放，不复刻上传图的原构图。
- 只要商品名或可见商品身份成立，N5/N6 必须产出营销策略和目标语言文案；不得因为缺少规格、包装、材质或详细卖点就退回空文案。
- 包装/包含物图只有在商品事实或目标图里确有包装、配件或包含物证据时才安排。没有证据时，N5 必须改做 FAB、场景代入、情绪触发、拟人表达、身份表达、细节信任、礼物/陪伴/使用等购买任务。
- 最终给 `gpt-image-2` 的 Prompt 以正向描述为主：写清“要什么画面、什么主体、什么数量和什么文案”，不要反复列举“不要三根、不要多一根、不要缺少”等负向数量示例；风险和规则审查留在 N7 内部执行。

## 运行容错与耗时边界

- DeepSeek 文本节点单次等待 20 秒；空响应、坏 JSON 或缺少非关键字段时使用当前商品事实生成兜底结构，不让商品卡因为内部 Schema 错误卡死。
- `preparing` 超过 120 秒会回到待处理，下一轮 Prompt Worker 继续预备。
- 正式生成只复用当前配置下已通过 N7 的 `PromptVersion`，不再在创建 `Generation` 时二次跑 N7。
- `submitting` 且没有 `provider_task_id` 超过 600 秒会回到队列重新提交，用于恢复部署中断或网络提交中断。
- 生成提交前只锁业务表本行和必要依赖，不使用 Postgres 不支持的 nullable outer join `FOR UPDATE`；否则 worker 会崩溃并把任务留在 `submitting`。
- 同一 worker 进程内按 OSS `storage_path` 复用 APIMart 上传后的 image_url，避免 1+8 槽位重复上传同一参考图。
- `gpt-image-2` 单张图 80–120 秒属于正常后台生成区间；265–358 秒属于偏慢但不是必然失败。系统必须显示进度和可刷新状态，不能让用户误以为按钮没反应。

## 失败边界

应该阻断用户的情况只有这些：

- 所有图片都无法看到任何商品，且用户也没有补充商品名/用途。
- 平台硬规则禁止。
- 可见文案语言无法可靠生成。
- APIMart、OSS、ERP、登录或网络失败。

不再阻断的情况：

- N1 不确定具体品类。
- N2 认为人工名称和视觉识别不完全一致。
- 商品缺少规格、材质、容量、使用参数。
- 只能推断大致卖点。

这些情况继续生成：人工填写内容优先，图片作为参考，缺失卖点由 N5/N6 做低风险合理推断；最终必须人工审核。

不该直接展示给用户的内部错误：

- `image_role`
- `visible product identity`
- `evidence_refs`
- `JSON`
- `Schema`
- `string`
- `N2 may...`

这些只进日志或管理员排障，普通员工界面统一显示“系统识别异常，请重试预备生成”。
