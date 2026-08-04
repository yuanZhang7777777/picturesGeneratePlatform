from datetime import date

from django.core.management.base import BaseCommand

from platform_app.models import OutputSlot, OutputTemplate, PromptNodeTemplate, RuleProfile
from platform_app.prompt_templates_v3 import PROMPT_OS_VERSION, PROMPT_TEMPLATES
from platform_app.template_policy import STANDARD_PRODUCT_HERO_NAME, STANDARD_PRODUCT_HERO_PURPOSE


GLOBAL_NAME = "Global marketplace baseline"
VERSION = "2026.08.04"
RULE_VERSION = "2026.07"
GLOBAL_TEMPLATE_KEY = "global-marketplace-baseline-template"
EIGHT_SLOT_TEMPLATE_KEY = "global-marketplace-eight-slot-template"
GLOBAL_RULE_KEY = "global-marketplace-baseline-rule"
GLOBAL_SLOTS = (
    (1, STANDARD_PRODUCT_HERO_NAME, STANDARD_PRODUCT_HERO_PURPOSE),
    (2, "Key benefit", "Show one verified product selling point"),
    (3, "Product detail", "Show material, construction, or detail evidence"),
    (4, "Function", "Show a verified product function"),
    (5, "Usage", "Show realistic product use"),
    (6, "Model or scale", "Show model, wearer, user, pet, or real-world scale without unverified claims"),
    (7, "Size, packaging, or contents", "Show verified size, packaging, or included items without inventing numbers"),
    (8, "Marketplace conversion", "Show marketplace-ready conversion creative in the target market language"),
)
REGIONAL_SITES = {
    "shopee": ("SG", "MY", "TH", "VN", "PH", "ID", "TW", "BR"),
    "tiktok": ("SG", "MY", "TH", "VN", "PH", "US"),
}
CHECKED_AT = date(2026, 7, 30)
GLOBAL_V2_RULE_KEY = "global-marketplace-prompt-os-v2-rule"
VN_GENERAL_TEMPLATE_KEY = "shopee-vn-general-nine-slot-v2-template"
VN_GENERAL_SLOTS = (
    (1, "Seller original product photo", "Preserve the actual uploaded or ERP product photo without AI generation"),
    (2, STANDARD_PRODUCT_HERO_NAME, STANDARD_PRODUCT_HERO_PURPOSE),
    (3, "Product structure", "Show a complementary structure or angle"),
    (4, "Product detail", "Show material, construction, or a useful detail"),
    (5, "Usage", "Show realistic product use"),
    (6, "User or scale", "Show the correct user, wearer, pet, or spatial scale"),
    (7, "Packaging or contents", "Show verified packaging or included items"),
    (8, "Local lifestyle", "Show a relevant Vietnamese lifestyle context"),
    (9, "Supplemental conversion", "Resolve one remaining purchase question without repetition"),
)
TIKTOK_BASE_SOURCE = "https://seller-sg.tiktok.com/university/essay?knowledge_id=7651420421883649&lang=en"
TIKTOK_US_SOURCE = "https://seller-us.tiktok.com/university/essay?knowledge_id=3196690250417921"
SHOPEE_BASE_SOURCE = "https://help.shopee.sg/portal/4/article/170850-Shopee-Community-Guidelines"
SHOPEE_VN_SOURCE = "https://help.shopee.vn/portal/4/article/77246"
SHOPEE_TW_SOURCE = (
    "https://cdngarenanow-a.akamaihd.net/shopee/seller/seller_cms/"
    "7658bd13abe2ad617a834190a75ba1a5/%E8%9D%A6%E7%9A%AE%E5%95%86%E5%9F%8E%E4%B8%8A%E6%9E%B6%E8%A6%8F%E7%AF%84.pdf"
)
PROMPT_OS_NODES = {
    "N1": """
你是商品视觉证据观察器。一次只观察一张图片，不做身份归并、营销策划、事实推断或图片生成。

owned_product 模式：
1. 区分真实商品主体、包装、说明书、配件、人物、手、道具和其他商品。
2. 只记录当前图片直接可见的颜色、轮廓、结构、Logo、接口、按钮、数量和使用接触关系。
3. 包装文字只能作为线索，不能代替实物证据。
4. 与 confirmed_points 冲突时记录冲突，不自行裁决。
5. 不可见或不确定内容使用 null、空字符串或空数组，不得依据品类常识补全。
6. 图片观察不能自动升级为营销卖点。

competitor_style 模式只提炼抽象色彩、光线、构图、场景密度和视觉节奏；不得输出竞品品牌、包装文字、人物身份、独特插画、可识别版式或商品事实，并在 forbidden_to_copy 中记录不可复刻元素。

只输出符合调用方指定结构的单个 JSON 对象，不输出 Markdown、解释或额外字段。所有 0–100 分数必须为整数。
""".strip(),
    "N2": """
你是商品身份归并器。根据商品名、人工确认资料和逐图观察结果，选择一个真实主外观并建立不可变身份锁。

1. 商品家族不变量包括核心品类、主体结构、工作或开合机制、关键部件拓扑、装配关系和共有内外结构。
2. 颜色、花纹、普通尺寸、拍摄角度、开合状态和内容物摆放是可变属性，不能单独证明商品身份冲突。
3. 主外观的颜色、纹理、Logo、装饰和外形只能来自 primary_asset_id 及与其明确一致的图片。
4. 其他 SKU 图片只能补充高置信度共有结构，不能把其可变属性混入主外观。
5. 精确数量只能来自 confirmed_points 或清晰、可可靠计数的图像观察。
6. 看不见的内部结构、接口、配件和背面结构不得补全。
7. supporting_asset_ids 最多三张，必须与主图互补，不能重复角度或包含竞品。
8. 存在有效图片和已确认商品名时优先 continue；只有无法确认真实目标商品或核心结构存在不可消解冲突时才 needs_input。

只输出符合调用方指定结构的单个 JSON 对象，不输出 Markdown、解释、营销文案、图片 Prompt 或新商品事实。
""".strip(),
    "N3": """
你是商品事实与推断台账管理员。允许作合理推断，但必须显式标记 inferred，并给出证据、置信度、风险和允许用途。

1. confirmed 只来自 confirmed_points 或明确人工确认。
2. observed 只来自图片直接可见内容，不得把视觉线索写成已确认性能。
3. inferred 可以覆盖可能的材质、功能、规格、卖点、人群和场景，但不得伪装成 confirmed 或 observed。
4. 价格、折扣、认证、疗效、减重、美容前后对比、产地、兼容性保证、精确容量或规格、安全保证、质保和站外导流等高风险主题，不能仅凭推断进入消费者文案。
5. 同一事实存在冲突时保留冲突，不静默选择更有营销价值的一方。
6. 每条事实必须有稳定 fact_id、evidence_refs、confidence、risk_level 和 review_note。
7. allowed_uses 只能从 identity、visual_prompt、scene_planning、consumer_copy、consumer_copy_pending_review、blocked 中选择。

只输出符合调用方指定结构的单个 JSON 对象，不输出 Markdown、解释、最终营销文案或图片 Prompt。
""".strip(),
    "N4": """
你是标准白底商品图编译器。只编译当前模板中语义为标准白底商品图的槽位，不策划营销场景。

最终中文白底提示词必须：
1. 声明主参考图优先，准确锁定商品轮廓、颜色、Logo、接口、精确部件数量、排列、比例和已验证结构。
2. 只包含一个纯白商业摄影棚场景，主要动作为 none。
3. 商品完整、居中、无遮挡、不裁切，使用正面或最能验证结构的轻微三分之四视角。
4. 使用真实材质、柔和影棚光和自然接触阴影。
5. 禁止新增文字、促销、Logo、水印、边框、图标、人物、道具、包装、未确认配件和虚构结构。
6. 不得把 supporting 图片中其他 SKU 的颜色、纹理、装饰等可变属性混入主外观。
7. 不得依靠推断改变商品身份；inference_trace 只能记录不影响身份的低风险展示判断。
8. 合并重复否定句，prompt 按 Unicode 字符计数不得超过调用方限制。

只输出符合调用方指定结构的单个 JSON 对象，不输出 Markdown 或解释。prompt 必须是可直接交给 gpt-image-2 的中文纯文本，visible_text_lines 必须为空。
""".strip(),
    "N5": """
你是商品套图营销导演。根据输入的实际营销槽位，为每个槽位设计一个独立购买决策任务，不生成最终图片 Prompt。

1. 每个槽位只有一个主场景和一个主要动作；不需要动作时使用 none。
2. 整套图应覆盖结构/第二视角、核心卖点、材质细节、真实使用、正确用户或尺度、尺寸/包装/包含物、本地生活方式与补充转化等不同决策问题，并服从输入槽位职责。
3. 不得重复场景族、机位、人物姿态、文字意图、主要动作或同一卖点。
4. fact_refs 和 inference_refs 必须引用事实台账中已经存在的 ID，不得创建事实。
5. 推断可以参与场景策划；高风险或 blocked 推断不能进入 copy_intent。
6. Usage/使用/功能/操作槽位必须写出真人、手部、身体局部、用户或宠物如何实际接触、拿起、佩戴、携带、摆放或操作商品；Model/scale/User/模特/比例/尺度槽位必须写出真人、手部、宠物或真实空间尺度线索，帮助买家判断大小、适配对象或使用尺度。
7. 人物、婴幼儿、成人或宠物只在与商品真实用途相符且平台规则允许时出现，动作必须表现正确接触、佩戴、握持、安装或支撑关系，不能只是站在旁边。
8. 内部结构、包装包含物、精确尺寸、配件和性能只有存在证据时才能安排。
9. seed_style 和 Style DNA 只影响抽象视觉策略，不得复刻竞品品牌、原文字、人物、插画或可识别版式。
10. coverage_check 必须明确报告重复、空槽和高风险推断。

只输出符合调用方指定结构的单个 JSON 对象，不输出 Markdown、解释、最终消费者文案或 gpt-image-2 Prompt。
""".strip(),
    "N6": """
你是本地化单槽图片 Prompt 编译器。一次只编译一个营销槽位。

事实与身份：
1. 商品身份、精确数量、排列、颜色、Logo、接口、结构和真实使用关系以 identity_lock 为最高优先级。
2. 文案和画面事实只能引用 fact_ledger 中允许用途包含 consumer_copy、consumer_copy_pending_review、visual_prompt 或 scene_planning 的记录。
3. inferred 内容必须保留 inference_trace；价格、折扣、认证、疗效、减重、绝对效果、站外导流等禁止主题不能进入文案。

场景与人物：
4. 最终 prompt 只能有一个主场景和一个主要动作。
5. 人物、婴幼儿、成人或宠物必须符合商品目标消费者并正确使用商品，不能只是站在旁边；slot_plan.subject_plan.person_presence 写了真人、手部、身体局部、用户、宠物、模特、比例或尺度时，display_prompt 必须完整保留这层关系。
6. 不可见内部结构、配件、承重关系和工作原理不得推断。

本地化文字：
7. 根据 market_context 生成母语级电商短文案，不逐字翻译。
8. visible_text_lines 最多三行，每行短、自然、只出现一次。
9. text_enabled=false 或平台规则禁字时必须输出零行。
10. 图片控制指令用中文正式导演稿；消费者可见文字保持目标语言，不翻成英文。
11. display_prompt 必须明确写出画面可见文字逐字渲染哪些内容，不得生成字段名、站点代码、乱码或额外促销文字。

输出：
12. display_prompt 按 Unicode 字符计数不得超过调用方限制。
13. 合并重复约束，删除无必要的镜头数字、装饰、多场景和多动作链。
14. 只输出符合调用方指定结构的单个 JSON 对象，不输出 Markdown 或解释；字段只保留 slot_id、slot_order、display_prompt，不得输出英文翻译稿、回译、自评过程或 JSON 版式块。
""".strip(),
    "N7": """
你是商品图规则语义审查器。后端确定性检查结果不可更改，你只能补充确定性规则难以覆盖的语义风险。

1. 检查虚假或夸大承诺、对象关系错误、隐含站外导流、未披露推断、危险使用、歧视或敏感内容。
2. 每个结论必须引用输入中的 rule_id、fact_id 或 inference fact_id。
3. 不得把平台差异、文案质量、身份不完整、构图风险或 ADVICE 升级成生图阻断；这些只写 semantic_risks/warnings，交给人工审核。
4. 只有价格/折扣、虚假促销、未验证认证/奖项、医疗疗效、减重、美容前后对比、100%/绝对效果、站外导流、未授权 IP、危险、色情、暴力、仇恨或儿童安全风险可以新增 hard_blocks。
5. 不得因为文案营销性强就自动判违规；必须指出具体规则和具体内容。
6. 不得取消、降低、删除或改写确定性引擎已有真正 hard_blocks。
7. 不确定时加入 semantic_risks 并要求人工复核，不虚构官方规则。
8. pass 只表示允许提交付费生成，不代表图片质量已人工认可。

只输出符合调用方指定结构的单个 JSON 对象，不输出 Markdown、解释或新的平台规则。
""".strip(),
    "N8": """
你是商品图修改导演。把审核圈选、问题标签和文字意见编译为只改目标区域的最小差量 Prompt。

1. 先识别修改属于结构、文字、颜色、道具、人物、背景还是删除对象。
2. 只修改圈选区域及完成目标所需的最小邻接区域。
3. 圈外商品身份、精确部件数量、颜色、Logo、接口、构图、人物、文字和光线保持不变。
4. 用户要求与身份锁、确认事实或硬规则冲突时输出 blocked_change，不得照错执行。
5. 修改文字时仍最多三行，只能使用已有事实，不得顺带改写未被要求的其他文字。
6. 不得借修改新增配件、功能、内部结构、促销、认证或高风险推断。
7. delta_prompt 不重复完整原 Prompt，只写目标变化、必须保留项和必要硬规则，并按 Unicode 字符计数不超过调用方限制。
8. 修改结果必须创建新版本并重新人工审核，不能声明原图已被覆盖或自动通过。

只输出符合调用方指定结构的单个 JSON 对象，不输出 Markdown、解释或修改过程。
""".strip(),
    "N9": """
你是图片 Prompt 失败简化器。只处理 prompt_complexity 或允许安全重写的 content_safety_rejection；不得处理网络、限流、供应商 5xx、轮询失败或提交状态未知。

必须保留：
1. 商品身份、精确数量、排列、主颜色、Logo、接口和真实使用关系。
2. 硬规则和允许显示的消费者文案；文案逐行原样保留且最多三行。
3. 一个主场景和一个主要动作。
4. 原 fact_trace、inference_trace 和 rule_refs 的可追溯性。

必须删除或合并：
5. 重复否定句、次要道具、冗余镜头数字、复杂装饰、多阶段动作、候选场景和候选机位。
6. 内容安全失败时删除非必要的敏感人物、危险动作或不当场景，不得用同义词、隐喻或拼写变形规避安全规则。
7. 若敏感内容是商品本身、确认事实或主要销售意图且无法安全删除，返回 manual_prompt_change_required。
8. 简化后必须明显短于原 prompt，并按 Unicode 字符计数不超过调用方限制。
9. 简化结果必须重新经过 N7，N9 不能自行批准付费重试。

只输出符合调用方指定结构的单个 JSON 对象，不输出分析、Markdown、解释或新的营销事实。
""".strip(),
}


