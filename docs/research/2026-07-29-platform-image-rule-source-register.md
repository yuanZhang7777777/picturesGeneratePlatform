# 商品图规则官方来源登记册

更新：2026-07-30

用途：为 Shopee 与 TikTok Shop 的规则编译、Prompt 预检、图片 QA 和默认 `1+8` 九图套图提供可追溯的官方来源。本文是设计输入，不替代各站 Seller Center 的最新规则或法律意见。

## 使用约定

系统只维护两套官网主规则套件：

1. **Shopee 官方基线**：平台级真实性、知识产权与禁限售底线，加上已公开 Mall 文件反复出现的商品图要求。
2. **TikTok Shop 官方基线**：以当前可公开核对的 TikTok Shop US Product Listing Policy 为 listing 主规则，以商品上传指南、PDP 质量指南和 Content Policy 补充技术、优化与推广内容规则。

只有官方资料明确给出差异时才增加站点覆盖项。缺少当地公开资料的站点复用同平台官网基线，并把来源状态标记为 `OFFICIAL_FALLBACK`；这表示“采用平台官网公开规则作保守回退”，不表示已核实当地 Seller Center，也不得展示“官方自动合规”。

规则等级固定为：

| 等级 | 含义 | 默认系统动作 |
| --- | --- | --- |
| `HARD_PLATFORM` | 当前平台政策、服务条款或明确会触发下架/处罚的 listing 要求 | 生成前阻断或审核前必须修正 |
| `HARD_MALL` | 只适用于 Shopee Mall/Official Shop 的明确要求 | 仅在 `seller_tier=mall` 时阻断 |
| `TECH_UPLOAD` | 图片数量、尺寸、格式、文件大小等上传约束 | 导出/上传预检阻断 |
| `ADVICE` | 官方优化建议、Good 质量标准或“recommended/preferred”内容 | 告警或质量评分，不作为合规阻断 |
| `UNVERIFIED` | 旧版、页面冲突、动态页不可读或尚未核实的规则 | 不自动宣称合规，必要时人工复核 |
| `INTERNAL_BASELINE` | 本平台为统一生产而采用的更保守规则 | 可阻断内部生产，但不得冒充平台官网要求 |

来源状态另使用：

- `OFFICIAL_DIRECT`：当前规则有适用站点的官方直链。
- `OFFICIAL_LEGACY`：官方文件可访问，但发布日期较早，只能作为旧版证据。
- `OFFICIAL_FALLBACK`：当地资料缺失，复用同平台官网基线。

## Shopee 官方基线

### 官网主规则套件

| 规则 ID | 等级 | 适用范围 | 官网规则摘要 | 系统处理 |
| --- | --- | --- | --- | --- |
| `shopee.base.identity_match` | `HARD_PLATFORM` | 所有站点、所有槽位 | 图片、标题、规格、颜色、包装和实际售卖商品必须一致，不得误导，不得展示买家不会收到的商品或配件。 | 使用商品身份锁；商品类型、结构、颜色、数量或包含物冲突时阻断。 |
| `shopee.base.ip_rights` | `HARD_PLATFORM` | 所有站点、所有槽位 | 不得未经授权使用第三方商标、品牌图、版权素材、人物肖像或伪装官方关系。 | 未在权利清单中的第三方 Logo、角色、肖像和品牌素材进入人工审核。 |
| `shopee.base.prohibited_content` | `HARD_PLATFORM` | 所有站点、所有槽位 | 不得包含违法、色情露骨、暴力血腥、仇恨或禁限售商品相关内容。 | 视觉安全检测命中即阻断。 |
| `shopee.base.no_external_diversion` | `HARD_PLATFORM` | 有当地明确证据的站点 | 不得在图片中放入站外联系方式、其他平台 Logo、外链、二维码或支付信息。 | VN、ID、TW 直接阻断；其他站点使用内部保守规则，直到当地来源核实。 |
| `shopee.base.mall_image_set` | `HARD_MALL` | 已有公开 Mall 文件的站点 | 通常至少 3 张清晰、非重复、真实且专业的商品图；首图使用纯色背景、商品主体突出，不使用网格拼图、多框或非官方边框。 | Mall 套件检查数量、清晰度、感知去重、拼图和遮挡；站点比例由覆盖项决定。 |
| `shopee.base.mall_cover_person` | `HARD_MALL` | 已有公开 Mall 文件的站点 | 首图通常不得出现模特、人物、手脚或人物反射；服饰、美妆、运动等类目有例外。 | 默认无人物；只通过站点和类目白名单放行。 |
| `shopee.base.clean_cover` | `INTERNAL_BASELINE` | 所有站点第 1 槽 | 标准白底、正方形、商品完整、无促销文字、无水印、无边框、无拼图、无无关道具。 | 作为项目统一第 1 槽规则；不得标注为 Shopee 全站官方硬规则。 |
| `shopee.base.gallery` | `ADVICE` | 第 2–9 槽 | 使用不同角度、细节、尺寸、包装、用法和场景帮助买家理解商品；不得臆造事实。 | 由九槽模板分配，重复角度告警，身份冲突仍按硬规则阻断。 |