def rule(
    rule_id,
    market,
    severity,
    requirement,
    directive,
    source_url,
    *,
    source_date="",
    seller_tier="general",
    slots=("cover", "gallery"),
    categories=("all",),
):
    return {
        "rule_id": rule_id,
        "market": market,
        "seller_tier": seller_tier,
        "category_scope": list(categories),
        "slot_scope": list(slots),
        "severity": severity,
        "requirement": requirement,
        "prompt_directive": directive,
        "source_url": source_url,
        "source_date": source_date,
        "checked_at": CHECKED_AT.isoformat(),
        "verification_status": "verified",
    }


def tiktok_sea_rules(market, source_url, source_date):
    prefix = f"tiktok.{market.lower()}"
    return [
        rule(
            f"{prefix}.gallery.truthful",
            market,
            "HARD_PLATFORM",
            "Images must accurately and consistently represent the product sold.",
            "Preserve the supplied product identity; do not invent a different color, structure, included item, or function.",
            source_url,
            source_date=source_date,
        ),
        rule(
            f"{prefix}.gallery.square_count",
            market,
            "TECH_UPLOAD",
            "Use square images at least 600 × 600 pixels, with no more than 9 images.",
            "Compose a square marketplace image with the complete product clearly visible.",
            source_url,
            source_date=source_date,
        ),
        rule(
            f"{prefix}.cover.solid_complete",
            market,
            "HARD_PLATFORM",
            "The primary image must show the complete product on a solid background; white is recommended.",
            "For the cover use a clean solid background, show the complete product, and keep the product dominant.",
            source_url,
            source_date=source_date,
            slots=("cover",),
        ),
        rule(
            f"{prefix}.claims.no_false_or_absolute",
            market,
            "HARD_PLATFORM",
            "False price, discount, certification, absolute efficacy, medical, beauty, or weight-loss claims are prohibited.",
            "Do not add price, discount, award, certification, medical, beauty, weight-loss, instant-result, or absolute claims.",
            source_url,
            source_date=source_date,
        ),
        rule(
            f"{prefix}.gallery.no_external_contact",
            market,
            "HARD_PLATFORM",
            "Images must not redirect customers with QR codes, websites, phone numbers, or external contact details.",
            "Do not add QR codes, URLs, phone numbers, social handles, or off-platform contact information.",
            source_url,
            source_date=source_date,
        ),
    ]


class Command(BaseCommand):
    help = "Seed the global marketplace baseline and regional draft placeholders."

    def handle(self, *args, **options):
        for node_name, template_data in PROMPT_TEMPLATES.items():
            template, created = PromptNodeTemplate.objects.get_or_create(
                node_name=node_name,
                version=PROMPT_OS_VERSION,
                defaults={
                    "status": PromptNodeTemplate.Status.PUBLISHED,
                    "instruction": template_data["instruction"],
                    "user_message_template": template_data["user_message_template"],
                    "output_schema": template_data["output_schema"],
                },
            )
            if not created:
                template.status = PromptNodeTemplate.Status.PUBLISHED
                template.instruction = template_data["instruction"]
                template.user_message_template = template_data["user_message_template"]
                template.output_schema = template_data["output_schema"]
                template.save(
                    update_fields=[
                        "status",
                        "instruction",
                        "user_message_template",
                        "output_schema",
                        "updated_at",
                    ]
                )
            PromptNodeTemplate.objects.filter(
                node_name=node_name,
                status=PromptNodeTemplate.Status.PUBLISHED,
            ).exclude(version=PROMPT_OS_VERSION).update(status=PromptNodeTemplate.Status.RETIRED)
        PromptNodeTemplate.objects.filter(
            node_name__in=("N5", "N6", "N7"),
            status=PromptNodeTemplate.Status.PUBLISHED,
        ).update(status=PromptNodeTemplate.Status.RETIRED)
        template, created = OutputTemplate.objects.get_or_create(
            seed_key=GLOBAL_TEMPLATE_KEY,
            defaults={
                "platform": "global",
                "site": "",
                "name": GLOBAL_NAME,
                "version": VERSION,
                "status": OutputTemplate.Status.PUBLISHED,
                "default_size": "1:1",
                "default_resolution": "1k",
            },
        )
        if not created and (template.version != VERSION or template.slots.count() != len(GLOBAL_SLOTS)):
            template, _ = OutputTemplate.objects.get_or_create(
                seed_key=EIGHT_SLOT_TEMPLATE_KEY,
                defaults={
                    "platform": "global",
                    "site": "",
                    "name": GLOBAL_NAME,
                    "version": VERSION,
                    "status": OutputTemplate.Status.PUBLISHED,
                    "default_size": "1:1",
                    "default_resolution": "1k",
                },
            )
        for order, name, purpose in GLOBAL_SLOTS:
            OutputSlot.objects.get_or_create(
                template=template,
                order=order,
                defaults={"name": name, "purpose": purpose},
            )

        RuleProfile.objects.get_or_create(
            seed_key=GLOBAL_RULE_KEY,
            defaults={
                "platform": "global",
                "site": "",
                "name": GLOBAL_NAME,
                "version": RULE_VERSION,
                "status": RuleProfile.Status.PUBLISHED,
                "rules": {
                    "review_required": False,
                    "localized_copy": True,
                    "no_unverified_claims": True,
                },
            },
        )
        RuleProfile.objects.get_or_create(
            seed_key=GLOBAL_V2_RULE_KEY,
            defaults={
                "platform": "global",
                "site": "",
                "name": "Global marketplace Prompt OS v2 baseline",
                "version": "2026.07.30",
                "status": RuleProfile.Status.PUBLISHED,
                "checked_at": CHECKED_AT,
                "rules": [
                    rule(
                        "internal.global.review.required",
                        "*",
                        "INTERNAL_BASELINE",
                        "Every generated image requires human approval before export.",
                        "Prepare the image for human review and do not treat generation as approval.",
                        "",
                    ),
                    rule(
                        "internal.global.hero.white",
                        "*",
                        "INTERNAL_BASELINE",
                        "Every standard product set contains a complete white-background product hero.",
                        "Show the complete, accurate product on pure white with no added promotional text or watermark.",
                        "",
                        slots=("cover",),
                    ),
                    rule(
                        "internal.global.inference.disclose",
                        "*",
                        "INTERNAL_BASELINE",
                        "Inferred product claims must be disclosed in the review ledger.",
                        "Use inferred details only when explicitly listed by the compiler; never add price, certification, or medical claims.",
                        "",
                    ),
                ],
            },
        )

        for platform, sites in REGIONAL_SITES.items():
            for site in sites:
                name = f"{platform.title()} {site} official rules pending"
                template, _ = OutputTemplate.objects.get_or_create(
                    seed_key=f"{platform}-{site.lower()}-template",
                    defaults={
                        "platform": platform,
                        "site": site,
                        "name": name,
                        "version": VERSION,
                        "status": OutputTemplate.Status.DRAFT,
                        "default_size": "1:1",
                        "default_resolution": "1k",
                    },
                )
                OutputSlot.objects.get_or_create(
                    template=template,
                    order=1,
                    defaults={
                        "name": STANDARD_PRODUCT_HERO_NAME,
                        "purpose": STANDARD_PRODUCT_HERO_PURPOSE,
                    },
                )
                RuleProfile.objects.get_or_create(
                    seed_key=f"{platform}-{site.lower()}-rule",
                    defaults={
                        "platform": platform,
                        "site": site,
                        "name": name,
                        "version": VERSION,
                        "status": RuleProfile.Status.DRAFT,
                    },
                )

        vietnam_template, _ = OutputTemplate.objects.get_or_create(
            seed_key=VN_GENERAL_TEMPLATE_KEY,
            defaults={
                "platform": "shopee",
                "site": "VN",
                "name": "Shopee VN general 1 source + 1 white + 7 marketing",
                "version": "2026.07.30",
                "status": OutputTemplate.Status.PUBLISHED,
                "default_size": "1:1",
                "default_resolution": "1k",
            },
        )
        for order, name, purpose in VN_GENERAL_SLOTS:
            OutputSlot.objects.get_or_create(
                template=vietnam_template,
                order=order,
                defaults={"name": name, "purpose": purpose},
            )

        shopee_vn_rules = [
            rule(
                "shopee.vn.general.actual_seller_photo",
                "VN",
                "HARD_PLATFORM",
                "At least one image must be an actual product photo taken by the seller, with the product occupying at least 40% of the image.",
                "Preserve one supplied seller or ERP source photo unchanged; do not synthesize an image that pretends to be the original photo.",
                SHOPEE_VN_SOURCE,
                slots=("source",),
            ),
            rule(
                "shopee.vn.general.image_language",
                "VN",
                "HARD_PLATFORM",
                "Image-background copy must be Vietnamese rather than a foreign language.",
                "Use Vietnamese for any newly generated visible copy.",
                SHOPEE_VN_SOURCE,
            ),
            rule(
                "shopee.vn.general.no_unrelated_contact",
                "VN",
                "HARD_PLATFORM",
                "Images must not include unrelated shop introductions, contact details, or payment information.",
                "Do not add shop introductions, phone numbers, URLs, QR codes, or payment information.",
                SHOPEE_VN_SOURCE,
            ),
        ]
        RuleProfile.objects.get_or_create(
            seed_key="shopee-vn-verified-20260730-rule",
            defaults={
                "platform": "shopee",
                "site": "VN",
                "name": "Shopee VN verified general listing rules",
                "version": "2026.07.30",
                "status": RuleProfile.Status.PUBLISHED,
                "source_url": SHOPEE_VN_SOURCE,
                "checked_at": CHECKED_AT,
                "rules": shopee_vn_rules,
            },
        )

        shopee_tw_rules = [
            rule(
                "shopee.tw.mall.cover.square_occupancy",
                "TW",
                "HARD_MALL",
                "Mall cover images must be square and the product must occupy at least 80% of the image.",
                "Use a square cover with the complete product occupying at least 80% of the canvas.",
                SHOPEE_TW_SOURCE,
                seller_tier="mall",
                slots=("cover",),
            ),
            rule(
                "shopee.tw.mall.no_simplified_chinese",
                "TW",
                "HARD_MALL",
                "New image copy must not use Simplified Chinese; original packaging text may remain.",
                "Use Traditional Chinese for any newly generated visible copy; preserve unavoidable original package text.",
                SHOPEE_TW_SOURCE,
                seller_tier="mall",
            ),
        ]
        RuleProfile.objects.get_or_create(
            seed_key="shopee-tw-mall-verified-20260730-rule",
            defaults={
                "platform": "shopee",
                "site": "TW",
                "name": "Shopee TW Mall verified image rules",
                "version": "2026.07.30",
                "status": RuleProfile.Status.PUBLISHED,
                "source_url": SHOPEE_TW_SOURCE,
                "checked_at": CHECKED_AT,
                "rules": shopee_tw_rules,
            },
        )

        RuleProfile.objects.get_or_create(
            seed_key="shopee-official-baseline-20260730-rule",
            defaults={
                "platform": "shopee",
                "site": "",
                "name": "Shopee official platform baseline",
                "version": "2026.07.30",
                "status": RuleProfile.Status.PUBLISHED,
                "source_url": SHOPEE_BASE_SOURCE,
                "checked_at": CHECKED_AT,
                "rules": [
                    rule(
                        "shopee.official.product_truth",
                        "*",
                        "HARD_PLATFORM",
                        "Images and claims must accurately represent the listed product and must not mislead buyers.",
                        "Preserve product identity and do not invent price, certification, function, quantity, color, or included items.",
                        SHOPEE_BASE_SOURCE,
                    ),
                    rule(
                        "shopee.official.safe_and_authorized",
                        "*",
                        "HARD_PLATFORM",
                        "Illegal, sexual, violent, hateful, infringing, or unauthorized content is prohibited.",
                        "Do not add third-party brands, copyrighted characters, unauthorized people, sexual content, violence, or hateful symbols.",
                        SHOPEE_BASE_SOURCE,
                    ),
                ],
            },
        )
        RuleProfile.objects.get_or_create(
            seed_key="tiktok-official-baseline-20260730-rule",
            defaults={
                "platform": "tiktok",
                "site": "",
                "name": "TikTok Shop official platform baseline",
                "version": "2026.03.16",
                "status": RuleProfile.Status.PUBLISHED,
                "source_url": TIKTOK_BASE_SOURCE,
                "checked_at": CHECKED_AT,
                "rules": tiktok_sea_rules("*", TIKTOK_BASE_SOURCE, "2026-03-16"),
            },
        )

        tiktok_us_rules = [
            rule(
                "tiktok.us.gallery.square_count",
                "US",
                "TECH_UPLOAD",
                "Use up to 9 square images at least 600 × 600 pixels.",
                "Compose a square marketplace image at 600 × 600 pixels or higher.",
                TIKTOK_US_SOURCE,
                source_date="2026-06-15",
            ),
            rule(
                "tiktok.us.cover.pure_white",
                "US",
                "HARD_PLATFORM",
                "The main product image must show the front physical view on a pure white background.",
                "Use a pure white cover and show the complete front physical view of the product.",
                TIKTOK_US_SOURCE,
                source_date="2026-06-15",
                slots=("cover",),
            ),
            rule(
                "tiktok.us.gallery.no_added_text",
                "US",
                "HARD_PLATFORM",
                "Product images must not include added logos, text, borders, watermarks, or background graphics.",
                "Do not add any logo, text, border, watermark, badge, or background graphic.",
                TIKTOK_US_SOURCE,
                source_date="2026-06-15",
            ),
            rule(
                "tiktok.us.gallery.no_digital_rendering",
                "US",
                "HARD_PLATFORM",
                "Placeholders and digital renderings of a product are not allowed.",
                "Block AI-generated listing output for TikTok Shop US official-compliance mode; require actual product photography.",
                TIKTOK_US_SOURCE,
                source_date="2026-06-15",
            ),
            rule(
                "tiktok.us.gallery.received_items_only",
                "US",
                "HARD_PLATFORM",
                "Images may include only what the customer will receive.",
                "Do not add accessories, gifts, packaging, or props that could be mistaken for included items.",
                TIKTOK_US_SOURCE,
                source_date="2026-06-15",
            ),
        ]
        RuleProfile.objects.get_or_create(
            seed_key="tiktok-us-verified-20260730-rule",
            defaults={
                "platform": "tiktok",
                "site": "US",
                "name": "TikTok Shop US verified listing rules",
                "version": "2026.06.15",
                "status": RuleProfile.Status.PUBLISHED,
                "source_url": TIKTOK_US_SOURCE,
                "checked_at": CHECKED_AT,
                "rules": tiktok_us_rules,
            },
        )

        self.stdout.write(self.style.SUCCESS("platform templates ready"))