Shopee 没有可安全套用到八站的统一白底、主体占比、水印或文字硬规则。平台基线只承载共同底线；比例、语言、实拍和 Mall 例外放在下面的覆盖项中。

### 官方来源

| 站点/范围 | 官方来源 | 页面/文件日期 | 核对日期 | 证据用途 |
| --- | --- | --- | --- | --- |
| SG，全平台 | [Shopee Community Guidelines](https://help.shopee.sg/portal/4/article/170850-Shopee-Community-Guidelines) | 页面未显示精确发布日期 | 2026-07-30 | 违法、有害、误导、冒充与知识产权底线，`HARD_PLATFORM`。 |
| SG，全平台 | [Prohibited and Restricted Items Policy](https://help.shopee.sg/portal/4/article/77151) | 官方索引约 2026-07 | 2026-07-30 | 禁限售与 listing 删除/账号处罚，`HARD_PLATFORM`。 |
| MY，全平台 | [Shopee Terms of Service](https://help.shopee.com.my/portal/4/article/77215) | 页面未显示精确发布日期 | 2026-07-30 | 信息准确、不得误导及 listing 处置底线，`HARD_PLATFORM`。 |
| MY，商品页优化 | [Improve Your Product Detail Page for Successful Advertising](https://ads.shopee.com.my/learn/faq/502/104) | 页面未显示精确发布日期 | 2026-07-29 | 1:1、至少 1024×1024、真实商品、清晰白底、避免水印等，均为 `ADVICE`。 |
| MY，旧 Mall/Official Shop | [Listing Requirements](https://cdngarenanow-a.akamaihd.net/shopee/seller/seller_cms/d38d33a37423e15adf187c2b33b81813/OS%20Listing%20Requirements_07182019%20%281%29.pdf) | 2019-07-18 | 2026-07-30 | Mall 图片数量、白底、70% 占比和技术要求，`OFFICIAL_LEGACY`。 |
| TH，全平台 | [การคุ้มครองทรัพย์สินทางปัญญาของ Shopee](https://help.shopee.co.th/portal/4/article/80736-%5B%E0%B8%99%E0%B9%82%E0%B8%A2%E0%B8%9A%E0%B8%B2%E0%B8%A2%5D-%E0%B8%81%E0%B8%B2%E0%B8%A3%E0%B8%84%E0%B8%B8%E0%B9%89%E0%B8%A1%E0%B8%84%E0%B8%A3%E0%B8%AD%E0%B8%87%E0%B8%97%E0%B8%A3%E0%B8%B1%E0%B8%9E%E0%B8%A2%E0%B9%8C%E0%B8%AA%E0%B8%B4%E0%B8%99%E0%B8%97%E0%B8%B2%E0%B8%87%E0%B8%9B%E0%B8%B1%E0%B8%8D%E0%B8%8D%E0%B8%B2%E0%B8%82%E0%B8%AD%E0%B8%87-Shopee) | 页面未显示精确发布日期 | 2026-07-30 | 图片、描述与商品知识产权责任，`HARD_PLATFORM`。 |
| TH，Mall | [เหตุผลการระงับ/ลบสินค้าบน Shopee](https://cdngarenanow-a.akamaihd.net/shopee/seller/seller_cms/fdc574bbd038be85bf8981c98d39ec9e/%E0%B9%80%E0%B8%AB%E0%B8%95%E0%B8%B8%E0%B8%9C%E0%B8%A5%E0%B8%81%E0%B8%B2%E0%B8%A3%E0%B8%A3%E0%B8%B0%E0%B8%87%E0%B8%B1%E0%B8%9A_%E0%B8%A5%E0%B8%9A%E0%B8%AA%E0%B8%B4%E0%B8%99%E0%B8%84%E0%B9%89%E0%B8%B2%E0%B8%9A%E0%B8%99%20Shopee_Oct_2019.pdf) | 文件名日期 2019-10 | 2026-07-30 | 首图背景、遮挡、拼图、边框、人物、至少 3 张和去重的 Mall 执行码，`OFFICIAL_LEGACY`。 |
| TH，上传 | [Mass Upload Product](https://cdngarenanow-a.akamaihd.net/shopee/seller/seller_cms/f503595fd17c398cecdc64ef19e8c253/EDH-Mass%20Upload.pdf) | 页面未显示精确发布日期 | 2026-07-30 | JPG/JPEG/PNG、最大 2 MB、1:1 建议和最多 8 张附图，`TECH_UPLOAD`/`ADVICE`。 |
| VN，所有卖家 | [QUY ĐỊNH VỀ ĐĂNG BÁN SẢN PHẨM TRÊN SHOPEE](https://help.shopee.vn/portal/4/article/77246) | 2024-08-14 | 2026-07-30 | 实拍、40% 占比、越南语、图片一致性和站外导流，`HARD_PLATFORM`。 |
| VN，Mall | [QUY ĐỊNH ĐĂNG BÁN TẠI SHOPEE MALL](https://cdngarenanow-a.akamaihd.net/shopee/seller/help/vn/52b23ec307fde7fd666961c47b092321/VN%20Quy%20%C4%91%E1%BB%8Bnh%20%C4%91%C4%83ng%20b%C3%A1n%20Shopee%20Mall.pdf) | 文件未显示精确发布日期 | 2026-07-30 | 至少 3 张、首图 60%、Logo 与后续图规则，`HARD_MALL`。 |
| PH，全平台 | [Shopee Terms of Service](https://help.shopee.ph/portal/4/article/77272-Shopee-Terms-of-Service) | 页面未显示精确发布日期 | 2026-07-30 | listing 准确性、禁限售与处置底线，`HARD_PLATFORM`。 |
| PH，Mall | [SHOPEE MALL LISTING GUIDELINES](https://cdngarenanow-a.akamaihd.net/shopee/seller/seller_cms/b71e298c6c220f22ef6f08608dbe1bd8/Mall%20Listing%20Guidelines.pdf) | 公开索引约 2020 | 2026-07-30 | 至少 3 张、首图 60%、Logo 与后续图规则，`OFFICIAL_LEGACY`。 |
| ID，所有卖家 | [Peraturan Komunitas](https://help.shopee.co.id/portal/4/article/73507-Peraturan-Komunitas) | 2024-05-06 | 2026-07-30 | 图片与商品一致、语言、站外导流、色情和侵权规则，`HARD_PLATFORM`。 |
| ID，Mall | [PANDUAN DAFTAR PRODUK SHOPEE MALL](https://cdngarenanow-a.akamaihd.net/shopee/seller/seller_cms/f9fe5116c72b4bc8a54651cfdc4deeed/Panduan%20Daftar%20Produk%20Shopee%20Mall.pdf) | 文件未显示精确发布日期 | 2026-07-30 | 至少 3 张、首图占比、组合商品、Logo、图文和后续图规则，`HARD_MALL`。 |
| TW，所有卖家 | [蝦皮購物規範](https://help.shopee.tw/portal/4/article/77364-%5B%E6%A2%9D%E6%AC%BE%E8%88%87%E6%94%BF%E7%AD%96%5D-%E8%9D%A6%E7%9A%AE%E8%B3%BC%E7%89%A9%E8%A6%8F%E7%AF%84) | 页面未显示精确发布日期 | 2026-07-30 | 站外导流、真实信息、违法与侵权底线，`HARD_PLATFORM`。 |
| TW，Mall | [蝦皮商城商品上架規範](https://cdngarenanow-a.akamaihd.net/shopee/seller/seller_cms/7658bd13abe2ad617a834190a75ba1a5/%E8%9D%A6%E7%9A%AE%E5%95%86%E5%9F%8E%E4%B8%8A%E6%9E%B6%E8%A6%8F%E7%AF%84.pdf) | 文件未显示精确发布日期 | 2026-07-30 | 至少 3 张、80% 占比、正方形、繁体中文、有限水印和图文规则，`HARD_MALL`。 |
| BR，全平台 | [Termos de Serviço da Shopee](https://help.shopee.com.br/portal/4/article/77113) | 页面未显示精确发布日期 | 2026-07-30 | 误导、违法、侵权、未经授权营销和信息准确性，`HARD_PLATFORM`。 |
| BR，商品页优化 | [Melhore a página do seu produto para ter sucesso](https://ads.shopee.com.br/learn/faq/111/1462) | 页面未显示精确发布日期 | 2026-07-30 | 主体大于一半、白底、1:1、至少 1024×1024、避免水印和多角度展示，`ADVICE`。 |

### 站点覆盖项与回退

| 站点/店铺 | 来源状态 | 相对 Shopee 官方基线的覆盖项 |
| --- | --- | --- |
| SG 普通店/Mall | `OFFICIAL_FALLBACK` | 已确认平台级内容与禁限售底线；未公开核实当前首图比例、文字、水印和 Mall 构图细则，复用基线但不得宣称当地构图合规。 |
| MY 普通店 | `OFFICIAL_DIRECT` | 1:1、1024×1024、白底和避免水印只按 `ADVICE` 执行。 |
| MY Mall | `OFFICIAL_LEGACY` | 旧版要求至少 3 张、640×640/72dpi、首图白底、单商品、主体及道具至少 70%；发布前需 Seller Center 复核。 |
| TH 普通店 | `OFFICIAL_FALLBACK` | 复用平台基线；当前公开构图细则不足。 |
| TH Mall | `OFFICIAL_LEGACY` | 执行码禁止首图多色/图案背景、拼图、多框、遮挡、非官方边框和人物；至少 3 张且不重复。旧指南的 60% 占比保持 `UNVERIFIED`。 |
| VN 普通店 | `OFFICIAL_DIRECT` | listing 至少保留一张卖家实拍，实物占图至少 40%；ERP 原图只有在确认属于真实商品实拍时才可计入。背景附加文字使用越南语，不得放第三方联系方式。默认九槽改为“已确认实拍原图 + 标准白底图 + 7 张营销图”。 |
| VN Mall | `OFFICIAL_DIRECT` | 至少 3 张非重复图；首图商品至少 60%，Logo 通常不超过 10%；后续图商品及道具至少 50%。VN 普通店实拍底线仍保留。 |
| PH 普通店 | `OFFICIAL_FALLBACK` | 复用平台基线；当前公开普通店构图细则不足。 |
| PH Mall | `OFFICIAL_LEGACY` | 旧版至少 3 张；首图商品至少 60%、白色背景优先、Logo 左上不超过 10%；发布前需复核。 |
| ID 普通店 | `OFFICIAL_DIRECT` | 图片、名称、描述必须与实物一致；图片中的外部联系方式、其他平台标识和不当内容阻断。 |
| ID Mall | `OFFICIAL_DIRECT` | 至少 3 张；文字规则多次写首图商品至少 60%，但示例出现 80%，因此 `<60%` 阻断、`60%–80%` 人工复核；允许专业单场景多商品，禁止网格拼图。 |
| TW 普通店 | `OFFICIAL_DIRECT` | 站外联系方式与其他平台信息阻断；附加中文文字统一使用繁体中文作为 `INTERNAL_BASELINE`。 |
| TW Mall | `OFFICIAL_DIRECT` | 至少 3 张；首图商品至少 80%、正方形、不得黑白边；附加中文文字必须为繁体中文、不得使用简体中文，商品原包装文字走例外；可在左右下角放非遮挡小水印，文字/插图/配件合计小于 20%。 |
| BR 普通店/Mall | `OFFICIAL_FALLBACK` | 平台级硬规则已确认；主体大于一半、1024×1024、1:1、白底和避免水印只按 `ADVICE` 执行，未核实 Mall 构图细则。 |

## TikTok Shop 官方基线

### 官网主规则套件

TikTok Shop 当前公开且可完整核对的商品图片主规则来自 US Academy。它可作为 TikTok Shop 官网基线，但 US 以外站点只能标记 `OFFICIAL_FALLBACK`。

| 规则 ID | 等级 | 适用范围 | 官网规则摘要 | 系统处理 |
| --- | --- | --- | --- | --- |
| `tiktok.base.identity_match` | `HARD_PLATFORM` | 所有 listing 图片 | 图片必须准确、清晰地代表买家实际收到的商品，只展示订单包含物，不得误导或侵犯知识产权、个人权利。 | 商品身份、颜色、数量、包含物和权利校验失败即阻断。 |
| `tiktok.us.image_count` | `TECH_UPLOAD` | US listing | 至少 1 张、最多 9 张；JPG/JPEG/PNG；至少 600×600；单图最大 10 MB。 | 导出预检阻断；默认 `1+8` 数量与上限一致。 |
| `tiktok.us.cover` | `HARD_PLATFORM` | US 主图 | 正方形、纯白背景，展示商品正面实物和完整主体，客观直接，不得使用黑白图。 | 第 1 槽使用纯白、正面、完整、彩色商品图。 |
| `tiktok.us.no_added_text` | `HARD_PLATFORM` | US 主图和附图 | 所有商品图不得添加 Logo、文字、边框、水印，亦不得在商品上或背景中加入覆盖性图形。 | US 市场关闭新增文字，Prompt 与后处理均不得生成促销语、规格字、Logo、水印、边框或信息图。 |
| `tiktok.us.no_digital_rendering` | `HARD_PLATFORM` | US listing 图片 | 不允许占位图或商品 digital renderings。 | AI 生成商品图不得被标记为 US listing 官方合规图；见下方冲突门禁。 |
| `tiktok.us.gallery_angles` | `HARD_PLATFORM` | US 附图 | 附图展示正面、背面、侧面、细节和实际包含配件；不得重复同一角度。 | 第 2–9 槽去重并覆盖不同角度；场景和细节仍受禁字与实物规则约束。 |
| `tiktok.us.good_listing` | `ADVICE` | US PDP 质量 | Good 质量建议至少 5 张高分辨率图片、多角度和品类相关信息；首图背景建议白色。 | 作为质量评分，不替代 listing 硬规则。 |
| `tiktok.us.creator_aigc` | `HARD_PLATFORM` | US Creator 推广内容，不是 PDP listing 图片 | Content Policy 只禁止误导、冒充或违反其他政策的 AIGC，并非全面禁止 AIGC。 | 仅用于推广内容审核；不得据此放宽 listing 的 digital rendering 禁令。 |
| `tiktok.base.clean_cover` | `INTERNAL_BASELINE` | 非 US 回退站点第 1 槽 | 纯白、正面、完整商品、无文字/Logo/水印/边框/图形。 | 作为 TikTok Shop 保守回退；不得宣称已核实当地站点。 |

### 官方来源

| 适用范围 | 官方来源 | 页面日期 | 核对日期 | 证据用途 |
| --- | --- | --- | --- | --- |
| TikTok Shop US listing | [Product Listing Policy](https://seller-us.tiktok.com/university/essay?knowledge_id=3196690250417921) | 2026-06-15 | 2026-07-30 | listing 图片真实性、纯白主图、全图禁字/Logo/边框/水印/图形、600×600、digital rendering 禁令及处罚，`HARD_PLATFORM`。 |
| TikTok Shop US 上传 | [How to Add Products to Your Shop](https://seller-us.tiktok.com/university/essay?knowledge_id=6581713858676522&lang=en) | 2026-05-19 | 2026-07-30 | 至少 1 张、最多 9 张、格式、10 MB、分辨率和上传流程，`TECH_UPLOAD`；“avoid”类表述不覆盖 Product Listing Policy 的硬规则。 |
| TikTok Shop US PDP 质量 | [Product Detail Pages & Listing Quality Guidelines](https://seller-us.tiktok.com/university/essay?knowledge_id=481891871868714&lang=en) | 2026-07-08 | 2026-07-30 | 至少 5 张、多角度、清晰度和品类信息属于 Good 质量优化，`ADVICE`；该页也再次写明 digital renderings 不允许。 |
| TikTok Shop US Creator 内容 | [Content Policy](https://seller-us.tiktok.com/university/essay?knowledge_id=6837891779151617) | 2026-07-17 | 2026-07-30 | 推广内容的误导、侵权、医疗/价格宣称和 AIGC 边界，`HARD_PLATFORM`，但适用对象是 Creator promotional content。 |

### US 禁字与 digital rendering 冲突门禁

US Product Listing Policy 是更具体的商品详情页规则：

- 所有 listing 图片均不得添加文字、Logo、水印、边框或图形。因此 US 不是“主图禁字、附图可做营销信息图”，而是第 1–9 槽全部禁字。
- listing 图片不得使用 digital renderings。当前 `gpt-image-2` 生成结果属于数字生成图，不能标记为 TikTok Shop US 官方 listing 合规图。
- US Content Policy 对“不误导的 AIGC”并非全面禁止，但它针对 Creator 推广内容；不能用这条较宽规则覆盖 PDP listing 的明确禁令。

系统门禁：

1. `platform=tiktok_shop, market=US` 时，Prompt 编译器关闭所有新增文字、促销贴纸、Logo、水印、边框和信息图槽位。
2. 真实上传/ERP 原图可以进入 US listing 候选；AI 生成图只可作为内部创意预览或非 listing 用途，除非 TikTok Shop 后续官方文件明确给出商品 listing 的生成图例外。
3. US 模板不得显示“AI 生成商品图已官方合规”。若仍允许生成，规则结果必须标记 `UNVERIFIED` 并阻止自动导出为 US listing 包。

### 站点回退

| 站点 | 来源状态 | 处理 |
| --- | --- | --- |
| US | `OFFICIAL_DIRECT` | 使用上述 US listing、上传和质量规则；禁字与 digital rendering 门禁生效。 |
| 非 US 目标站点 | `OFFICIAL_FALLBACK` | 复用 TikTok Shop 官网基线的真实性、知识产权、安全内容和干净主图规则；当地数量、分辨率、文字、生成图和类目差异均标记 `UNVERIFIED`，不得宣称当地官方合规。 |

## 默认 `1+8` 九槽策略

1. **标准白底产品主图**：所有平台固定为第 1 槽；完整、真实、身份准确、无促销文字和水印。第 2–9 槽仅在第 1 槽技术完成后提交。
2. **商品第二角度**：结构、背面或配件。
3. **核心卖点**：只使用已确认事实；禁字市场关闭文字。
4. **材质/工艺细节**：不臆造材质、认证或性能。
5. **使用场景**：场景与消费者、类目和市场匹配。
6. **模特/比例展示**：只在站点与类目规则允许时使用。
7. **尺寸/包装/包含物**：只使用已核对规格与包装清单。
8. **平台转化图**：按已发布规则表达一个事实卖点。
9. **补充转化图**：使用方法或本地化卖点；禁止虚假效果、价格和未证实优惠。

覆盖优先级为：`站点直接硬规则 > 平台官网基线 > INTERNAL_BASELINE > 创意模板`。Shopee VN 普通店用“已确认实拍原图 + 标准白底图 + 7 张营销图”替换默认九槽；TikTok Shop US 不得把 AI 生成图自动导出为 listing 包。

用户可从管理员发布且 APIMart 支持的比例、分辨率和槽位组合中调整；默认 `1:1` 与 `1k` 只是内部生产参数，不是平台合规保证。

## 本地化与竞品边界

- 默认文字语言：SG/PH/US 英语；MY 马来语；TH 泰语；VN 越南语；ID 印尼语；TW 繁体中文；BR 巴葡。禁字规则优先于默认语言。
- 竞品分析只可复用画面层级、版式结构、镜头、颜色、光线和场景密度等抽象 Style DNA。
- 竞品原图仅可发送给批准的 `gpt-5-nano-2025-08-07` 视觉观察端点；不得传给 `gpt-image-2`、文本模型、生产 Prompt 或导出包，也不得复制商标、包装、人物肖像或原文案。

## 发布门槛与证据空白

- 管理员刘学城是平台规则与模板的唯一发布人。发布前保存来源 URL、页面/文件日期、核对日期、站点、店铺类型、规则等级、版本和适用槽位。
- `OFFICIAL_FALLBACK` 允许按保守基线继续生产，但界面和导出清单不得显示“目标站点官方合规”；当地直链核实后才能升级为 `OFFICIAL_DIRECT`。
- Shopee SG、BR 的当前 Mall 构图细则以及 SG/TH/PH 普通店构图细则未公开核实；MY、TH、PH 的部分 Mall 文件为旧版。
- Shopee ID Mall 的 60%/80% 占比、PH/VN 的小 Logo/水印例外、TH 上传资料中的比例文本存在冲突或提取歧义，保留人工门禁。
- TikTok Shop 非 US 站点的 listing 图片规则尚未逐站公开核实；不得把 US 页面直接描述成当地官方规则。
- TikTok Shop US 的 listing digital rendering 禁令与本平台 AI 商品图生产目标直接冲突。在官方给出明确例外前，只能生成内部预览，不得自动导出为 US listing 官方合规包。
- 当官方文档为 PDF/PPT 时，归档来源链接、文件哈希、提取日期和适用范围，不复制整份受版权保护的材料。
