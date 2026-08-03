import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.conf import settings
from django.test.utils import CaptureQueriesContext


pytestmark = pytest.mark.django_db


def strict_n1(payload):
    identity = {
        "dominant_colors": [],
        "visible_material_cues": [],
        "logos_or_markings": [],
        "controls_ports_connectors": [],
        "distinctive_parts": [],
        "count_observations": [],
        **payload["observed_identity"],
    }
    return {
        "observed_use_relationships": [],
        "non_target_objects": [],
        "package_or_text_clues": [],
        "conflicts_with_confirmed_points": [],
        "style_dna": None,
        "reason": "",
        **payload,
        "observed_identity": identity,
    }


def strict_n2(payload):
    return {
        **payload,
        "product_profile": {
            "shared_structure": [],
            "visible_fixed_counts": [],
            "verified_use_relationships": [],
            "included_items": [],
            "other_variants": [],
            "known_conflicts": [],
            **payload["product_profile"],
        },
        "identity_lock": {
            "family_invariants": [],
            "primary_variant_attributes": [],
            "exact_component_constraints": [],
            "verified_hidden_or_internal_structure": [],
            "use_relationship_constraints": [],
            **payload["identity_lock"],
        },
    }


def make_user():
    return get_user_model().objects.create_user(
        username="prompt-operator",
        password="long-enough-password",
    )


def make_cluster(
    batch,
    *,
    product_name="Infant feeding set",
    facts="BPA-free silicone cup",
    lock="Keep sage green cup and two handles",
):
    from platform_app.models import Asset, Cluster

    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="source.png",
        storage_path="originals/source.png",
        sha256="a" * 64,
        file_size=12,
        content_type="image/png",
    )
    cluster = Cluster.create_for_asset(batch, asset)
    cluster.product_name = product_name
    cluster.product_facts = facts
    cluster.identity_lock = lock
    cluster.save(update_fields=["product_name", "product_facts", "identity_lock"])
    return cluster


def creative_strategy(mode="fab_value", refs=("fact.name.001",)):
    return {
        "mode": mode,
        "source_fact_refs": list(refs),
        "user_job": "understand the product value",
        "consumer_tension": "the value is not obvious from a plain product view",
        "feature": "visible product structure",
        "advantage": "easier to understand before buying",
        "consumer_benefit": "buy with clearer expectations",
        "mental_simulation": "imagine using the product in a normal day",
        "emotional_shift": "from uncertain to confident",
        "product_voice": "",
        "identity_signal": "",
        "selection_reason": "supported by the referenced product fact",
    }


def n5_plan(slot_order, *, mode="fab_value", fact_refs=("fact.name.001",), appearance_ids=None):
    return {
        "slot_order": slot_order,
        "role": f"marketing role {slot_order}",
        "visual_theme": f"visual theme {slot_order}",
        "specific_moment": f"specific moment {slot_order}",
        "aesthetic_point_of_view": f"aesthetic point of view {slot_order}",
        "typography_direction": f"typography direction {slot_order}",
        "decision_task": f"decision task {slot_order}",
        "conversion_goal": f"conversion goal {slot_order}",
        "fact_refs": list(fact_refs),
        "inference_refs": [],
        "main_scene": f"scene {slot_order}",
        "main_action": f"action {slot_order}",
        "subject_relationship": f"relationship {slot_order}",
        "composition": f"composition {slot_order}",
        "copy_intent": f"copy intent {slot_order}",
        "text_mode": "up_to_3_lines",
        "visible_text_lines": [],
        "localization_notes": [],
        "must_show": [],
        "must_avoid": [],
        "scene_family": f"scene-family-{slot_order}",
        "environment": f"environment-{slot_order}",
        "camera": f"camera-{slot_order}",
        "appearance_ids": list(appearance_ids or []),
        "creative_strategy": creative_strategy(mode, fact_refs),
    }


def test_fallback_display_prompt_uses_concrete_visual_design_brief():
    from platform_app.services import _fallback_display_prompt

    prompt = _fallback_display_prompt(
        {
            "slot_order": 4,
            "visual_theme": "通勤前一秒的轻松陪伴",
            "specific_moment": "年轻通勤者弯腰系鞋带时顺手扶正黄色毛绒玩偶",
            "aesthetic_point_of_view": "低饱和玄关生活摄影，暖白侧窗光，玩偶绒毛清晰",
            "typography_direction": "左上角两行泰文标题，第一行 34px 粗体无衬线，第二行 22px 常规无衬线，深墨绿色，宽度约画面 38%",
            "composition": "玩偶占右前景 58%，帆布包和钥匙盘在左后方形成通勤语境",
            "camera": "平视略低机位，中近景浅景深",
            "main_scene": "浅木玄关换鞋凳旁",
            "main_action": "扶正毛绒玩偶",
        },
        ["ใช้ได้พอดีกับจังหวะชีวิต", "เรื่องเล็กง่ายขึ้น"],
    )

    assert "通勤前一秒的轻松陪伴" in prompt
    assert "年轻通勤者弯腰系鞋带时顺手扶正黄色毛绒玩偶" in prompt
    assert "34px" in prompt
    assert "22px" in prompt
    assert "左上角" in prompt
    assert "占右前景 58%" in prompt
    assert "浅木玄关换鞋凳旁" in prompt
    assert "ใช้ได้พอดีกับจังหวะชีวิต" in prompt
    assert "เรื่องเล็กง่ายขึ้น" in prompt
    assert "画面围绕参考图" not in prompt
    assert "生活里的小任务" not in prompt
    assert "不做技术图" not in prompt


def test_confirm_generation_snapshots_selected_market_template_rule_and_prompt_asset():
    """A generation must retain the selected configuration after it is later edited."""
    from platform_app.models import Batch, OutputSlot, OutputTemplate, RuleProfile
    from platform_app.services import confirm_generation

    user = make_user()
    template = OutputTemplate.objects.create(
        platform="shopee",
        site="US",
        name="US storefront",
        version="2026.07",
        default_size="1:1",
        default_resolution="1k",
    )
    slot = OutputSlot.objects.create(
        template=template,
        name="detail scene",
        order=1,
        purpose="Show the product in a tidy kitchen scene",
    )
    rule = RuleProfile.objects.create(
        platform="shopee",
        site="US",
        name="US rules",
        version="2026-07",
        status=RuleProfile.Status.PUBLISHED,
        rules={"no_text_overlay": True},
    )
    batch = Batch.objects.create(
        owner=user,
        name="US launch",
        platform="shopee",
        site="US",
        market="US",
        global_prompt="Natural commercial photography",
        output_template=template,
        rule_profile=rule,
        size="3:4",
        resolution="2k",
    )
    cluster = make_cluster(batch)

    generation = confirm_generation(batch, user)[0]

    assert generation.output_slot == slot
    assert generation.size == "3:4"
    assert generation.resolution == "2k"
    assert generation.template_snapshot["version"] == "2026.07"
    assert generation.template_snapshot["slot"]["purpose"] == "Show the product in a tidy kitchen scene"
    assert generation.rule_snapshot["version"] == "2026-07"
    assert generation.rule_snapshot["rules"] == {"no_text_overlay": True}
    assert generation.prompt_version.cluster == cluster
    assert generation.prompt_version.node_name == "slot_prompt"
    assert generation.prompt_version.template_version == "builtin-v1"
    assert generation.prompt_version.provider_model == settings.APIMART_PROMPT_MODEL
    assert generation.prompt_version.input_snapshot["market"] == "US"
    assert generation.prompt_version.evaluation["fact_policy"] == "traceable-inference"

    template.version = "mutated-template"
    template.save(update_fields=["version"])
    slot.purpose = "mutated purpose"
    slot.save(update_fields=["purpose"])
    rule.version = "mutated-rule"
    rule.rules = {"no_text_overlay": False}
    rule.save(update_fields=["version", "rules"])
    generation.refresh_from_db()

    assert generation.template_snapshot["version"] == "2026.07"
    assert generation.template_snapshot["slot"]["purpose"] == "Show the product in a tidy kitchen scene"
    assert generation.rule_snapshot == {
        "id": str(rule.id),
        "name": "US rules",
        "version": "2026-07",
        "status": "published",
        "platform": "shopee",
        "site": "US",
        "source_url": "",
        "checked_at": None,
        "rules": {"no_text_overlay": True},
        "resolved_rules": [],
    }


def test_compile_slot_prompt_uses_only_product_references_and_sanitized_style_dna():
    """Competitor metadata must never become source material for an image task."""
    from platform_app.models import Batch, CompetitorInsight, OutputSlot, OutputTemplate, RuleProfile
    from platform_app.services import compile_slot_prompt

    user = make_user()
    template = OutputTemplate.objects.create(
        platform="shopee",
        name="template",
        version="v8",
    )
    slot = OutputSlot.objects.create(
        template=template,
        name="kitchen scene",
        order=1,
        purpose="Show the product beside a breakfast table",
    )
    rule = RuleProfile.objects.create(
        platform="shopee",
        site="SG",
        name="SG rules",
        version="v3",
        status=RuleProfile.Status.PUBLISHED,
        rules={"background": "clean", "text": "forbidden"},
    )
    batch = Batch.objects.create(
        owner=user,
        name="baby set",
        platform="shopee",
        site="SG",
        market="SG",
        global_prompt="Use an honest catalogue style",
        output_template=template,
        rule_profile=rule,
        size="3:4",
        resolution="2k",
    )
    cluster = make_cluster(batch)
    CompetitorInsight.objects.create(
        cluster=cluster,
        style_dna={
            "composition": "top-left",
            "lighting": "soft daylight",
            "color": "warm neutral",
            "scene_density": "minimal",
            "camera": "eye level",
        },
    )
    CompetitorInsight.objects.create(
        cluster=cluster,
        style_dna={
            "lighting": "Nike campaign daylight",
            "color": "Limited sale copy",
            "camera": "portrait of a competitor model",
            "material": "competitor titanium coating",
        },
    )

    compiled = compile_slot_prompt(cluster, slot)

    assert compiled["target_consumer"] == "baby"
    assert compiled["model_persona"] == "baby"
    assert compiled["reference_snapshot"] == ["originals/source.png"]
    assert compiled["style_dna"] == {
        "composition": "top-left",
        "lighting": "soft daylight",
        "color": "warm neutral",
        "scene_density": "minimal",
        "camera": "eye level",
    }
    assert "Nike" not in compiled["prompt"]
    assert "Limited sale" not in compiled["prompt"]
    assert "competitor model" not in compiled["prompt"]
    assert "titanium coating" not in compiled["prompt"]
    assert "CompetitorInsight" not in compiled["prompt"]
    for expected in [
        "Infant feeding set",
        "BPA-free silicone cup",
        "Keep sage green cup and two handles",
        "honest catalogue style",
        "Market: SG",
        "Model persona: baby",
        "Creative requirements: not provided",
        "Scene:",
        "Grounding:",
        "Composition:",
        "Lighting:",
        "Material:",
        "Identity lock:",
        "Size: 3:4",
        "Resolution: 2k",
    ]:
        assert expected in compiled["prompt"]


def test_target_consumer_override_wins_over_infant_keyword():
    """An operator-selected audience must override the keyword fallback."""
    from platform_app.models import Batch, OutputSlot, OutputTemplate
    from platform_app.services import compile_slot_prompt

    user = make_user()
    template = OutputTemplate.objects.create(platform="shopee", name="template")
    slot = OutputSlot.objects.create(template=template, name="main", order=1, purpose="Main image")
    batch = Batch.objects.create(
        owner=user,
        name="override",
        platform="shopee",
        site="BR",
        market="BR",
        output_template=template,
    )
    cluster = make_cluster(batch, product_name="Baby care kit")
    cluster.target_consumer = "adult"
    cluster.save(update_fields=["target_consumer"])

    compiled = compile_slot_prompt(cluster, slot)

    assert compiled["target_consumer"] == "adult"
    assert "Model persona: adult" in compiled["prompt"]
    assert compiled["provider_model"] == settings.APIMART_PROMPT_MODEL
    assert "provider_model" not in compiled["prompt"]


def test_compiled_n6_prompt_sends_image_model_direct_creative_instruction():
    from platform_app.models import Batch, OutputSlot, OutputTemplate
    from platform_app.services import compile_slot_prompt

    user = make_user()
    template = OutputTemplate.objects.create(platform="shopee", name="template")
    slot = OutputSlot.objects.create(template=template, name="usage", order=5, purpose="Show realistic product use")
    batch = Batch.objects.create(
        owner=user,
        name="direct",
        platform="shopee",
        site="VN",
        market="VN",
        output_template=template,
    )
    cluster = make_cluster(batch, product_name="餐具套装")

    compiled = compile_slot_prompt(
        cluster,
        slot,
        slot_directive="Create a realistic breakfast table scene with the cutlery set as the main subject.",
        main_scene="breakfast table",
        main_action="adult hand uses the cutlery naturally",
        node_name="N6.generic",
    )

    assert compiled["prompt"].startswith("Create a realistic breakfast table scene")
    assert "Product name:" not in compiled["prompt"]
    assert "Slot purpose:" not in compiled["prompt"]
    assert "Rule " not in compiled["prompt"]
    assert "Use the supplied product reference images to understand product identity" in compiled["prompt"]


def test_vn_image_prompt_does_not_render_chinese_internal_product_name():
    from platform_app.models import Batch, OutputSlot, OutputTemplate
    from platform_app.services import compile_slot_prompt

    user = make_user()
    template = OutputTemplate.objects.create(platform="shopee", name="template")
    slot = OutputSlot.objects.create(template=template, name="usage", order=5, purpose="Show realistic product use")
    batch = Batch.objects.create(
        owner=user,
        name="vn",
        platform="shopee",
        site="VN",
        market="VN",
        output_template=template,
    )
    cluster = make_cluster(batch, product_name="餐具套装", facts="木质餐具套装")

    compiled = compile_slot_prompt(
        cluster,
        slot,
        slot_directive='Create a warm lifestyle scene for 餐具套装. Add title "餐具套装" and subtitle "Bộ dụng cụ ăn uống".',
        visible_text_lines=["餐具套装", "Bộ dụng cụ ăn uống"],
        main_scene="warm dining table",
        main_action="adult uses the cutlery set naturally",
        node_name="N6.shopee",
    )

    assert "餐具套装" not in compiled["prompt"]
    assert "木质餐具套装" not in compiled["prompt"]
    assert "Bộ dụng cụ ăn uống" in compiled["prompt"]
    assert compiled["input_snapshot"]["visible_text_lines"] == ["Bộ dụng cụ ăn uống"]
    assert "Create a new ecommerce composition" in compiled["prompt"]
    assert "For lifestyle/use scenes, make the functional product component" in compiled["prompt"]


def test_confirm_generation_keeps_prompt_override_as_extra_creative_requirements():
    """A creative override must not replace grounded product or identity constraints."""
    from platform_app.models import Batch, OutputSlot, OutputTemplate
    from platform_app.services import confirm_generation

    user = make_user()
    template = OutputTemplate.objects.create(platform="shopee", name="template")
    OutputSlot.objects.create(template=template, name="main", order=1, purpose="Main image")
    batch = Batch.objects.create(
        owner=user,
        name="creative",
        platform="shopee",
        site="SG",
        market="SG",
        output_template=template,
    )
    cluster = make_cluster(batch)
    cluster.prompt_override = "Use a calm breakfast mood"
    cluster.save(update_fields=["prompt_override"])

    generation = confirm_generation(batch, user)[0]

    assert "Creative requirements: Use a calm breakfast mood" in generation.prompt_text
    assert "BPA-free silicone cup" in generation.prompt_text
    assert "Keep sage green cup and two handles" in generation.prompt_text


def test_first_output_slot_enforces_a_standard_white_background_product_hero():
    """The first output cannot be repurposed into a promotional or lifestyle image."""
    from platform_app.models import Batch, OutputSlot, OutputTemplate
    from platform_app.services import compile_slot_prompt

    user = make_user()
    template = OutputTemplate.objects.create(platform="global", name="Custom template")
    slot = OutputSlot.objects.create(
        template=template,
        name="Promotional cover",
        order=1,
        purpose="Lifestyle campaign image with a sale headline",
    )
    batch = Batch.objects.create(owner=user, name="hero", output_template=template, global_prompt="Add a sale headline")
    cluster = make_cluster(batch)

    compiled = compile_slot_prompt(cluster, slot)

    assert "Standard product hero: show the complete, accurate product on a pure white background." in compiled["prompt"]
    assert "Hero restrictions: no promotional text, text overlay, watermark, border, price, discount, badge, or lifestyle scene." in compiled["prompt"]
    assert compiled["input_snapshot"]["standard_product_hero"] is True


def test_confirm_generation_rejects_a_template_without_the_required_first_output_slot():
    """A set without slot 1 would omit the mandatory standard product hero."""
    from platform_app.models import Batch, OutputSlot, OutputTemplate
    from platform_app.services import confirm_generation

    user = make_user()
    template = OutputTemplate.objects.create(platform="global", name="Incomplete template")
    OutputSlot.objects.create(template=template, name="Detail image", order=2)
    batch = Batch.objects.create(owner=user, name="missing hero", output_template=template)
    make_cluster(batch)

    with pytest.raises(ValueError, match="standard product hero"):
        confirm_generation(batch, user)


@pytest.mark.parametrize("status", ["draft", "retired"])
def test_confirm_generation_rejects_unpublished_template(status):
    """Paid generation cannot run against a draft or retired output template."""
    from platform_app.models import Batch, OutputSlot, OutputTemplate
    from platform_app.services import confirm_generation

    user = make_user()
    template = OutputTemplate.objects.create(platform="shopee", name="template", status=status)
    OutputSlot.objects.create(template=template, name="main", order=1)
    batch = Batch.objects.create(owner=user, name="blocked", output_template=template)
    make_cluster(batch)

    with pytest.raises(ValueError, match="published"):
        confirm_generation(batch, user)


@pytest.mark.parametrize("status", ["draft", "retired"])
def test_confirm_generation_rejects_unpublished_rule(status):
    """Paid generation cannot run against a draft or retired rule profile."""
    from platform_app.models import Batch, OutputSlot, OutputTemplate, RuleProfile
    from platform_app.services import confirm_generation

    user = make_user()
    template = OutputTemplate.objects.create(platform="shopee", name="template")
    OutputSlot.objects.create(template=template, name="main", order=1)
    rule = RuleProfile.objects.create(
        platform="shopee",
        site="SG",
        name="blocked rules",
        status=status,
    )
    batch = Batch.objects.create(owner=user, name="blocked", output_template=template, rule_profile=rule)
    make_cluster(batch)

    with pytest.raises(ValueError, match="published"):
        confirm_generation(batch, user)


def test_prompt_version_used_by_generation_cannot_change_snapshot_or_admin_form():
    """A prompt used for billing remains immutable both in the model and the admin."""
    from django.contrib import admin

    from platform_app.models import Batch, Generation, OutputSlot, OutputTemplate, PromptVersion

    user = make_user()
    template = OutputTemplate.objects.create(platform="shopee", name="template")
    slot = OutputSlot.objects.create(template=template, name="main", order=1)
    batch = Batch.objects.create(owner=user, name="immutable")
    cluster = make_cluster(batch)
    prompt_version = PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        prompt_text="original",
        input_snapshot={"product_name": "original"},
    )
    Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        prompt_version=prompt_version,
    )

    prompt_version.prompt_text = "rewritten"
    prompt_version.input_snapshot = {"product_name": "rewritten"}
    with pytest.raises(ValidationError, match="immutable"):
        prompt_version.save()

    prompt_admin = admin.site._registry[PromptVersion]
    assert "prompt_text" in prompt_admin.get_readonly_fields(None, prompt_version)


def test_confirm_generation_query_count_does_not_grow_with_cluster_slot_compilation():
    """Adding prompt slots must add writes, not reference/node lookup selects per slot."""
    from platform_app.models import Batch, OutputSlot, OutputTemplate
    from platform_app.services import confirm_generation

    user = make_user()

    def make_batch(name, cluster_count):
        template = OutputTemplate.objects.create(platform="shopee", name=f"{name} template")
        OutputSlot.objects.create(template=template, name="main", order=1)
        OutputSlot.objects.create(template=template, name="detail", order=2)
        batch = Batch.objects.create(
            owner=user,
            name=name,
            platform="shopee",
            site="SG",
            market="SG",
            output_template=template,
        )
        for _ in range(cluster_count):
            make_cluster(batch)
        return batch

    one_cluster = make_batch("one", 1)
    two_clusters = make_batch("two", 2)

    with CaptureQueriesContext(connection) as one_context:
        confirm_generation(one_cluster, user)
    with CaptureQueriesContext(connection) as two_context:
        confirm_generation(two_clusters, user)

    one_selects = sum(query["sql"].lstrip().upper().startswith("SELECT") for query in one_context.captured_queries)
    two_selects = sum(query["sql"].lstrip().upper().startswith("SELECT") for query in two_context.captured_queries)
    assert two_selects < one_selects * 2


def test_prompt_node_template_publish_and_rollback_keeps_one_active_version():
    """Publishing or rolling back a node must make exactly the intended version active."""
    from platform_app.models import PromptNodeTemplate
    from platform_app.services import publish_prompt_node_template, rollback_prompt_node_template

    first = PromptNodeTemplate.objects.create(
        node_name="slot_prompt",
        version="v1",
        instruction="Keep claims grounded.",
    )
    second = PromptNodeTemplate.objects.create(
        node_name="slot_prompt",
        version="v2",
        instruction="Keep claims grounded and concise.",
    )

    publish_prompt_node_template(first)
    publish_prompt_node_template(second)
    rollback_prompt_node_template("slot_prompt", "v1")

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == PromptNodeTemplate.Status.PUBLISHED
    assert second.status == PromptNodeTemplate.Status.RETIRED


def test_prompt_worker_prepares_pending_cluster_with_nine_slot_prompts(tmp_path, settings):
    import json

    from platform_app.models import Asset, Batch, Cluster, Generation, OutputTemplate, PromptVersion
    from platform_app.services import FakeAPIMartClient, LocalStorage, process_prompt_once, request_cluster_preparation
    from platform_app.management.commands.seed_platform_templates import GLOBAL_SLOTS

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = Batch.objects.create(owner=user, name="Prompt queue", output_template=None)
    storage_path = f"originals/{batch.id}/source.png"
    (tmp_path / storage_path).parent.mkdir(parents=True)
    (tmp_path / storage_path).write_bytes(b"png-bytes")
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="source.png",
        storage_path=storage_path,
        sha256="b" * 64,
        file_size=9,
        content_type="image/png",
    )
    cluster = Cluster.create_for_asset(batch, asset)

    template = OutputTemplate.objects.create(
        seed_key="global-marketplace-nine-slot-template",
        platform="global",
        site="",
        name="Nine slot",
        version="2026.07.9",
    )
    for order, name, purpose in GLOBAL_SLOTS:
        template.slots.create(order=order, name=name, purpose=purpose)
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    request_cluster_preparation(cluster, auto_generate=True)

    class PromptClient(FakeAPIMartClient):
        def observe_images(self, instruction, image_paths):
            assert image_paths
            observed_asset_id = instruction.split("ASSET_ID=", 1)[1].splitlines()[0]
            return {
                "output_text": json.dumps(
                    strict_n1({
                        "asset_id": observed_asset_id,
                        "asset_kind": "owned_product",
                        "image_role": "clean_product",
                        "contains_target_product": True,
                        "target_is_physical_product": True,
                        "target_visibility": 91,
                        "target_complete": True,
                        "background_complexity": "low",
                        "observed_identity": {
                            "category_candidates": ["silicone cup"],
                            "overall_shape": "round cup with two handles",
                        },
                        "reference_quality": 91,
                        "recommended_use": "reuse",
                        "candidate_product_name": "Silicone cup",
                        "candidate_product_name_confidence": 0.91,
                        "product_facts": ["green silicone cup", "two handles"],
                        "identity_lock": "Keep green cup and two handles",
                        "target_consumer": "adult",
                    })
                ),
                "raw": {"node": "vision"},
            }

        def optimize_prompt(self, payload):
            return super().optimize_prompt(payload)

    assert process_prompt_once(PromptClient(), LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()

    assert cluster.preparation_status == "ready"
    assert cluster.auto_generate is False
    assert cluster.product_name == "Silicone cup"
    assert cluster.product_facts == "green silicone cup; two handles"
    assert cluster.identity_lock == "Keep green cup and two handles"
    assert (
        cluster.analysis_snapshot["observations"][0]["candidate_product_name_confidence"]
        == 0.91
    )
    assert cluster.analysis_snapshot["fact_ledger"]["review_summary"]["confirmed_count"] == 1
    prompts = list(PromptVersion.objects.filter(cluster=cluster).order_by("output_slot__order"))
    assert sorted({prompt.output_slot.order for prompt in prompts}) == list(range(1, 10))
    assert len(prompts) == 9
    assert all(prompt.prompt_text for prompt in prompts)
    assert all(prompt.evaluation["rule_gate"]["decision"] == "pass" for prompt in prompts)
    assert sorted(
        Generation.objects.filter(cluster=cluster).values_list(
            "output_slot__order",
            flat=True,
        )
    ) == list(range(1, 10))

    confirmed_path = f"originals/{batch.id}/confirmed.png"
    (tmp_path / confirmed_path).write_bytes(b"confirmed-bytes")
    confirmed_asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="confirmed.png",
        storage_path=confirmed_path,
        sha256="d" * 64,
        file_size=15,
        content_type="image/png",
    )
    confirmed_cluster = Cluster.create_for_asset(batch, confirmed_asset)
    confirmed_cluster.product_name = "Confirmed ceramic mug"
    confirmed_cluster.save(update_fields=["product_name"])
    request_cluster_preparation(confirmed_cluster, auto_generate=False)

    class LowConfidenceClient(PromptClient):
        def optimize_prompt(self, payload):
            if "Normalize an ecommerce marketing plan" in payload.get("system", ""):
                return {
                    "output_text": json.dumps(
                        {
                            "plans": [
                                {
                                    "slot_order": order,
                                    "scene_family": f"family-{order}",
                                    "conversion_goal": f"decision-{order}",
                                    "main_scene": f"scene-{order}",
                                "main_action": f"action-{order}",
                                    "visible_text_lines": [],
                                }
                                for order in range(2, 10)
                            ]
                        }
                    ),
                    "raw": {},
                }
            if "NODE N2" in payload["text"]:
                return {
                    "output_text": json.dumps(
                        strict_n2({
                            "decision": "continue",
                            "confidence": 0.1,
                            "needs_input_reason": "",
                            "product_name": "Confirmed ceramic mug",
                            "conflict_state": "unknown",
                            "product_profile": {
                                "category": "ceramic mug",
                                "primary_appearance": "visible ceramic mug",
                            },
                            "identity_lock": {"must_not_change": ["visible mug"]},
                            "primary_asset_id": str(confirmed_asset.id),
                            "supporting_asset_ids": [],
                            "standardization_mode": "reuse",
                            "standardization_reason": "",
                        })
                    ),
                    "raw": {},
                }
            if "NODE N5" in payload["text"]:
                return super().optimize_prompt(payload)
            return super().optimize_prompt(payload)

    assert process_prompt_once(LowConfidenceClient(), LocalStorage(tmp_path)) == 1
    confirmed_cluster.refresh_from_db()
    assert confirmed_cluster.preparation_status == "ready", confirmed_cluster.preparation_error
    assert confirmed_cluster.product_name == "Confirmed ceramic mug"


def test_prompt_worker_repairs_non_object_slot_json_with_schema(tmp_path, settings):
    import json

    from platform_app.management.commands.seed_platform_templates import GLOBAL_SLOTS
    from platform_app.models import Asset, Batch, Cluster, OutputTemplate, PromptVersion
    from platform_app.services import FakeAPIMartClient, LocalStorage, process_prompt_once, request_cluster_preparation

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = Batch.objects.create(owner=user, name="Prompt repair", output_template=None)
    storage_path = f"originals/{batch.id}/source.png"
    (tmp_path / storage_path).parent.mkdir(parents=True)
    (tmp_path / storage_path).write_bytes(b"png-bytes")
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="source.png",
        storage_path=storage_path,
        sha256="c" * 64,
        file_size=9,
        content_type="image/png",
    )
    cluster = Cluster.create_for_asset(batch, asset)
    template = OutputTemplate.objects.create(
        seed_key="global-marketplace-nine-slot-template",
        platform="global",
        site="",
        name="Nine slot",
        version="2026.07.9",
    )
    for order, name, purpose in GLOBAL_SLOTS:
        template.slots.create(order=order, name=name, purpose=purpose)
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    request_cluster_preparation(cluster, auto_generate=False)

    class PromptClient(FakeAPIMartClient):
        def __init__(self):
            self.repairs = []
            self.broke_identity = False

        def observe_images(self, instruction, image_paths):
            return {
                "output_text": json.dumps(
                    strict_n1({
                        "asset_id": str(asset.id),
                        "asset_kind": "owned_product",
                        "image_role": "clean_product",
                        "contains_target_product": True,
                        "target_is_physical_product": True,
                        "target_visibility": 90,
                        "target_complete": True,
                        "background_complexity": "low",
                        "observed_identity": {
                            "category_candidates": ["storage box"],
                            "overall_shape": "rectangular storage box",
                        },
                        "reference_quality": 90,
                        "recommended_use": "reuse",
                        "candidate_product_name": "Storage box",
                        "candidate_product_name_confidence": 0.9,
                        "product_facts": ["blue box"],
                        "identity_lock": "Keep blue box",
                        "target_consumer": "adult",
                    })
                ),
                "raw": {},
            }

        def optimize_prompt(self, payload):
            if "Previous response:" in payload["text"]:
                self.repairs.append(payload["text"])
                return {
                    "output_text": json.dumps(
                        strict_n2({
                                "decision": "continue",
                                "confidence": 90,
                                "needs_input_reason": "",
                                "product_name": "Storage box",
                                "conflict_state": "unknown",
                                "product_profile": {"category": "storage box", "primary_appearance": "blue"},
                                "identity_lock": {"must_not_change": ["blue box"]},
                            "primary_asset_id": str(asset.id),
                            "supporting_asset_ids": [],
                            "standardization_mode": "reuse",
                            "standardization_reason": "",
                        })
                    ),
                    "raw": {},
                }
            if "NODE N2" in payload["text"] and not self.broke_identity:
                self.broke_identity = True
                return {"output_text": json.dumps([{"wrong": "top-level"}]), "raw": {}}
            return super().optimize_prompt(payload)

    client = PromptClient()
    assert process_prompt_once(client, LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()

    assert cluster.preparation_status == "ready"
    assert client.repairs
    prompts = list(PromptVersion.objects.filter(cluster=cluster).order_by("output_slot__order"))
    assert len(prompts) == 9
    assert all("Storage box" in prompt.prompt_text for prompt in prompts)


def test_prompt_worker_marks_only_current_cluster_failed_after_json_repair_fails(tmp_path, settings):
    from platform_app.models import Asset, Batch, Cluster, OutputSlot, OutputTemplate
    from platform_app.services import LocalStorage, process_prompt_once, request_cluster_preparation

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    template = OutputTemplate.objects.create(platform="global", site="", name="Template")
    OutputSlot.objects.create(template=template, name="Hero", order=1)
    batch = Batch.objects.create(owner=user, name="Prompt queue", output_template=template)

    for index in range(2):
        storage_path = f"originals/{batch.id}/{index}.png"
        (tmp_path / storage_path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / storage_path).write_bytes(b"png-bytes")
        asset = Asset.objects.create(
            batch=batch,
            kind=Asset.Kind.IMAGE,
            original_filename=f"{index}.png",
            storage_path=storage_path,
            sha256=str(index).rjust(64, "0"),
            file_size=9,
            content_type="image/png",
        )
        request_cluster_preparation(Cluster.create_for_asset(batch, asset), auto_generate=False)

    class BadClient:
        def observe_images(self, instruction, image_paths):
            return {"output_text": "{bad", "raw": {}}

        def optimize_prompt(self, payload):
            return {"output_text": "{still bad", "raw": {}}

    assert process_prompt_once(BadClient(), LocalStorage(tmp_path)) == 1
    statuses = list(batch.clusters.order_by("created_at").values_list("preparation_status", flat=True))
    assert statuses == ["failed", "pending"]


def test_prompt_worker_runs_versioned_nodes_and_keeps_grounding_in_final_prompts(tmp_path, settings):
    import json

    from django.core.management import call_command

    from platform_app.management.commands.seed_platform_templates import GLOBAL_SLOTS
    from platform_app.models import Asset, Batch, Cluster, OutputTemplate, PromptVersion
    from platform_app.services import (
        LocalStorage,
        _validate_prompt_version_readiness,
        process_prompt_once,
        request_cluster_preparation,
        update_cluster_content,
    )

    settings.MEDIA_ROOT = tmp_path
    call_command("seed_platform_templates")
    user = make_user()
    template = OutputTemplate.objects.create(
        seed_key="prompt-os-v2-test-template",
        platform="global",
        site="",
        name="Prompt OS v2",
        version="2026.07.30",
    )
    for order, name, purpose in GLOBAL_SLOTS:
        template.slots.create(order=order, name=name, purpose=purpose)
    batch = Batch.objects.create(owner=user, name="Prompt OS v2", output_template=template)
    storage_path = f"originals/{batch.id}/source.png"
    (tmp_path / storage_path).parent.mkdir(parents=True)
    (tmp_path / storage_path).write_bytes(b"png-bytes")
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="source.png",
        storage_path=storage_path,
        sha256="d" * 64,
        file_size=9,
        content_type="image/png",
    )
    cluster = Cluster.create_for_asset(batch, asset)
    request_cluster_preparation(cluster, auto_generate=False)

    class PromptOSClient:
        n7_calls = []

        def observe_images(self, instruction, image_paths):
            assert "NODE N1" in instruction
            return {
                "output_text": json.dumps(
                    strict_n1({
                        "asset_id": str(asset.id),
                        "asset_kind": "owned_product",
                        "image_role": "clean_product",
                        "contains_target_product": True,
                        "target_is_physical_product": True,
                        "target_visibility": 95,
                        "target_complete": True,
                        "background_complexity": "low",
                        "reference_quality": 95,
                        "observed_identity": {
                            "category_candidates": ["storage container"],
                            "overall_shape": "rectangular container with two handles",
                            "dominant_colors": ["sage green"],
                            "distinctive_parts": ["two handles"],
                        },
                        "candidate_product_name": "Sage storage container",
                        "candidate_product_name_confidence": 93,
                        "recommended_use": "reuse",
                    })
                ),
                "raw": {},
            }

        def optimize_prompt(self, payload):
            text = payload["text"]
            if "NODE N2" in text:
                output = strict_n2({
                        "decision": "continue",
                        "confidence": 93,
                        "needs_input_reason": "",
                        "product_name": "Sage storage container",
                        "conflict_state": "unknown",
                        "product_profile": {"category": "storage container", "primary_appearance": "sage green"},
                    "identity_lock": {
                        "family_invariants": ["storage container"],
                        "primary_variant_attributes": ["sage green"],
                        "must_not_change": ["two handles"],
                    },
                    "primary_asset_id": str(asset.id),
                    "supporting_asset_ids": [],
                    "target_appearances": [{
                        "appearance_id": "appearance.primary",
                        "label": "sage green",
                        "variant_attributes": ["sage green"],
                        "asset_ids": [str(asset.id)],
                        "primary_asset_id": str(asset.id),
                    }],
                    "standardization_mode": "reuse",
                    "standardization_reason": "",
                })
            elif "NODE N3" in text:
                output = {
                    "ledger_version": "2.0.0",
                    "facts": [
                        {
                            "fact_id": "fact.name.001",
                            "statement": "Sage storage container",
                            "fact_class": "confirmed",
                            "confidence": 1.0,
                            "evidence_refs": ["product_name"],
                            "risk_level": "low",
                            "allowed_uses": ["identity", "visual_prompt", "consumer_copy"],
                            "review_note": "",
                        },
                        {
                            "fact_id": "fact.color.001",
                            "statement": "sage green",
                            "fact_class": "observed",
                            "confidence": 0.95,
                            "evidence_refs": [f"asset:{asset.id}"],
                            "risk_level": "low",
                            "allowed_uses": ["identity", "visual_prompt"],
                            "review_note": "",
                        },
                    ],
                    "blocked_claim_topics": ["price", "certification", "medical_efficacy"],
                    "unresolved_questions": [],
                    "review_summary": {
                        "confirmed_count": 1,
                        "observed_count": 1,
                        "inferred_count": 0,
                        "high_risk_count": 0,
                    },
                }
            elif "NODE N4" in text:
                output = {
                    "slot_id": "1",
                    "main_scene": "pure white commercial studio",
                    "main_action": "none",
                    "visible_text_lines": [],
                    "prompt": "Front-facing complete product on pure white.",
                    "character_count": 44,
                    "reference_plan": {
                        "primary_asset_id": str(asset.id),
                        "supporting_asset_ids": [],
                        "include_completed_white_image": False,
                    },
                    "fact_trace": ["fact.name.001", "fact.color.001"],
                    "inference_trace": [],
                    "rule_refs": [],
                    "generation_parameters": {
                        "model": "gpt-image-2",
                        "n": 1,
                        "size": "1:1",
                        "resolution": "1k",
                    },
                    "review_required": True,
                }
            elif "NODE N5" in text:
                modes = [
                    "fab_value",
                    "scene_ownership",
                    "emotion",
                    "personification",
                    "identity_signal",
                ]
                output = {
                    "plans": [
                        {
                            "slot_order": order,
                            "role": f"role-{order}",
                            "appearance_ids": ["appearance.primary"],
                            "scene_family": f"family-{order}",
                            "environment": f"environment-{order}",
                            "camera": f"camera-{order}",
                            "decision_task": f"goal-{order}",
                            "conversion_goal": f"goal-{order}",
                                "fact_refs": [],
                                "inference_refs": [],
                                "main_scene": f"scene-{order}",
                                "main_action": f"action-{order}",
                                "subject_relationship": "product centered",
                            "composition": f"composition-{order}",
                            "copy_intent": "",
                            "text_mode": "up_to_3_lines",
                            "localization_notes": [],
                            "must_show": [],
                            "must_avoid": [],
                            "visible_text_lines": [],
                            "creative_strategy": creative_strategy(
                                modes[(order - 2) % len(modes)],
                                ("fact.name.001",),
                            ),
                        }
                        for order in range(2, 10)
                    ]
                }
            elif "NODE N6" in text:
                order = int(text.split("SLOT_ORDER=", 1)[1].splitlines()[0])
                prompt = f"Show purchase decision scene {order}."
                output = {
                    "slot_id": str(order),
                    "main_scene": f"scene-{order}",
                    "main_action": "none",
                    "visible_text_lines": [],
                    "localized_copy": {
                        "language": "en",
                        "lines": [],
                        "back_translation": "",
                        "strategy_mode": "fab_value",
                        "source_fact_refs": [],
                        "source_inference_refs": [],
                        "quality": {
                            "relevance": 90,
                            "specificity": 90,
                            "imagery": 90,
                            "naturalness": 90,
                            "truthfulness": 90,
                            "mobile_readability": 90,
                            "generic_phrase_hits": [],
                        },
                    },
                    "prompt": prompt,
                    "character_count": len(prompt),
                    "reference_plan": {
                        "primary_asset_id": str(asset.id),
                        "supporting_asset_ids": [],
                        "completed_white_result_id": None,
                    },
                    "fact_trace": ["fact.name.001", "fact.color.001"],
                    "inference_trace": [],
                    "rule_refs": [],
                    "generation_parameters": {
                        "model": "gpt-image-2",
                        "n": 1,
                        "size": "1:1",
                        "resolution": "1k",
                    },
                    "review_required": True,
                }
            elif "NODE N7" in text:
                self.n7_calls.append(text.splitlines()[0])
                output = {
                    "decision": "pass",
                    "hard_blocks": [],
                    "semantic_risks": [],
                    "warnings": [],
                    "prompt_checks": {
                        "character_count": 32,
                        "text_line_count": 0,
                        "main_scene_count": 1,
                        "main_action_count": 1,
                        "reference_assets_valid": True,
                    },
                    "copy_checks": {
                        "lines_match_visible_text": True,
                        "each_line_present_once": True,
                        "language_match": True,
                        "fact_refs_valid": True,
                        "generic_phrase_hits": [],
                    },
                    "resolved_rule_refs": [],
                    "review_required": True,
                }
            else:
                raise AssertionError(text)
            return {"output_text": json.dumps(output), "raw": {}}

    prompt_client = PromptOSClient()
    assert process_prompt_once(prompt_client, LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()

    assert cluster.preparation_status == Cluster.PreparationStatus.READY, cluster.preparation_error
    assert cluster.product_name == "Sage storage container"
    assert cluster.analysis_snapshot["fact_ledger"]["review_summary"]["observed_count"] == 1
    assert [snapshot["node_id"] for snapshot in cluster.analysis_snapshot["prompt_os"]] == [
        "N1",
        "N2",
        "N3",
        "N4",
        "N5.generic",
        *["N6.generic"] * 8,
        *["N7.generic"] * 9,
    ]
    prompts = list(PromptVersion.objects.filter(cluster=cluster).order_by("output_slot__order"))
    assert len(prompts) == 9
    assert [prompt.node_name for prompt in prompts] == ["N4", *["N6.generic"] * 8]
    assert prompt_client.n7_calls == []
    assert all("Sage storage container" in prompt.prompt_text for prompt in prompts)
    assert all("two handles" in prompt.prompt_text for prompt in prompts)
    assert all(len(prompt.prompt_text) <= 3500 for prompt in prompts)
    assert all(prompt.evaluation["rule_gate"]["decision"] == "pass" for prompt in prompts)

    parent = PromptVersion.objects.filter(cluster=cluster, output_slot__order=1).latest("created_at")
    update_cluster_content(
        cluster,
        user,
        {
            "expected_version": cluster.version,
            "prompts": [
                {
                    "slot_order": 1,
                    "prompt": "Accurate Sage storage container on pure white.",
                }
            ],
        },
    )
    cluster.refresh_from_db()
    manual = PromptVersion.objects.filter(cluster=cluster, output_slot__order=1).latest("created_at")
    assert manual.id != parent.id
    assert manual.source_snapshot["parent_prompt_version_id"] == str(parent.id)
    assert manual.evaluation["rule_gate"]["decision"] == "pass"
    assert cluster.analysis_snapshot["prompt_os"][-1]["node_id"] == "N7.generic"
    _validate_prompt_version_readiness(
        manual,
        cluster,
        batch,
        manual.output_slot,
        allowed_nodes={"N4"},
    )


def test_tiktok_us_official_no_digital_rendering_rule_warns_without_blocking_generation():
    from platform_app.models import Batch, OutputSlot, OutputTemplate, RuleProfile
    from platform_app.services import evaluate_prompt_rule_gate

    user = make_user()
    template = OutputTemplate.objects.create(platform="tiktok", site="US", name="US template")
    slot = OutputSlot.objects.create(template=template, name="Hero", order=1)
    rule = RuleProfile.objects.create(
        platform="tiktok",
        site="US",
        name="TikTok US",
        status=RuleProfile.Status.PUBLISHED,
        rules=[
            {
                "rule_id": "tiktok.us.gallery.no_digital_rendering",
                "market": "US",
                "seller_tier": "general",
                "category_scope": ["all"],
                "slot_scope": ["cover", "gallery"],
                "severity": "HARD_PLATFORM",
                "requirement": "Digital renderings are not permitted.",
                "prompt_directive": "Use real product photography; do not generate a digital rendering.",
                "verification_status": "verified",
            }
        ],
    )
    batch = Batch.objects.create(
        owner=user,
        name="TikTok US",
        platform="tiktok",
        site="US",
        market="US",
        output_template=template,
        rule_profile=rule,
    )

    gate = evaluate_prompt_rule_gate(
        batch,
        slot,
        "Create one product image.",
        effective_config={"platform": "tiktok", "market": "US", "sellerTier": "general"},
        rule_profile=rule,
    )

    assert gate["decision"] == "pass"
    assert gate["hard_blocks"] == []
    assert "tiktok.us.gallery.no_digital_rendering" in gate["warnings"]


def test_prompt_worker_requeues_when_settings_change_during_preparation(tmp_path, settings):
    from platform_app.management.commands.seed_platform_templates import GLOBAL_SLOTS
    from platform_app.models import Asset, Batch, Cluster, OutputTemplate, PromptVersion, RuleProfile
    from platform_app.services import (
        FakeAPIMartClient,
        LocalStorage,
        process_prompt_once,
        request_cluster_preparation,
        update_project_settings,
    )

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    template = OutputTemplate.objects.create(
        seed_key="global-marketplace-nine-slot-template",
        platform="global",
        site="",
        name="Global template",
    )
    for order, name, purpose in GLOBAL_SLOTS:
        template.slots.create(order=order, name=name, purpose=purpose)
    rule = RuleProfile.objects.create(
        seed_key="global-marketplace-prompt-os-v2-rule",
        platform="global",
        site="",
        name="Global rules",
        status=RuleProfile.Status.PUBLISHED,
    )
    batch = Batch.objects.create(
        owner=user,
        name="Race",
        platform="shopee",
        site="VN",
        market="VN",
        output_template=template,
        rule_profile=rule,
    )
    storage_path = f"originals/{batch.id}/source.png"
    (tmp_path / storage_path).parent.mkdir(parents=True)
    (tmp_path / storage_path).write_bytes(b"png-bytes")
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="source.png",
        storage_path=storage_path,
        sha256="e" * 64,
        file_size=9,
        content_type="image/png",
    )
    cluster = Cluster.create_for_asset(batch, asset)
    request_cluster_preparation(cluster, auto_generate=False)

    class UpdatingClient(FakeAPIMartClient):
        changed = False

        def observe_images(self, instruction, image_paths):
            if not self.changed:
                self.changed = True
                update_project_settings(
                    batch,
                    {
                        "platform": "tiktok",
                        "market": "TH",
                        "seller_tier": "general",
                        "size": "1:1",
                        "resolution": "1k",
                    },
                )
            return super().observe_images(instruction, image_paths)

    assert process_prompt_once(UpdatingClient(), LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()
    assert cluster.preparation_status == Cluster.PreparationStatus.DRAFT
    assert PromptVersion.objects.filter(cluster=cluster).count() == 0


def test_stale_terminal_persistence_cannot_overwrite_a_newer_claim():
    from platform_app.models import Batch, Cluster, OutputSlot, OutputTemplate, PromptVersion
    from platform_app.services import _persist_prompt_terminal

    user = make_user()
    template = OutputTemplate.objects.create(platform="global", name="Terminal")
    slot = OutputSlot.objects.create(template=template, name="Hero", order=1)
    batch = Batch.objects.create(owner=user, name="Terminal", output_template=template)
    cluster = Cluster.objects.create(
        batch=batch,
        name="Product",
        preparation_status=Cluster.PreparationStatus.PREPARING,
        analysis_snapshot={"_preparation_revision": 2},
    )

    persisted = _persist_prompt_terminal(
        cluster.id,
        1,
        [{"output_slot": slot, "node_name": "N7", "template_version": "v1", "provider_model": "fake", "prompt_text": "stale"}],
        {"_preparation_revision": 1},
        Cluster.PreparationStatus.READY,
        "",
        user,
    )

    cluster.refresh_from_db()
    assert persisted is False
    assert cluster.preparation_status == Cluster.PreparationStatus.PREPARING
    assert cluster.analysis_snapshot["_preparation_revision"] == 2
    assert PromptVersion.objects.filter(cluster=cluster).count() == 0


def test_prompt_worker_stale_terminal_does_not_autogenerate_newer_claim(
    tmp_path, settings, monkeypatch
):
    from platform_app.models import (
        Asset,
        Batch,
        Cluster,
        Generation,
        OutputSlot,
        OutputTemplate,
        PromptVersion,
    )
    from platform_app.services import (
        FakeAPIMartClient,
        LocalStorage,
        ensure_cluster_generations,
        process_prompt_once,
        request_cluster_preparation,
    )

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    template = OutputTemplate.objects.create(platform="global", name="Terminal")
    OutputSlot.objects.create(template=template, name="Hero", order=1)
    batch = Batch.objects.create(owner=user, name="Terminal", output_template=template)
    storage_path = f"originals/{batch.id}/source.png"
    (tmp_path / storage_path).parent.mkdir(parents=True)
    (tmp_path / storage_path).write_bytes(b"png-bytes")
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="source.png",
        storage_path=storage_path,
        sha256="f" * 64,
        file_size=9,
        content_type="image/png",
    )
    cluster = Cluster.create_for_asset(batch, asset)
    cluster.analysis_snapshot = {"_preparation_revision": 1}
    cluster.save(update_fields=["analysis_snapshot"])
    request_cluster_preparation(cluster, auto_generate=False)

    ensure_calls = []

    def tracked_ensure_cluster_generations(*args, **kwargs):
        ensure_calls.append(args[0].id)
        return ensure_cluster_generations(*args, **kwargs)

    monkeypatch.setattr(
        "platform_app.services.ensure_cluster_generations",
        tracked_ensure_cluster_generations,
    )

    class NewerClaimClient(FakeAPIMartClient):
        changed = False

        def optimize_prompt(self, payload):
            response = super().optimize_prompt(payload)
            if not self.changed and "NODE N4" in payload["text"]:
                self.changed = True
                Cluster.objects.filter(id=cluster.id).update(
                    preparation_status=Cluster.PreparationStatus.PREPARING,
                    analysis_snapshot={"_preparation_revision": 2},
                    auto_generate=True,
                )
            return response

    assert process_prompt_once(NewerClaimClient(), LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()
    assert cluster.preparation_status == Cluster.PreparationStatus.PREPARING
    assert cluster.analysis_snapshot["_preparation_revision"] == 2
    assert cluster.auto_generate is True
    assert ensure_calls == []
    assert PromptVersion.objects.filter(cluster=cluster).count() == 0
    assert Generation.objects.filter(cluster=cluster).count() == 0


def test_n1_and_n2_normalizers_require_identity_fields_and_cluster_owned_references():
    from platform_app.services import _normalize_n1_observation, _normalize_n2_identity

    asset_id = "11111111-1111-1111-1111-111111111111"
    observation = _normalize_n1_observation(
        strict_n1({
            "asset_id": asset_id,
            "asset_kind": "owned_product",
            "image_role": "clean_product",
            "contains_target_product": True,
            "target_is_physical_product": True,
            "target_visibility": 92,
            "target_complete": True,
            "background_complexity": "low",
            "observed_identity": {
                "category_candidates": ["travel mug"],
                "overall_shape": "cylindrical mug with lid",
            },
            "reference_quality": 92,
            "recommended_use": "reuse",
            "candidate_product_name": "Travel mug",
            "candidate_product_name_confidence": 87,
        }),
        asset_id,
    )

    assert observation["candidate_product_name"] == "Travel mug"
    assert observation["candidate_product_name_confidence"] == 0.87

    identity = _normalize_n2_identity(
        strict_n2({
            "decision": "continue",
            "product_name": "Travel mug",
            "confidence": 91,
            "needs_input_reason": "",
            "conflict_state": "unknown",
            "primary_asset_id": asset_id,
            "supporting_asset_ids": [],
            "standardization_mode": "reuse",
            "standardization_reason": "",
            "identity_lock": {"must_not_change": ["lid"]},
            "product_profile": {
                "category": "travel mug",
                "primary_appearance": "visible travel mug",
            },
        }),
        {asset_id},
    )

    assert identity["confidence"] == 0.91
    assert identity["primary_asset_id"] == asset_id

    with pytest.raises(ValueError, match="candidate_product_name"):
        _normalize_n1_observation(
                strict_n1({
                    "asset_id": asset_id,
                    "asset_kind": "owned_product",
                    "image_role": "clean_product",
                    "contains_target_product": True,
                    "target_is_physical_product": True,
                    "target_visibility": 92,
                    "target_complete": True,
                    "background_complexity": "low",
                    "observed_identity": {
                        "category_candidates": ["travel mug"],
                        "overall_shape": "cylindrical mug with lid",
                    },
                    "reference_quality": 92,
                    "recommended_use": "reuse",
                }),
            asset_id,
        )

    with pytest.raises(ValueError, match="cluster asset"):
        _normalize_n2_identity(
            {
                **identity,
                "primary_asset_id": "22222222-2222-2222-2222-222222222222",
            },
            {asset_id},
        )


def test_n3_to_n6_normalizers_reject_unknown_refs_overlong_prompts_and_duplicate_marketing_sets():
    from platform_app.services import (
        _normalize_n3_ledger,
        _normalize_n4_prompt,
        _normalize_n5_plans,
        _normalize_n6_prompt,
    )

    ledger = _normalize_n3_ledger(
        {
            "ledger_version": "2.0.0",
            "facts": [
                {
                    "fact_id": "fact.name.001",
                    "statement": "Travel mug",
                    "fact_class": "confirmed",
                    "confidence": 1,
                    "evidence_refs": ["product_name"],
                    "risk_level": "low",
                    "allowed_uses": ["identity", "visual_prompt", "consumer_copy"],
                    "review_note": "",
                }
            ],
            "blocked_claim_topics": ["price"],
            "unresolved_questions": [],
            "review_summary": {
                "confirmed_count": 1,
                "observed_count": 0,
                "inferred_count": 0,
                "high_risk_count": 0,
            },
        }
    )
    identity = {
        "primary_asset_id": "11111111-1111-1111-1111-111111111111",
        "supporting_asset_ids": [],
    }
    hero = _normalize_n4_prompt(
        {
            "slot_id": "1",
            "main_scene": "pure white commercial studio",
            "main_action": "none",
            "visible_text_lines": [],
            "prompt": "Accurate product on pure white.",
            "character_count": 31,
            "reference_plan": {
                "primary_asset_id": identity["primary_asset_id"],
                "supporting_asset_ids": [],
                "include_completed_white_image": False,
            },
            "fact_trace": ["fact.name.001"],
            "inference_trace": [],
            "rule_refs": ["rule.hero"],
            "generation_parameters": {
                "model": "gpt-image-2",
                "n": 1,
                "size": "1:1",
                "resolution": "1k",
            },
            "review_required": True,
        },
        1,
        identity,
        ledger,
        {"rule.hero"},
    )
    assert hero["character_count"] == len(hero["prompt"])

    with pytest.raises(ValueError, match="3500"):
        _normalize_n4_prompt(
            {**hero, "prompt": "x" * 3501, "character_count": 3501},
            1,
            identity,
            ledger,
            {"rule.hero"},
        )

    slots = [
        type("Slot", (), {"order": 2, "name": "Benefit", "purpose": "Benefit"})(),
        type("Slot", (), {"order": 3, "name": "Usage", "purpose": "Usage"})(),
    ]
    duplicate_plan = {
        "plans": [
            {
                "slot_order": order,
                "role": f"role-{order}",
                "decision_task": f"decision-{order}",
                "fact_refs": ["fact.name.001"],
                "inference_refs": [],
                "main_scene": "kitchen",
                "main_action": "none",
                "subject_relationship": "product centered",
                "composition": "centered",
                "copy_intent": "",
                "text_mode": "up_to_3_lines",
                "localization_notes": [],
                "must_show": [],
                "must_avoid": [],
                "scene_family": "home",
                "environment": "kitchen",
                "camera": "eye level",
            }
            for order in (2, 3)
        ]
    }
    with pytest.raises(ValueError, match="diversity"):
        _normalize_n5_plans(duplicate_plan, slots, {"fact.name.001"}, set())

    with pytest.raises(ValueError, match="fact reference"):
        _normalize_n6_prompt(
            {
                "slot_id": "2",
                "main_scene": "kitchen",
                "main_action": "none",
                "visible_text_lines": [],
                "localized_copy": {
                    "language": "en-SG",
                    "lines": [],
                    "source_fact_refs": ["fact.missing"],
                    "source_inference_refs": [],
                },
                "prompt": "Accurate travel mug in one kitchen scene.",
                "character_count": 41,
                "reference_plan": {
                    "primary_asset_id": identity["primary_asset_id"],
                    "supporting_asset_ids": [],
                    "completed_white_result_id": None,
                },
                "fact_trace": ["fact.missing"],
                "inference_trace": [],
                "rule_refs": [],
                "generation_parameters": {
                    "model": "gpt-image-2",
                    "n": 1,
                    "size": "1:1",
                    "resolution": "1k",
                },
                "review_required": True,
            },
            2,
            identity,
            ledger,
            set(),
        )

    vn_prompt = _normalize_n6_prompt(
        {
            "slot_id": "2",
            "main_scene": "kitchen",
            "main_action": "none",
            "visible_text_lines": ["餐具套装", "Bộ dụng cụ ăn uống"],
            "localized_copy": {
                "language": "vi-VN",
                "lines": ["餐具套装", "Bộ dụng cụ ăn uống"],
                "source_fact_refs": ["fact.name.001"],
                "source_inference_refs": [],
            },
            "prompt": "Accurate travel mug in one kitchen scene.",
            "character_count": 41,
            "reference_plan": {
                "primary_asset_id": identity["primary_asset_id"],
                "supporting_asset_ids": [],
                "completed_white_result_id": None,
            },
            "fact_trace": ["fact.name.001"],
            "inference_trace": [],
            "rule_refs": [],
            "generation_parameters": {
                "model": "gpt-image-2",
                "n": 1,
                "size": "1:1",
                "resolution": "1k",
            },
            "review_required": True,
        },
        2,
        identity,
        ledger,
        set(),
    )
    assert vn_prompt["visible_text_lines"] == ["Bộ dụng cụ ăn uống"]
    assert vn_prompt["localized_copy"]["lines"] == ["Bộ dụng cụ ăn uống"]


def test_n3_normalizer_repairs_unknown_evidence_refs_to_known_source():
    from platform_app.services import _normalize_n3_ledger

    ledger = _normalize_n3_ledger(
        {
            "ledger_version": "2.0.0",
            "facts": [
                {
                    "fact_id": "fact.observed.001",
                    "statement": "可见木色商品主体",
                    "fact_class": "observed",
                    "confidence": 0.8,
                    "evidence_refs": ["image"],
                    "risk_level": "low",
                    "allowed_uses": ["identity", "visual_prompt"],
                    "review_note": "",
                }
            ],
            "blocked_claim_topics": [],
            "unresolved_questions": [],
            "review_summary": {
                "confirmed_count": 0,
                "observed_count": 1,
                "inferred_count": 0,
                "high_risk_count": 0,
            },
        },
        known_evidence_refs={"product_name", "asset:asset-1"},
    )

    assert ledger["facts"][0]["evidence_refs"] == ["asset:asset-1"]


def test_rule_gate_warns_when_locked_copy_is_missing_or_changed():
    from platform_app.models import Batch, OutputTemplate
    from platform_app.services import evaluate_prompt_rule_gate

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Copy lock")
    template = OutputTemplate.objects.create(
        seed_key="copy-lock",
        platform="global",
        site="",
        name="Copy lock",
        version="2026.08",
    )
    slot = template.slots.create(order=2, name="Benefit", purpose="Benefit")

    gate = evaluate_prompt_rule_gate(
        batch,
        slot,
        'Show the product with visible text "Giữ gọn sau bữa".',
        visible_text_lines=["Giữ gọn sau bữa ăn"],
        localized_copy={"lines": ["Giữ gọn sau bữa ăn"], "source_fact_refs": ["fact.name.001"]},
        fact_ids={"fact.name.001"},
    )

    assert gate["decision"] == "pass"
    assert gate["hard_blocks"] == []
    assert "copy.literal_lock" in gate["warnings"]
    assert gate["copy_checks"]["each_line_present_once"] is False


def test_rule_gate_marks_generic_copy_for_one_rewrite_without_blocking():
    from platform_app.models import Batch, OutputTemplate
    from platform_app.services import evaluate_prompt_rule_gate

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Generic copy")
    template = OutputTemplate.objects.create(
        seed_key="generic-copy",
        platform="global",
        site="",
        name="Generic copy",
        version="2026.08",
    )
    slot = template.slots.create(order=3, name="Benefit", purpose="Benefit")

    gate = evaluate_prompt_rule_gate(
        batch,
        slot,
        'Commercial product image with visible text "Premium quality".',
        visible_text_lines=["Premium quality"],
        localized_copy={"lines": ["Premium quality"], "source_fact_refs": ["fact.name.001"]},
        fact_ids={"fact.name.001"},
    )

    assert gate["decision"] == "pass"
    assert gate["hard_blocks"] == []
    assert gate["rewrite_reasons"] == ["copy.generic_or_repeated"]
    assert gate["copy_checks"]["generic_phrase_hits"] == ["Premium quality"]


def test_rule_gate_warns_unknown_copy_fact_without_blocking_generation():
    from platform_app.models import Batch, OutputTemplate
    from platform_app.services import evaluate_prompt_rule_gate

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Unknown fact")
    template = OutputTemplate.objects.create(
        seed_key="unknown-fact",
        platform="global",
        site="",
        name="Unknown fact",
        version="2026.08",
    )
    slot = template.slots.create(order=4, name="Claim", purpose="Claim")

    gate = evaluate_prompt_rule_gate(
        batch,
        slot,
        'Commercial product image with visible text "Fits your small kitchen".',
        visible_text_lines=["Fits your small kitchen"],
        localized_copy={"lines": ["Fits your small kitchen"], "source_fact_refs": ["fact.cert.404"]},
        fact_ids={"fact.name.001"},
    )

    assert gate["decision"] == "pass"
    assert gate["hard_blocks"] == []
    assert "copy.unknown_fact_ref" in gate["warnings"]
    assert gate["rewrite_reasons"] == []


def test_rule_gate_warns_high_risk_visible_claims_without_blocking_generation():
    from platform_app.models import Batch, OutputTemplate
    from platform_app.services import evaluate_prompt_rule_gate

    user = make_user()
    batch = Batch.objects.create(owner=user, name="High risk copy")
    template = OutputTemplate.objects.create(
        seed_key="high-risk-copy",
        platform="global",
        site="",
        name="High risk copy",
        version="2026.08",
    )
    slot = template.slots.create(order=4, name="Claim", purpose="Claim")

    gate = evaluate_prompt_rule_gate(
        batch,
        slot,
        'Commercial product image with visible text "100% certified cure".',
        visible_text_lines=["100% certified cure"],
        localized_copy={"lines": ["100% certified cure"], "source_fact_refs": ["fact.name.001"]},
        fact_ids={"fact.name.001"},
    )

    assert gate["decision"] == "pass"
    assert gate["hard_blocks"] == []
    assert "copy.high_risk_claim" in gate["warnings"]


def test_final_n7_gate_receives_structured_localized_copy(tmp_path, settings):
    import json

    from platform_app.management.commands.seed_platform_templates import Command
    from platform_app.models import Batch, OutputTemplate
    from platform_app.services import FakeAPIMartClient, _run_final_n7_gate

    settings.MEDIA_ROOT = tmp_path
    settings.PROMPT_OS_SEMANTIC_N7_ENABLED = True
    Command().handle()
    user = make_user()
    batch = Batch.objects.create(owner=user, name="N7 localized copy")
    cluster = make_cluster(batch)
    template = OutputTemplate.objects.create(
        seed_key="n7-copy",
        platform="global",
        site="",
        name="N7 copy",
        version="2026.08",
    )
    slot = template.slots.create(order=5, name="Usage", purpose="Usage")

    class CapturingClient(FakeAPIMartClient):
        n7_input = None

        def optimize_prompt(self, payload):
            text = payload.get("text", "")
            if "NODE N7" in text:
                self.n7_input = json.loads(
                    next(line for line in reversed(text.splitlines()) if line.startswith("{"))
                )
            return super().optimize_prompt(payload)

    client = CapturingClient()
    _run_final_n7_gate(
        client,
        cluster,
        batch,
        slot,
        'Show product with visible text "Giữ gọn sau bữa ăn".',
        [],
        deterministic_gate={
            "decision": "pass",
            "hard_blocks": [],
            "semantic_risks": [],
            "warnings": [],
            "resolved_rule_refs": [],
            "prompt_checks": {},
            "copy_checks": {},
            "rewrite_reasons": [],
        },
        lineage={"cluster_id": str(cluster.id)},
        node_template_binding={"node_name": "N6.generic", "version": "3.1.0"},
        image_request={"model": "gpt-image-2", "n": 1, "size": "1:1", "resolution": "1k"},
        localized_copy={
            "language": "vi-VN",
            "lines": ["Giữ gọn sau bữa ăn"],
            "source_fact_refs": ["fact.name.001"],
        },
    )

    assert client.n7_input["localized_copy"]["language"] == "vi-VN"
    assert client.n7_input["localized_copy"]["lines"] == ["Giữ gọn sau bữa ăn"]


def test_prompt_worker_rewrites_generic_copy_once_before_saving_prompt(tmp_path, settings):
    import json

    from platform_app.management.commands.seed_platform_templates import Command
    from platform_app.models import Asset, Batch, OutputTemplate, PromptVersion
    from platform_app.services import LocalStorage, process_prompt_once, request_cluster_preparation

    settings.MEDIA_ROOT = tmp_path
    Command().handle()
    user = make_user()
    template = OutputTemplate.objects.create(
        seed_key="one-copy-rewrite",
        platform="global",
        site="",
        name="One copy rewrite",
        version="2026.08",
    )
    template.slots.create(order=1, name="标准白底产品图", purpose="standard white background product hero")
    template.slots.create(order=2, name="核心卖点图", purpose="benefit")
    batch = Batch.objects.create(owner=user, name="Rewrite batch", output_template=template)
    storage_path = f"originals/{batch.id}/source.png"
    (tmp_path / storage_path).parent.mkdir(parents=True)
    (tmp_path / storage_path).write_bytes(b"png-bytes")
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="source.png",
        storage_path=storage_path,
        sha256="c" * 64,
        file_size=9,
        content_type="image/png",
    )
    cluster = make_cluster(batch)
    cluster.cluster_assets.update(asset=asset)
    request_cluster_preparation(cluster, auto_generate=False)

    class RewriteClient:
        n6_calls = 0

        def observe_images(self, instruction, image_paths):
            return {
                "output_text": json.dumps(
                    strict_n1({
                        "asset_id": str(asset.id),
                        "asset_kind": "owned_product",
                        "image_role": "clean_product",
                        "contains_target_product": True,
                        "target_is_physical_product": True,
                        "target_visibility": 95,
                        "target_complete": True,
                        "background_complexity": "low",
                        "reference_quality": 95,
                        "observed_identity": {
                            "category_candidates": ["lunch box"],
                            "overall_shape": "rectangular lunch box",
                            "dominant_colors": ["cream"],
                        },
                        "candidate_product_name": "Lunch box",
                        "candidate_product_name_confidence": 90,
                        "recommended_use": "reuse",
                    })
                ),
                "raw": {},
            }

        def optimize_prompt(self, payload):
            text = payload["text"]
            if "NODE N2" in text:
                output = strict_n2({
                    "decision": "continue",
                    "confidence": 90,
                    "needs_input_reason": "",
                    "product_name": "Lunch box",
                    "conflict_state": "unknown",
                    "product_profile": {"category": "lunch box", "primary_appearance": "cream"},
                    "identity_lock": {
                        "family_invariants": ["rectangular lunch box"],
                        "primary_variant_attributes": ["cream"],
                        "must_not_change": ["single box"],
                    },
                    "primary_asset_id": str(asset.id),
                    "supporting_asset_ids": [],
                    "target_appearances": [{
                        "appearance_id": "appearance.primary",
                        "label": "cream lunch box",
                        "variant_attributes": ["cream"],
                        "asset_ids": [str(asset.id)],
                        "primary_asset_id": str(asset.id),
                    }],
                    "standardization_mode": "reuse",
                    "standardization_reason": "",
                })
            elif "NODE N3" in text:
                output = {
                    "ledger_version": "2.0.0",
                    "facts": [{
                        "fact_id": "fact.name.001",
                        "statement": "Lunch box",
                        "fact_class": "confirmed",
                        "confidence": 1,
                        "evidence_refs": ["product_name"],
                        "risk_level": "low",
                        "allowed_uses": ["identity", "visual_prompt", "consumer_copy"],
                        "review_note": "",
                    }],
                    "blocked_claim_topics": [],
                    "unresolved_questions": [],
                    "review_summary": {
                        "confirmed_count": 1,
                        "observed_count": 0,
                        "inferred_count": 0,
                        "high_risk_count": 0,
                    },
                }
            elif "NODE N4" in text:
                output = {
                    "slot_id": "1",
                    "main_scene": "white studio",
                    "main_action": "none",
                    "visible_text_lines": [],
                    "prompt": "Complete lunch box on pure white.",
                    "character_count": 33,
                    "reference_plan": {
                        "primary_asset_id": str(asset.id),
                        "supporting_asset_ids": [],
                        "include_completed_white_image": False,
                    },
                    "fact_trace": ["fact.name.001"],
                    "inference_trace": [],
                    "rule_refs": [],
                    "generation_parameters": {
                        "model": "gpt-image-2",
                        "n": 1,
                        "size": "1:1",
                        "resolution": "1k",
                    },
                    "review_required": True,
                }
            elif "NODE N5" in text:
                output = {"plans": [n5_plan(2, mode="fab_value", fact_refs=("fact.name.001",), appearance_ids=["appearance.primary"])]}
            elif "NODE N6" in text:
                self.n6_calls += 1
                line = "Premium quality" if self.n6_calls == 1 else "Lunch stays neatly packed"
                output = {
                    "slot_id": "2",
                    "main_scene": "office lunch table",
                    "main_action": "box opened neatly",
                    "visual_theme": "office lunch tidy moment",
                    "typography_plan": "top-left one-line English title, 32px bold sans-serif, dark green text, about 34% image width",
                    "display_prompt": "生成一张 1:1 Shopee 商品营销图，办公室午餐盒打开的一秒，左上角一行英文标题，暖白桌面光线清楚呈现餐盒结构。",
                    "visible_text_lines": [line],
                    "localized_copy": {
                        "language": "en",
                        "lines": [line],
                        "back_translation": "Keeps lunch organized",
                        "strategy_mode": "fab_value",
                        "source_fact_refs": ["fact.name.001"],
                        "source_inference_refs": [],
                        "quality": {
                            "relevance": 90,
                            "specificity": 90,
                            "imagery": 90,
                            "naturalness": 90,
                            "truthfulness": 90,
                            "mobile_readability": 90,
                            "generic_phrase_hits": [],
                        },
                    },
                    "prompt": f'Office lunch image with visible text "{line}".',
                    "character_count": 48,
                    "reference_plan": {
                        "primary_asset_id": str(asset.id),
                        "supporting_asset_ids": [],
                        "completed_white_result_id": None,
                    },
                    "fact_trace": ["fact.name.001"],
                    "inference_trace": [],
                    "rule_refs": [],
                    "generation_parameters": {
                        "model": "gpt-image-2",
                        "n": 1,
                        "size": "1:1",
                        "resolution": "1k",
                    },
                    "review_required": True,
                }
            elif "NODE N7" in text:
                output = {
                    "decision": "pass",
                    "hard_blocks": [],
                    "semantic_risks": [],
                    "warnings": [],
                    "prompt_checks": {
                        "character_count": 48,
                        "text_line_count": 1,
                        "main_scene_count": 1,
                        "main_action_count": 1,
                        "reference_assets_valid": True,
                    },
                    "copy_checks": {
                        "lines_match_visible_text": True,
                        "each_line_present_once": True,
                        "language_match": True,
                        "fact_refs_valid": True,
                        "generic_phrase_hits": [],
                    },
                    "resolved_rule_refs": [],
                    "review_required": True,
                }
            else:
                raise AssertionError(text)
            return {"output_text": json.dumps(output), "raw": {}}

    client = RewriteClient()
    assert process_prompt_once(client, LocalStorage(tmp_path)) == 1

    cluster.refresh_from_db()
    assert cluster.preparation_status == cluster.PreparationStatus.READY, cluster.preparation_error
    prompt = PromptVersion.objects.get(cluster=cluster, output_slot__order=2)
    assert client.n6_calls == 2
    assert "Lunch stays neatly packed" in prompt.prompt_text
    assert "Premium quality" not in prompt.prompt_text


def test_prompt_worker_waits_for_configuration_then_reuses_current_identity(
    tmp_path,
    settings,
):
    import json

    from platform_app.models import Asset, Batch, Cluster, OutputSlot, OutputTemplate, PromptVersion
    from platform_app.services import (
        LocalStorage,
        process_prompt_once,
        request_cluster_preparation,
        update_project_settings,
    )

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    template = OutputTemplate.objects.create(
        seed_key="global-marketplace-baseline-template",
        platform="global",
        site="",
        name="Identity first",
        status=OutputTemplate.Status.PUBLISHED,
    )
    OutputSlot.objects.create(template=template, name="Hero", order=1)
    batch = Batch.objects.create(
        owner=user,
        name="Needs market",
        platform="",
        site="",
        market="",
        output_template=template,
    )
    storage_path = f"originals/{batch.id}/source.png"
    (tmp_path / storage_path).parent.mkdir(parents=True)
    (tmp_path / storage_path).write_bytes(b"image")
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="source.png",
        storage_path=storage_path,
        sha256="1" * 64,
        file_size=5,
        content_type="image/png",
    )
    cluster = Cluster.create_for_asset(batch, asset)
    request_cluster_preparation(cluster, auto_generate=False)

    class IdentityClient:
        def __init__(self):
            self.n1_calls = 0
            self.n2_calls = 0
            self.later_calls = []

        def observe_images(self, instruction, image_paths):
            self.n1_calls += 1
            return {
                "output_text": json.dumps(
                    strict_n1({
                        "asset_id": str(asset.id),
                        "asset_kind": "owned_product",
                        "image_role": "clean_product",
                        "contains_target_product": True,
                        "target_is_physical_product": True,
                        "target_visibility": 95,
                        "target_complete": True,
                        "background_complexity": "low",
                        "observed_identity": {
                            "category_candidates": ["travel mug"],
                            "overall_shape": "cylindrical mug with lid",
                        },
                        "reference_quality": 95,
                        "recommended_use": "reuse",
                        "candidate_product_name": "Travel mug",
                        "candidate_product_name_confidence": 94,
                    })
                )
            }

        def optimize_prompt(self, payload):
            text = payload.get("text", "")
            if "NODE N2" in text:
                self.n2_calls += 1
                output = strict_n2({
                    "decision": "continue",
                    "product_name": "Travel mug",
                    "confidence": 93,
                    "needs_input_reason": "",
                    "conflict_state": "unknown",
                    "primary_asset_id": str(asset.id),
                    "supporting_asset_ids": [],
                    "standardization_mode": "reuse",
                    "standardization_reason": "",
                    "identity_lock": {"must_not_change": ["lid"]},
                    "product_profile": {
                        "category": "travel mug",
                        "primary_appearance": "visible travel mug",
                    },
                })
            elif "NODE N3" in text:
                self.later_calls.append("N3")
                output = {
                    "ledger_version": "2.0.0",
                    "facts": [
                        {
                            "fact_id": "fact.name.001",
                            "statement": "Travel mug",
                            "fact_class": "confirmed",
                            "confidence": 1,
                            "evidence_refs": ["product_name"],
                            "risk_level": "low",
                            "allowed_uses": ["identity", "visual_prompt", "consumer_copy"],
                            "review_note": "",
                        }
                    ],
                    "blocked_claim_topics": ["price"],
                    "unresolved_questions": [],
                    "review_summary": {
                        "confirmed_count": 1,
                        "observed_count": 0,
                        "inferred_count": 0,
                        "high_risk_count": 0,
                    },
                }
            elif "NODE N4" in text:
                self.later_calls.append("N4")
                output = {
                    "slot_id": "1",
                    "main_scene": "pure white commercial studio",
                    "main_action": "none",
                    "visible_text_lines": [],
                    "prompt": "Accurate travel mug on pure white.",
                    "character_count": 34,
                    "reference_plan": {
                        "primary_asset_id": str(asset.id),
                        "supporting_asset_ids": [],
                        "include_completed_white_image": False,
                    },
                    "fact_trace": ["fact.name.001"],
                    "inference_trace": [],
                    "rule_refs": [],
                    "generation_parameters": {
                        "model": "gpt-image-2",
                        "n": 1,
                        "size": "1:1",
                        "resolution": "1k",
                    },
                    "review_required": True,
                }
            elif "NODE N7" in text:
                self.later_calls.append("N7")
                output = {
                    "decision": "pass",
                    "hard_blocks": [],
                    "semantic_risks": [],
                    "warnings": [],
                    "advice": [],
                    "resolved_rule_refs": [],
                    "inference_disclosures": [],
                    "prompt_checks": {},
                    "copy_checks": {
                        "lines_match_visible_text": True,
                        "each_line_present_once": True,
                        "language_match": True,
                        "fact_refs_valid": True,
                        "generic_phrase_hits": [],
                    },
                    "review_required": True,
                }
            else:
                raise AssertionError(text)
            return {"output_text": json.dumps(output)}

    provider = IdentityClient()
    assert process_prompt_once(provider, LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()

    assert cluster.preparation_status == Cluster.PreparationStatus.BLOCKED
    assert cluster.analysis_snapshot["readiness"]["code"] == "configuration_required"
    assert [item["node_id"] for item in cluster.analysis_snapshot["prompt_os"]] == ["N1", "N2"]
    assert provider.n1_calls == 1
    assert provider.n2_calls == 1
    assert provider.later_calls == []
    assert PromptVersion.objects.filter(cluster=cluster).count() == 0

    update_project_settings(
        batch,
        {
            "platform": "shopee",
            "market": "SG",
            "seller_tier": "general",
            "size": "1:1",
            "resolution": "1k",
        },
    )
    cluster.refresh_from_db()
    assert cluster.preparation_status == Cluster.PreparationStatus.DRAFT
    request_cluster_preparation(cluster, auto_generate=False)
    cluster.refresh_from_db()
    assert cluster.preparation_status == Cluster.PreparationStatus.PENDING

    assert process_prompt_once(provider, LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()
    assert cluster.preparation_status == Cluster.PreparationStatus.READY, cluster.preparation_error
    assert provider.n1_calls == 1
    assert provider.n2_calls == 1
    assert provider.later_calls == ["N3", "N4"]
    prompt = PromptVersion.objects.get(cluster=cluster)
    assert prompt.input_snapshot["_preparation_revision"] == 1
    assert prompt.input_snapshot["_effective_config_signature"]
    assert (
        prompt.evaluation["rule_gate"]["effective_config_signature"]
        == prompt.input_snapshot["_effective_config_signature"]
    )

    settings.APIMART_VISION_MODEL = "gpt-5-nano-next"
    request_cluster_preparation(cluster, auto_generate=False)
    assert process_prompt_once(provider, LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()
    assert cluster.preparation_status == Cluster.PreparationStatus.READY, cluster.preparation_error
    assert provider.n1_calls == 2
    assert provider.n2_calls == 2


def test_prompt_worker_keeps_generating_when_n2_reports_visual_identity_conflict(
    tmp_path,
    settings,
):
    import json

    from django.core.management import call_command

    from platform_app.models import Asset, Batch, Cluster, OutputSlot, OutputTemplate, PromptVersion
    from platform_app.services import FakeAPIMartClient, LocalStorage, process_prompt_once, request_cluster_preparation

    settings.MEDIA_ROOT = tmp_path
    call_command("seed_platform_templates")
    user = make_user()
    template = OutputTemplate.objects.create(
        platform="global",
        site="",
        name="Conflict",
        status=OutputTemplate.Status.PUBLISHED,
    )
    OutputSlot.objects.create(template=template, name="Hero", order=1)
    batch = Batch.objects.create(
        owner=user,
        name="Conflict",
        platform="shopee",
        site="SG",
        market="SG",
        output_template=template,
    )
    storage_path = f"originals/{batch.id}/source.png"
    (tmp_path / storage_path).parent.mkdir(parents=True)
    (tmp_path / storage_path).write_bytes(b"image")
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="source.png",
        storage_path=storage_path,
        sha256="2" * 64,
        file_size=5,
        content_type="image/png",
    )
    cluster = Cluster.create_for_asset(batch, asset)
    cluster.product_name = "ERP electric kettle"
    cluster.save(update_fields=["product_name"])
    request_cluster_preparation(cluster, auto_generate=False)

    class ConflictClient(FakeAPIMartClient):
        def observe_images(self, instruction, image_paths):
            return {
                "output_text": json.dumps(
                    strict_n1({
                        "asset_id": str(asset.id),
                        "asset_kind": "owned_product",
                        "image_role": "clean_product",
                        "contains_target_product": True,
                        "target_is_physical_product": True,
                        "target_visibility": 90,
                        "target_complete": True,
                        "background_complexity": "low",
                        "observed_identity": {
                            "category_candidates": ["shoe"],
                            "overall_shape": "running shoe upper and sole",
                        },
                        "reference_quality": 90,
                        "recommended_use": "reuse",
                        "candidate_product_name": "Running shoe",
                        "candidate_product_name_confidence": 95,
                    })
                )
            }

        def optimize_prompt(self, payload):
            if "NODE N2" not in payload.get("text", ""):
                return super().optimize_prompt(payload)
            return {
                "output_text": json.dumps(
                    strict_n2({
                        "decision": "continue",
                        "product_name": "Running shoe",
                        "confidence": 96,
                        "needs_input_reason": "",
                        "conflict_state": "conflict",
                        "primary_asset_id": str(asset.id),
                        "supporting_asset_ids": [],
                        "target_appearances": [{
                            "appearance_id": "appearance.primary",
                            "label": "visible running shoe",
                            "variant_attributes": [],
                            "asset_ids": [str(asset.id)],
                            "primary_asset_id": str(asset.id),
                        }],
                        "standardization_mode": "reuse",
                        "standardization_reason": "",
                        "identity_lock": {"must_not_change": ["shoe upper"]},
                        "product_profile": {
                            "category": "shoe",
                            "primary_appearance": "visible running shoe",
                        },
                    })
                )
            }

    assert process_prompt_once(ConflictClient(), LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()

    assert cluster.preparation_status == Cluster.PreparationStatus.READY, cluster.preparation_error
    assert cluster.product_name == "ERP electric kettle"
    assert cluster.analysis_snapshot["identity"]["conflict_state"] == "conflict"
    assert cluster.analysis_snapshot["readiness_warning"]["code"] == "identity_conflict_review"
    assert PromptVersion.objects.filter(cluster=cluster).count() == 1


def test_n1_target_observation_fills_missing_visible_identity():
    from platform_app.services import _normalize_n1_observation

    asset_id = "11111111-1111-1111-1111-111111111111"

    normalized = _normalize_n1_observation(
        {
            "asset_id": asset_id,
            "asset_kind": "owned_product",
            "image_role": "clean_product",
            "contains_target_product": True,
            "target_is_physical_product": True,
            "target_visibility": 92,
            "target_complete": True,
            "background_complexity": "low",
            "observed_identity": {"category_candidates": []},
            "reference_quality": 92,
            "recommended_use": "reuse",
            "candidate_product_name": "Travel mug",
            "candidate_product_name_confidence": 0.9,
        },
        asset_id,
    )
    assert normalized["observed_identity"]["category_candidates"] == ["Travel mug"]
    assert normalized["observed_identity"]["overall_shape"] == "Travel mug"


def test_n1_cleans_schema_placeholder_strings_as_identity_evidence():
    from platform_app.services import _normalize_n1_observation

    asset_id = "11111111-1111-1111-1111-111111111111"

    normalized = _normalize_n1_observation(
        strict_n1({
            "asset_id": asset_id,
            "asset_kind": "owned_product",
            "image_role": "clean_product",
            "contains_target_product": True,
            "target_is_physical_product": True,
            "target_visibility": 0,
            "target_complete": True,
            "background_complexity": "low",
            "observed_identity": {
                "category_candidates": ["string"],
                "overall_shape": "string",
            },
            "reference_quality": 0,
            "recommended_use": "reuse",
            "candidate_product_name": "string",
            "candidate_product_name_confidence": 0,
        }),
        asset_id,
    )
    assert normalized["candidate_product_name"] == ""
    assert normalized["observed_identity"]["category_candidates"] == ["可见商品"]
    assert normalized["observed_identity"]["overall_shape"] == "可见商品"


@pytest.mark.parametrize(
    ("role", "normalized_role"),
    [
        ("product", "clean_product"),
        ("product_detail", "detail"),
        ("packaging", "packaging"),
    ],
)
def test_n1_normalizes_common_owned_product_image_roles(role, normalized_role):
    from platform_app.services import _normalize_n1_observation

    asset_id = "11111111-1111-1111-1111-111111111111"
    payload = strict_n1({
        "asset_id": asset_id,
        "asset_kind": "owned_product",
        "image_role": role,
        "contains_target_product": True,
        "target_is_physical_product": True,
        "target_visibility": 92,
        "target_complete": True,
        "background_complexity": "low",
        "observed_identity": {
            "category_candidates": ["chopsticks set"],
            "overall_shape": "two chopsticks and a spoon in slim trays",
        },
        "reference_quality": 90,
        "recommended_use": "reuse",
        "candidate_product_name": "Chopsticks set",
        "candidate_product_name_confidence": 0.9,
    })

    normalized = _normalize_n1_observation(payload, asset_id)

    assert normalized["image_role"] == normalized_role


def test_n1_falls_back_unknown_owned_role_when_product_is_visible():
    from platform_app.services import _normalize_n1_observation

    asset_id = "11111111-1111-1111-1111-111111111111"
    payload = strict_n1({
        "asset_id": asset_id,
        "asset_kind": "owned_product",
        "image_role": "owned_product_reference",
        "contains_target_product": True,
        "target_is_physical_product": True,
        "target_visibility": 92,
        "target_complete": True,
        "background_complexity": "low",
        "observed_identity": {
            "category_candidates": ["chopsticks set"],
            "overall_shape": "two chopsticks and a spoon in slim trays",
        },
        "reference_quality": 90,
        "recommended_use": "reuse",
        "candidate_product_name": "Chopsticks set",
        "candidate_product_name_confidence": 0.9,
    })

    normalized = _normalize_n1_observation(payload, asset_id)

    assert normalized["image_role"] == "clean_product"


def test_n1_tolerates_provider_short_json_when_product_is_visible():
    from platform_app.services import _normalize_n1_observation

    asset_id = "11111111-1111-1111-1111-111111111111"
    normalized = _normalize_n1_observation(
        {
            "asset_id": asset_id,
            "asset_kind": "product",
            "image_role": "ecommerce listing photo",
            "contains_target_product": True,
            "target_visibility": 0.92,
            "reference_quality": 0.8,
            "observed_identity": {
                "category_candidates": ["餐具套装"],
                "overall_shape": "两套餐具放在收纳盒中",
            },
            "candidate_product_name": "餐具套装",
            "candidate_product_name_confidence": 0.9,
        },
        asset_id,
    )

    assert normalized["asset_kind"] == "owned_product"
    assert normalized["image_role"] == "clean_product"
    assert normalized["target_visibility"] == 92
    assert normalized["reference_quality"] == 80
    assert normalized["observed_identity"]["dominant_colors"] == []


def test_n2_fills_incomplete_identity_when_valid_product_images_exist():
    from platform_app.services import _n2_observation_fallbacks, _normalize_n2_identity

    observations = [
        {
            "asset_id": "asset-1",
            "contains_target_product": True,
            "target_is_physical_product": True,
            "candidate_product_name": "木质餐具套装",
            "candidate_product_name_confidence": 0.85,
            "observed_identity": {
                "category_candidates": ["餐具套装"],
                "overall_shape": "木勺与筷子收纳在盒内",
                "dominant_colors": ["木色"],
            },
        },
        {
            "asset_id": "asset-2",
            "contains_target_product": True,
            "target_is_physical_product": True,
            "candidate_product_name": "深色餐具套装",
            "candidate_product_name_confidence": 0.75,
            "observed_identity": {
                "category_candidates": ["餐具套装"],
                "overall_shape": "深色款餐具组合",
                "dominant_colors": ["深棕色"],
            },
        },
    ]
    repaired = _n2_observation_fallbacks(
        {
            "decision": "needs_input",
            "needs_input_reason": "not sure",
            "product_name": "string",
            "confidence": 0.4,
            "conflict_state": "uncertain",
            "product_profile": {},
            "identity_lock": {},
            "primary_asset_id": "bad",
            "supporting_asset_ids": ["bad"],
            "target_appearances": [{"appearance_id": "bad", "asset_ids": ["bad"]}],
            "standardization_mode": "string",
            "standardization_reason": "",
        },
        {"product_name": "", "observations": observations},
    )

    normalized = _normalize_n2_identity(
        repaired,
        {"asset-1", "asset-2"},
        required_primary_asset_id="asset-1",
        require_continue_when_valid=True,
    )

    assert normalized["decision"] == "continue"
    assert normalized["primary_asset_id"] == "asset-1"
    assert [item["primary_asset_id"] for item in normalized["target_appearances"]] == ["asset-1", "asset-2"]


def test_uncertain_n1_continues_with_visible_image_reference(tmp_path, settings):
    import json

    from django.core.management import call_command

    from platform_app.models import Asset, Batch, Cluster
    from platform_app.services import FakeAPIMartClient, LocalStorage, process_prompt_once, request_cluster_preparation

    settings.MEDIA_ROOT = tmp_path
    call_command("seed_platform_templates")
    user = make_user()
    batch = Batch.objects.create(owner=user, name="Blocked identity")
    storage_path = f"originals/{batch.id}/source.png"
    (tmp_path / storage_path).parent.mkdir(parents=True)
    (tmp_path / storage_path).write_bytes(b"png-bytes")
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="source.png",
        storage_path=storage_path,
        sha256="b" * 64,
        file_size=9,
        content_type="image/png",
    )
    cluster = Cluster.create_for_asset(batch, asset)
    request_cluster_preparation(cluster, auto_generate=False)

    class PlaceholderIdentityClient(FakeAPIMartClient):
        def observe_images(self, instruction, image_paths):
            observed_asset_id = instruction.split("ASSET_ID=", 1)[1].splitlines()[0]
            return {
                "output_text": json.dumps(
                    strict_n1({
                        "asset_id": observed_asset_id,
                        "asset_kind": "owned_product",
                        "image_role": "clean_product",
                        "contains_target_product": False,
                        "target_is_physical_product": False,
                        "target_visibility": 0,
                        "target_complete": False,
                        "background_complexity": "low",
                        "observed_identity": {
                            "category_candidates": ["chopsticks set"],
                            "overall_shape": "two wooden chopsticks with a spoon in trays",
                        },
                        "reference_quality": 90,
                        "recommended_use": "reuse",
                        "candidate_product_name": "Chopsticks set",
                        "candidate_product_name_confidence": 0.9,
                    })
                ),
                "raw": {},
            }

        def optimize_prompt(self, payload):
            if "NODE N2" in payload["text"]:
                return {
                    "output_text": json.dumps(
                        strict_n2({
                            "decision": "needs_input",
                            "confidence": 0,
                            "needs_input_reason": "Need a human product name.",
                            "product_name": "string",
                            "conflict_state": "unknown",
                            "product_profile": {
                                "category": "string",
                                "primary_appearance": "",
                            },
                            "identity_lock": {"must_not_change": []},
                            "primary_asset_id": None,
                            "supporting_asset_ids": [],
                            "target_appearances": [],
                            "standardization_mode": "reuse",
                            "standardization_reason": "",
                        })
                    ),
                    "raw": {},
                }
            return super().optimize_prompt(payload)

    assert process_prompt_once(PlaceholderIdentityClient(), LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()

    assert cluster.preparation_status == Cluster.PreparationStatus.READY, cluster.preparation_error
    assert cluster.product_name == "餐具套装"
    assert cluster.name != "string"
    assert cluster.analysis_snapshot["observations"][0]["contains_target_product"] is True
    assert cluster.analysis_snapshot["identity"]["decision"] == "continue"


def test_empty_prompt_node_responses_fall_back_to_usable_prompts(tmp_path, settings):
    from django.core.management import call_command

    from platform_app.models import Asset, Batch, Cluster, PromptVersion
    from platform_app.services import FakeAPIMartClient, LocalStorage, process_prompt_once, request_cluster_preparation

    settings.MEDIA_ROOT = tmp_path
    call_command("seed_platform_templates")
    user = make_user()
    batch = Batch.objects.create(owner=user, name="Empty node response")
    storage_path = f"originals/{batch.id}/source.png"
    (tmp_path / storage_path).parent.mkdir(parents=True)
    (tmp_path / storage_path).write_bytes(b"png-bytes")
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="source.png",
        storage_path=storage_path,
        sha256="c" * 64,
        file_size=9,
        content_type="image/png",
    )
    cluster = Cluster.create_for_asset(batch, asset)
    cluster.product_name = "餐具套装"
    cluster.save(update_fields=["product_name"])
    request_cluster_preparation(cluster, auto_generate=False)

    class EmptyNodeClient(FakeAPIMartClient):
        def observe_images(self, instruction, image_paths):
            return {"output_text": "", "raw": {}}

        def optimize_prompt(self, payload):
            return {"output_text": "", "raw": {}}

    assert process_prompt_once(EmptyNodeClient(), LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()

    assert cluster.preparation_status == Cluster.PreparationStatus.READY, cluster.preparation_error
    assert cluster.preparation_error == ""
    assert cluster.analysis_snapshot["identity"]["decision"] == "continue"
    prompts = list(PromptVersion.objects.filter(cluster=cluster).order_by("output_slot__order"))
    assert len(prompts) == 9
    marketing_text = "\n".join(prompt.prompt_text for prompt in prompts[2:])
    assert "show product value through action" not in marketing_text
    assert "one clear creative ecommerce scene" not in marketing_text
    assert "Product identity:" in marketing_text
    assert "Visual theme:" in marketing_text
    assert "Specific moment:" in marketing_text


def test_n2_continue_requires_nonempty_identity_lock_and_product_profile():
    from platform_app.services import _normalize_n2_identity

    asset_id = "11111111-1111-1111-1111-111111111111"
    payload = {
        "decision": "continue",
        "product_name": "Travel mug",
        "confidence": 0.9,
        "needs_input_reason": "",
        "conflict_state": "match",
        "primary_asset_id": asset_id,
        "supporting_asset_ids": [],
        "identity_lock": {"must_not_change": []},
        "product_profile": {"category": ""},
        "standardization_mode": "reuse",
        "standardization_reason": "",
    }

    with pytest.raises(ValueError, match="identity_lock"):
        _normalize_n2_identity(payload, {asset_id})

    payload["identity_lock"] = {"must_not_change": ["Keep the visible handle"]}
    with pytest.raises(ValueError, match="product_profile"):
        _normalize_n2_identity(payload, {asset_id})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "observed_identity",
            {"category_candidates": [123], "overall_shape": "round"},
            "category_candidates",
        ),
        (
            "observed_identity",
            {"category_candidates": ["travel mug"], "overall_shape": ""},
            "overall_shape",
        ),
        ("target_visibility", 91.5, "target_visibility"),
    ],
)
def test_n1_repairs_weak_owned_product_identity_schema(field, value, message):
    from platform_app.services import _normalize_n1_observation

    asset_id = "11111111-1111-1111-1111-111111111111"
    payload = {
        "asset_id": asset_id,
        "asset_kind": "owned_product",
        "image_role": "clean_product",
        "contains_target_product": True,
        "target_is_physical_product": True,
        "target_visibility": 92,
        "target_complete": True,
        "background_complexity": "low",
        "observed_identity": {
            "category_candidates": ["travel mug"],
            "overall_shape": "cylindrical body with a handle",
        },
        "reference_quality": 92,
        "recommended_use": "reuse",
        "candidate_product_name": "Travel mug",
        "candidate_product_name_confidence": 0.9,
    }
    payload[field] = value

    normalized = _normalize_n1_observation(payload, asset_id)

    assert normalized["contains_target_product"] is True
    assert normalized["observed_identity"]["category_candidates"]
    assert normalized["observed_identity"]["overall_shape"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("asset_kind", None, "asset_kind"),
        ("asset_kind", "competitor_style", "asset_kind"),
        ("target_is_physical_product", None, "target_is_physical_product"),
        ("target_is_physical_product", "yes", "target_is_physical_product"),
        ("target_complete", None, "target_complete"),
        ("target_complete", 1, "target_complete"),
        ("background_complexity", None, "background_complexity"),
        ("background_complexity", "busy", "background_complexity"),
        ("recommended_use", None, "recommended_use"),
        ("recommended_use", "copy_brand", "recommended_use"),
    ],
)
def test_n1_reviewer_payload_requires_authoritative_owned_product_fields(
    field,
    value,
    message,
):
    from platform_app.services import _normalize_n1_observation

    asset_id = "11111111-1111-1111-1111-111111111111"
    payload = {
        "asset_id": asset_id,
        "asset_kind": "owned_product",
        "image_role": "clean_product",
        "contains_target_product": True,
        "target_is_physical_product": True,
        "target_visibility": 92,
        "target_complete": True,
        "reference_quality": 92,
        "background_complexity": "low",
        "observed_identity": {
            "category_candidates": ["travel mug"],
            "overall_shape": "cylindrical body with a handle",
        },
        "recommended_use": "reuse",
        "candidate_product_name": "Travel mug",
        "candidate_product_name_confidence": 0.9,
    }
    if value is None:
        payload.pop(field)
    else:
        payload[field] = value

    normalized = _normalize_n1_observation(payload, asset_id)

    assert normalized["contains_target_product"] is True
    assert normalized["observed_identity"]["category_candidates"]
    assert normalized["observed_identity"]["overall_shape"]


def test_prompt_worker_falls_back_to_n1_identity_when_n2_returns_schema_placeholders(
    tmp_path,
    settings,
):
    import json
    from io import BytesIO

    from django.core.management import call_command
    from PIL import Image

    from platform_app.models import Cluster
    from platform_app.services import (
        FakeAPIMartClient,
        LocalStorage,
        create_project,
        process_prompt_once,
        register_uploaded_asset,
        request_cluster_preparation,
    )

    settings.MEDIA_ROOT = tmp_path
    call_command("seed_platform_templates")
    user = make_user()
    batch = create_project(user, name="Placeholder fallback")
    image = BytesIO()
    Image.new("RGB", (8, 8), "white").save(image, "PNG")
    asset = register_uploaded_asset(batch, "chopsticks.png", image.getvalue(), "image/png")
    cluster = asset.clusters.get()
    request_cluster_preparation(cluster, auto_generate=False)

    class PlaceholderN2Client(FakeAPIMartClient):
        def observe_images(self, instruction, image_paths):
            asset_id = instruction.split("ASSET_ID=", 1)[1].splitlines()[0]
            return {
                "output_text": json.dumps(strict_n1({
                    "asset_id": asset_id,
                    "asset_kind": "owned_product",
                    "image_role": "product",
                    "contains_target_product": True,
                    "target_is_physical_product": True,
                    "target_visibility": 0,
                    "target_complete": True,
                    "background_complexity": "low",
                    "observed_identity": {
                        "category_candidates": ["chopsticks set"],
                        "overall_shape": "two chopsticks and a spoon in slim trays",
                    },
                    "reference_quality": 0,
                    "recommended_use": "semantic_extract_source",
                    "candidate_product_name": "Chopsticks set",
                    "candidate_product_name_confidence": 0.9,
                })),
                "raw": {},
            }

        def optimize_prompt(self, payload):
            if "NODE N2" in payload.get("text", ""):
                return {
                    "output_text": json.dumps(strict_n2({
                        "decision": "continue",
                        "product_name": "string",
                        "confidence": 0.9,
                        "needs_input_reason": "",
                        "conflict_state": "unknown",
                        "primary_asset_id": str(asset.id),
                        "supporting_asset_ids": [],
                        "target_appearances": [{
                            "appearance_id": "appearance.primary",
                            "label": "string",
                            "variant_attributes": ["string"],
                            "asset_ids": [str(asset.id)],
                            "primary_asset_id": str(asset.id),
                        }],
                        "identity_lock": {"must_not_change": ["string"]},
                        "product_profile": {
                            "category": "string",
                            "primary_appearance": "string",
                        },
                        "standardization_mode": "reuse",
                        "standardization_reason": "",
                    })),
                    "raw": {},
                }
            return super().optimize_prompt(payload)

    assert process_prompt_once(PlaceholderN2Client(), LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()

    assert cluster.preparation_status == Cluster.PreparationStatus.READY
    assert cluster.product_name == "Chopsticks set"
    assert cluster.analysis_snapshot["identity"]["product_profile"]["category"] == "chopsticks set"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("needs_input_reason", None, "needs_input_reason"),
        ("needs_input_reason", 7, "needs_input_reason"),
        ("standardization_mode", None, "standardization_mode"),
        ("standardization_mode", "invented", "standardization_mode"),
        ("standardization_reason", None, "standardization_reason"),
        ("standardization_reason", [], "standardization_reason"),
    ],
)
def test_n2_reviewer_payload_requires_authoritative_decision_fields(
    field,
    value,
    message,
):
    from platform_app.services import _normalize_n2_identity

    asset_id = "11111111-1111-1111-1111-111111111111"
    payload = {
        "decision": "continue",
        "confidence": 91,
        "needs_input_reason": "",
        "product_name": "Travel mug",
        "conflict_state": "match",
        "product_profile": {
            "category": "travel mug",
            "primary_appearance": "sage travel mug",
        },
        "identity_lock": {"must_not_change": ["visible handle"]},
        "primary_asset_id": asset_id,
        "supporting_asset_ids": [],
        "standardization_mode": "reuse",
        "standardization_reason": "",
    }
    if value is None:
        payload.pop(field)
    else:
        payload[field] = value

    with pytest.raises(ValueError, match=message):
        _normalize_n2_identity(payload, {asset_id})


@pytest.mark.parametrize(
    ("product_profile", "identity_lock", "message"),
    [
        (
            {"unrelated": "text"},
            {"must_not_change": ["visible handle"]},
            "category",
        ),
        (
            {"category": True, "primary_appearance": 3},
            {"must_not_change": ["visible handle"]},
            "category",
        ),
        (
            {"category": "travel mug", "primary_appearance": "sage green"},
            {"unrelated": True},
            "must_not_change",
        ),
        (
            {"category": "travel mug", "primary_appearance": "sage green"},
            {"must_not_change": [1]},
            "must_not_change",
        ),
    ],
)
def test_n2_continue_requires_named_string_identity_fields(
    product_profile,
    identity_lock,
    message,
):
    from platform_app.services import _normalize_n2_identity

    asset_id = "11111111-1111-1111-1111-111111111111"
    payload = {
        "decision": "continue",
        "product_name": "Travel mug",
        "confidence": 0.9,
        "needs_input_reason": "",
        "conflict_state": "match",
        "primary_asset_id": asset_id,
        "supporting_asset_ids": [],
        "identity_lock": identity_lock,
        "product_profile": product_profile,
        "standardization_mode": "reuse",
        "standardization_reason": "",
    }

    with pytest.raises(ValueError, match=message):
        _normalize_n2_identity(payload, {asset_id})


def test_n2_normalizes_target_appearances_and_keeps_all_valid_variants():
    from platform_app.services import _normalize_n2_identity

    first = "11111111-1111-1111-1111-111111111111"
    second = "22222222-2222-2222-2222-222222222222"
    payload = strict_n2({
        "decision": "continue",
        "product_name": "Travel mug set",
        "confidence": 0.9,
        "needs_input_reason": "",
        "conflict_state": "match",
        "primary_asset_id": first,
        "supporting_asset_ids": [second],
        "target_appearances": [
            {"appearance_id": "appearance.green", "label": "green", "variant_attributes": ["green"], "asset_ids": [first], "primary_asset_id": first},
            {"appearance_id": "appearance.blue", "label": "blue", "variant_attributes": ["blue"], "asset_ids": [second], "primary_asset_id": second},
        ],
        "identity_lock": {"must_not_change": ["cup shape"]},
        "product_profile": {"category": "travel mug", "primary_appearance": "green and blue variants"},
        "standardization_mode": "reuse",
        "standardization_reason": "",
    })

    normalized = _normalize_n2_identity(payload, {first, second}, required_primary_asset_id=first)

    assert [item["appearance_id"] for item in normalized["target_appearances"]] == ["appearance.green", "appearance.blue"]
    assert {asset for item in normalized["target_appearances"] for asset in item["asset_ids"]} == {first, second}


def test_n2_explicit_target_appearances_must_assign_every_valid_product_image():
    from platform_app.services import _normalize_n2_identity

    first = "11111111-1111-1111-1111-111111111111"
    second = "22222222-2222-2222-2222-222222222222"
    payload = strict_n2({
        "decision": "continue",
        "product_name": "Travel mug set",
        "confidence": 0.9,
        "needs_input_reason": "",
        "conflict_state": "match",
        "primary_asset_id": first,
        "supporting_asset_ids": [second],
        "target_appearances": [{
            "appearance_id": "appearance.green",
            "label": "green",
            "variant_attributes": ["green"],
            "asset_ids": [first],
            "primary_asset_id": first,
        }],
        "identity_lock": {"must_not_change": ["cup shape"]},
        "product_profile": {"category": "travel mug", "primary_appearance": "green and blue variants"},
        "standardization_mode": "reuse",
        "standardization_reason": "",
    })

    with pytest.raises(ValueError, match="every valid product image"):
        _normalize_n2_identity(payload, {first, second}, required_primary_asset_id=first)


def test_n2_fallback_continues_when_n1_found_a_valid_product_image():
    from platform_app.services import _n2_observation_fallbacks, _normalize_n2_identity

    asset_id = "11111111-1111-1111-1111-111111111111"
    payload = strict_n2({
        "decision": "needs_input",
        "product_name": "",
        "confidence": 0,
        "needs_input_reason": "Unsure",
        "conflict_state": "unknown",
        "primary_asset_id": None,
        "supporting_asset_ids": [],
        "target_appearances": [],
        "identity_lock": {"must_not_change": []},
        "product_profile": {"category": "", "primary_appearance": ""},
        "standardization_mode": "reuse",
        "standardization_reason": "",
    })
    identity_input = {
        "product_name": "",
        "confirmed_points": [],
        "relation_type": "single_product",
        "observations": [
            strict_n1({
                "asset_id": asset_id,
                "asset_kind": "owned_product",
                "image_role": "owned_product_reference",
                "contains_target_product": True,
                "target_is_physical_product": True,
                "target_visibility": 92,
                "target_complete": True,
                "background_complexity": "low",
                "observed_identity": {
                    "category_candidates": ["chopsticks set"],
                    "dominant_colors": ["wood brown"],
                    "overall_shape": "two chopsticks and a spoon in slim trays",
                },
                "reference_quality": 90,
                "recommended_use": "reuse",
                "candidate_product_name": "Chopsticks set",
                "candidate_product_name_confidence": 0.9,
            })
        ],
        "max_supporting_images": 3,
    }

    normalized = _normalize_n2_identity(
        _n2_observation_fallbacks(payload, identity_input),
        {asset_id},
        required_primary_asset_id=asset_id,
        require_continue_when_valid=True,
    )

    assert normalized["decision"] == "continue"
    assert normalized["primary_asset_id"] == asset_id
    assert normalized["product_name"] == "Chopsticks set"
    assert normalized["target_appearances"][0]["asset_ids"] == [asset_id]


def test_prompt_worker_observes_every_image_and_continues_with_the_first_valid_one(
    tmp_path,
    settings,
):
    import json
    from io import BytesIO

    from django.core.management import call_command
    from PIL import Image

    from platform_app.models import Cluster
    from platform_app.services import (
        FakeAPIMartClient,
        LocalStorage,
        create_project,
        merge_asset_into_cluster,
        process_prompt_once,
        register_uploaded_asset,
        request_cluster_preparation,
    )

    settings.MEDIA_ROOT = tmp_path
    call_command("seed_platform_templates")
    user = make_user()
    batch = create_project(user, name="Mixed references")
    first_image = BytesIO()
    Image.new("RGB", (8, 8), "white").save(first_image, "PNG")
    second_image = BytesIO()
    Image.new("RGB", (8, 8), "blue").save(second_image, "PNG")
    first = register_uploaded_asset(batch, "invalid.png", first_image.getvalue(), "image/png")
    second = register_uploaded_asset(batch, "valid.png", second_image.getvalue(), "image/png")
    cluster = first.clusters.get()
    merge_asset_into_cluster(second, cluster, expected_version=cluster.version)
    cluster.refresh_from_db()
    request_cluster_preparation(cluster, auto_generate=False)

    class MixedReferenceClient(FakeAPIMartClient):
        observed = []

        def observe_images(self, instruction, image_paths):
            asset_id = instruction.split("ASSET_ID=", 1)[1].splitlines()[0]
            self.observed.append(asset_id)
            valid = asset_id == str(second.id)
            return {
                "output_text": json.dumps(strict_n1({
                    "asset_id": asset_id,
                    "asset_kind": "owned_product",
                    "image_role": "clean_product",
                    "contains_target_product": valid,
                    "target_is_physical_product": valid,
                    "target_visibility": 95 if valid else 0,
                    "target_complete": valid,
                    "background_complexity": "low",
                    "observed_identity": {
                        "category_candidates": ["travel mug"] if valid else [],
                        "overall_shape": "cylindrical mug" if valid else "not visible",
                    },
                    "reference_quality": 95 if valid else 0,
                    "recommended_use": "reuse",
                    "candidate_product_name": "Travel mug" if valid else "Unclear image",
                    "candidate_product_name_confidence": 0.95 if valid else 0,
                })),
                "raw": {},
            }

        def optimize_prompt(self, payload):
            if "NODE N2" not in payload.get("text", ""):
                return super().optimize_prompt(payload)
            return {
                "output_text": json.dumps(strict_n2({
                    "decision": "continue",
                    "product_name": "Travel mug",
                    "confidence": 0.95,
                    "needs_input_reason": "",
                    "conflict_state": "unknown",
                    "primary_asset_id": str(second.id),
                    "supporting_asset_ids": [],
                    "target_appearances": [{
                        "appearance_id": "appearance.primary",
                        "label": "travel mug",
                        "variant_attributes": [],
                        "asset_ids": [str(second.id)],
                        "primary_asset_id": str(second.id),
                    }],
                    "standardization_mode": "reuse",
                    "standardization_reason": "",
                    "identity_lock": {"must_not_change": ["mug silhouette"]},
                    "product_profile": {
                        "category": "travel mug",
                        "primary_appearance": "visible travel mug",
                    },
                })),
                "raw": {},
            }

    client = MixedReferenceClient()
    assert process_prompt_once(client, LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()

    assert client.observed == [str(first.id), str(second.id)]
    assert cluster.preparation_status == Cluster.PreparationStatus.READY, cluster.preparation_error
    assert cluster.analysis_snapshot["identity"]["primary_asset_id"] == str(second.id)


def test_n5_requires_the_set_to_cover_every_target_appearance():
    from types import SimpleNamespace
    from platform_app.services import _normalize_n5_plans

    slots = [SimpleNamespace(order=2, name="Benefit 1"), SimpleNamespace(order=3, name="Benefit 2")]
    base = {
        "role": "benefit",
        "decision_task": "task",
        "main_scene": "scene",
        "main_action": "none",
        "subject_relationship": "none",
        "composition": "hero",
        "copy_intent": "",
        "text_mode": "none",
        "scene_family": "studio",
        "environment": "studio",
        "camera": "front",
        "fact_refs": [],
        "inference_refs": [],
        "localization_notes": [],
        "must_show": [],
        "must_avoid": [],
    }
    payload = {"plans": [
        {**base, "slot_order": 2, "appearance_ids": ["appearance.green"]},
        {**base, "slot_order": 3, "decision_task": "task 2", "main_scene": "scene 2", "scene_family": "home", "environment": "home", "camera": "detail", "appearance_ids": ["appearance.green"]},
    ]}

    with pytest.raises(ValueError, match="cover every target appearance"):
        _normalize_n5_plans(payload, slots, set(), set(), {"appearance.green", "appearance.blue"})


def test_n5_receives_consumer_context_and_strategy_fact_refs(tmp_path, settings):
    import json

    from platform_app.management.commands.seed_platform_templates import GLOBAL_SLOTS
    from platform_app.models import Asset, Batch, Cluster, OutputTemplate
    from platform_app.services import FakeAPIMartClient, LocalStorage, process_prompt_once, request_cluster_preparation

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = Batch.objects.create(owner=user, name="Consumer context")
    storage_path = f"originals/{batch.id}/source.png"
    (tmp_path / storage_path).parent.mkdir(parents=True)
    (tmp_path / storage_path).write_bytes(b"png-bytes")
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="source.png",
        storage_path=storage_path,
        sha256="e" * 64,
        file_size=9,
        content_type="image/png",
    )
    cluster = Cluster.create_for_asset(batch, asset)
    template = OutputTemplate.objects.create(
        seed_key="global-marketplace-nine-slot-template",
        platform="global",
        site="",
        name="Nine slot",
        version="2026.07.9",
    )
    for order, name, purpose in GLOBAL_SLOTS:
        template.slots.create(order=order, name=name, purpose=purpose)
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    request_cluster_preparation(cluster, auto_generate=False)

    class ConsumerContextClient(FakeAPIMartClient):
        n5_input = None

        def observe_images(self, instruction, image_paths):
            observed_asset_id = instruction.split("ASSET_ID=", 1)[1].splitlines()[0]
            return {
                "output_text": json.dumps(strict_n1({
                    "asset_id": observed_asset_id,
                    "asset_kind": "owned_product",
                    "image_role": "clean_product",
                    "contains_target_product": True,
                    "target_is_physical_product": True,
                    "target_visibility": 93,
                    "target_complete": True,
                    "background_complexity": "low",
                    "observed_identity": {
                        "category_candidates": ["portable lunch utensil set"],
                        "overall_shape": "wooden spoon and chopsticks in a slim case",
                    },
                    "reference_quality": 93,
                    "recommended_use": "reuse",
                    "candidate_product_name": "Portable lunch utensil set",
                    "candidate_product_name_confidence": 0.93,
                    "product_facts": ["wooden spoon", "matching chopsticks", "slim carry case"],
                    "identity_lock": "Keep the spoon, chopsticks, and slim case together.",
                    "target_consumer": "office lunch users",
                })),
                "raw": {},
            }

        def optimize_prompt(self, payload):
            text = payload.get("text", "")
            if "NODE N5" in text:
                self.n5_input = json.loads(text.splitlines()[-1])
                modes = [
                    "fab_value",
                    "scene_ownership",
                    "emotion",
                    "identity_signal",
                    "fab_value",
                    "scene_ownership",
                    "emotion",
                    "identity_signal",
                ]
                appearances = [
                    item["appearance_id"]
                    for item in self.n5_input.get("target_appearances", [])
                    if item.get("appearance_id")
                ]
                return {
                    "output_text": json.dumps({
                        "plans": [
                            n5_plan(
                                slot["slot_order"],
                                mode=modes[index],
                                fact_refs=("fact.name.001",),
                                appearance_ids=appearances,
                            )
                            for index, slot in enumerate(self.n5_input["slots"])
                        ]
                    }),
                    "raw": {},
                }
            return super().optimize_prompt(payload)

    client = ConsumerContextClient()
    assert process_prompt_once(client, LocalStorage(tmp_path)) == 1

    assert client.n5_input["consumer_context"]["target_consumer"] == "office lunch users"
    assert "wooden spoon" in client.n5_input["consumer_context"]["product_facts"]
    cluster.refresh_from_db()
    first_plan = cluster.analysis_snapshot["marketing_plan"]["plans"][0]
    assert first_plan["creative_strategy"]["source_fact_refs"] == ["fact.name.001"]


def test_target_observed_product_facts_ignore_unrelated_reference_images():
    from platform_app.services import _target_observed_product_facts

    identity = {
        "primary_asset_id": "asset-plush",
        "supporting_asset_ids": [],
        "target_appearances": [
            {
                "appearance_id": "appearance.1",
                "asset_ids": ["asset-plush"],
                "primary_asset_id": "asset-plush",
            }
        ],
    }
    observations = [
        {
            "asset_id": "asset-plush",
            "contains_target_product": True,
            "product_facts": ["yellow plush toy", "large round eyes"],
        },
        {
            "asset_id": "asset-noodle-poster",
            "contains_target_product": False,
            "product_facts": ["foreground shows a bowl of noodles", "busy street stall"],
        },
    ]

    facts = _target_observed_product_facts(observations, identity)

    assert facts == ["yellow plush toy", "large round eyes"]


def test_fallback_n5_does_not_invent_packaging_without_evidence():
    from types import SimpleNamespace

    from platform_app.services import _fallback_n5_plans

    slots = [SimpleNamespace(order=order, name=f"营销图 {order}", purpose=f"购买问题 {order}") for order in range(2, 10)]

    plans = _fallback_n5_plans(
        {"seed_style": "", "consumer_context": {"product_category": "plush_toy"}},
        slots,
        {"fact.name.001"},
        set(),
        {"appearance.1"},
    )["plans"]

    joined = " ".join(
        " ".join(str(plan.get(field, "")) for field in ("decision_task", "main_scene", "main_action", "copy_intent"))
        for plan in plans
    ).lower()
    assert "packaging" not in joined
    assert "included items" not in joined
    assert "contents overview" not in joined


def test_fallback_n6_creates_target_language_marketing_copy_for_named_product():
    from platform_app.services import _fallback_n6_prompt

    identity = {
        "primary_asset_id": "asset-plush",
        "supporting_asset_ids": [],
        "target_appearances": [
            {
                "appearance_id": "appearance.1",
                "asset_ids": ["asset-plush"],
                "primary_asset_id": "asset-plush",
            }
        ],
    }
    ledger = {"facts": [{"fact_id": "fact.name.001", "fact_class": "confirmed"}]}
    slot_input = {
        "slot_order": 3,
        "product_name": "奶龙玩偶",
        "slot_plan": {
            "slot_order": 3,
            "appearance_ids": ["appearance.1"],
            "main_scene": "a playful giftable plush toy scene chosen by the marketing director",
            "main_action": "make the buyer imagine a child hugging the plush toy",
            "composition": "large product focus with a clean text-safe area",
            "camera": "warm editorial ecommerce angle",
            "copy_intent": "让买家感到这是适合送给小朋友的治愈陪伴玩偶",
            "text_mode": "up_to_3_lines",
            "seed_style": "",
            "creative_strategy": creative_strategy("emotion", ("fact.name.001",)),
        },
        "market_context": {"language": "vi", "market": "VN"},
        "size": "1:1",
        "resolution": "1k",
    }

    compiled = _fallback_n6_prompt(slot_input, identity, ledger, set())

    assert compiled["visible_text_lines"]
    assert compiled["localized_copy"]["lines"] == compiled["visible_text_lines"]
    assert all("奶龙" not in line for line in compiled["visible_text_lines"])
    assert "ภาพ" not in " ".join(compiled["visible_text_lines"])
    assert "购买任务" not in compiled["display_prompt"]
    assert compiled["display_prompt"].startswith("生成一张 1:1")
    assert "真实商业摄影" in compiled["display_prompt"]
    assert "镜头" in compiled["display_prompt"]
    assert "光" in compiled["display_prompt"]
    assert "主体：" not in compiled["display_prompt"]
    assert "动作：" not in compiled["display_prompt"]
    assert "构图：" not in compiled["display_prompt"]
    assert "禁止" not in compiled["display_prompt"]
    assert "do not" not in compiled["display_prompt"].lower()
    assert "Only render the quoted localized copy" in compiled["prompt"]
    assert "no extra" not in compiled["prompt"].lower()
    assert "missing" not in compiled["prompt"].lower()
    assert "duplicated" not in compiled["prompt"].lower()


def test_n6_normalization_replaces_english_copy_for_thai_market():
    from platform_app.services import _normalize_n6_prompt

    identity = {"primary_asset_id": "asset-1", "supporting_asset_ids": []}
    ledger = {"facts": [{"fact_id": "fact.name.001", "fact_class": "observed"}]}
    payload = {
        "slot_id": "3",
        "main_scene": "a playful ecommerce scene",
        "main_action": "show why the product is worth buying",
        "visible_text_lines": ["Take me home", "Here to brighten your day"],
        "localized_copy": {
            "language": "th-TH",
            "lines": ["Take me home", "Here to brighten your day"],
            "source_fact_refs": ["fact.name.001"],
            "source_inference_refs": [],
        },
        "prompt": "Create image. Only render these words: Take me home.",
        "reference_plan": {"primary_asset_id": "asset-1", "supporting_asset_ids": []},
        "fact_trace": ["fact.name.001"],
        "inference_trace": [],
        "rule_refs": [],
        "generation_parameters": {"model": "gpt-image-2", "n": 1, "size": "1:1", "resolution": "1k"},
        "review_required": True,
        "creative_strategy": {"mode": "personification"},
    }

    normalized = _normalize_n6_prompt(payload, 3, identity, ledger, set())

    assert normalized["visible_text_lines"] == ["หยิบใช้แล้วรู้สึกสะดวก", "เก็บง่ายทุกวัน"]
    assert normalized["localized_copy"]["lines"] == normalized["visible_text_lines"]
    assert "Final visible copy lock" in normalized["prompt"]
    assert "หยิบใช้แล้วรู้สึกสะดวก" in normalized["prompt"]
    assert "Take me home" not in normalized["prompt"]


def test_fallback_n6_usage_set_does_not_force_holder_into_every_scene():
    from platform_app.services import _fallback_n6_prompt

    identity = {
        "primary_asset_id": "asset-1",
        "supporting_asset_ids": [],
        "target_appearances": [
            {
                "appearance_id": "appearance.1",
                "asset_ids": ["asset-1"],
                "primary_asset_id": "asset-1",
            }
        ],
    }
    ledger = {
        "facts": [
            {
                "fact_id": "fact.name.001",
                "fact_class": "observed",
                "text": "wooden chopsticks and spoon set with slim storage tray",
            }
        ]
    }
    slot_input = {
        "slot_order": 5,
        "product_name": "Wooden utensil set",
        "slot_plan": {
            **n5_plan(5, mode="emotion", fact_refs=("fact.name.001",), appearance_ids=["appearance.1"]),
            "main_scene": "a warm meal usage scene",
            "main_action": "show an adult hand actively using the utensil during a meal",
            "composition": "human-eye lifestyle angle with the used utensil clearly visible",
        },
        "market_context": {"language": "en", "market": "SG"},
        "size": "1:1",
        "resolution": "1k",
    }

    compiled = _fallback_n6_prompt(slot_input, identity, ledger, set())

    assert compiled["visible_text_lines"]
    assert "Visual theme:" in compiled["prompt"]
    assert "Specific moment:" in compiled["prompt"]
    assert "Aesthetic direction:" in compiled["prompt"]
    assert "Typography plan:" in compiled["prompt"]
    assert "Use the references only for product identity" in compiled["prompt"]
    assert "arrange them naturally as a set" not in compiled["prompt"]
    assert "visible product set match" not in compiled["prompt"]


def test_n5_rejects_unknown_creative_strategy_fact_ref():
    from types import SimpleNamespace

    from platform_app.services import _normalize_n5_plans

    slots = [SimpleNamespace(order=2, name="Benefit")]
    payload = {
        "plans": [
            {
                **n5_plan(2, fact_refs=("fact.name.001",)),
                "creative_strategy": creative_strategy("fab_value", ("fact.missing",)),
            }
        ]
    }

    with pytest.raises(ValueError, match="creative_strategy"):
        _normalize_n5_plans(payload, slots, {"fact.name.001"}, set())


def test_n5_requires_four_modes_and_at_least_one_fab_for_eight_slots():
    from types import SimpleNamespace

    from platform_app.services import _normalize_n5_plans

    slots = [SimpleNamespace(order=order, name=f"Slot {order}") for order in range(2, 10)]
    invalid_modes = [
        "scene_ownership",
        "emotion",
        "identity_signal",
        "scene_ownership",
        "emotion",
        "identity_signal",
        "scene_ownership",
        "emotion",
    ]
    invalid = {
        "plans": [
            n5_plan(slot.order, mode=invalid_modes[index], fact_refs=("fact.name.001",))
            for index, slot in enumerate(slots)
        ]
    }
    with pytest.raises(ValueError, match="creative strategy"):
        _normalize_n5_plans(invalid, slots, {"fact.name.001"}, set())

    valid_modes = [
        "fab_value",
        "scene_ownership",
        "emotion",
        "identity_signal",
        "fab_value",
        "scene_ownership",
        "emotion",
        "identity_signal",
    ]
    valid = {
        "plans": [
            n5_plan(slot.order, mode=valid_modes[index], fact_refs=("fact.name.001",))
            for index, slot in enumerate(slots)
        ]
    }

    normalized = _normalize_n5_plans(valid, slots, {"fact.name.001"}, set())
    assert [plan["creative_strategy"]["mode"] for plan in normalized["plans"]] == valid_modes


def test_n5_diversity_rejects_one_repeated_dimension_even_when_tuples_differ():
    from types import SimpleNamespace

    from platform_app.services import _normalize_n5_plans

    slots = [
        SimpleNamespace(order=2, name="Benefit 1"),
        SimpleNamespace(order=3, name="Benefit 2"),
        SimpleNamespace(order=4, name="Benefit 3"),
        SimpleNamespace(order=5, name="Benefit 4"),
    ]
    plans = []
    for index, slot in enumerate(slots):
        plans.append(
            {
                "slot_order": slot.order,
                "role": "marketing",
                "decision_task": f"decision-{index}",
                "main_scene": f"scene-{index}",
                "main_action": f"action-{index}",
                "subject_relationship": f"relationship-{index}",
                "composition": f"composition-{index}",
                "text_mode": "none",
                "scene_family": "lifestyle",
                "environment": f"environment-{index}",
                "camera": f"camera-{index}",
                "copy_intent": "",
                "fact_refs": [],
                "inference_refs": [],
                "localization_notes": [],
                "must_show": [],
                "must_avoid": [],
            }
        )

    with pytest.raises(ValueError, match="scene_family"):
        _normalize_n5_plans({"plans": plans}, slots, set(), set())


def test_six_category_marketing_benchmark_satisfies_prompt_os_31_gate():
    from types import SimpleNamespace

    from platform_app.models import Batch, OutputTemplate
    from platform_app.services import _normalize_n5_plans, evaluate_prompt_rule_gate

    user = make_user()
    categories = [
        ("home_kitchen", "家居厨卫", "餐具收纳套装", "Bữa trưa gọn hơn"),
        ("pet", "宠物用品", "宠物玩具收纳", "Chơi xong vẫn gọn"),
        ("baby", "婴幼儿", "婴儿外出用品", "Ra ngoài nhẹ hơn"),
        ("wearable", "穿戴", "通勤穿戴配件", "Mặc đẹp không vội"),
        ("beauty", "美妆", "随身补妆工具", "Chạm nhẹ là tươi"),
        ("tool", "工具/电器", "家用小工具", "Sửa nhanh trong tay"),
    ]
    modes = [
        "fab_value",
        "scene_ownership",
        "emotion",
        "identity_signal",
        "fab_value",
        "scene_ownership",
        "emotion",
        "personification",
    ]
    generic_hits = []
    repeated_signatures = []
    invalid_fact_refs = []
    copy_lock_errors = []
    high_quality_slots = 0
    total_slots = 0

    for slug, category_name, product_name, copy_seed in categories:
        template = OutputTemplate.objects.create(
            seed_key=f"benchmark-{slug}",
            platform="global",
            site="",
            name=f"{category_name} benchmark",
            version="2026.08",
        )
        slots = [
            template.slots.create(
                order=order,
                name=f"{category_name}槽位{order}",
                purpose=f"{category_name} buying decision {order}",
            )
            for order in range(2, 10)
        ]
        batch = Batch.objects.create(owner=user, name=f"{category_name} benchmark")
        fact_ids = {"fact.name.001", f"fact.{slug}.visible"}
        plans = []
        for index, slot in enumerate(slots):
            plan = n5_plan(slot.order, mode=modes[index], fact_refs=("fact.name.001",))
            plan.update(
                {
                    "role": f"{category_name}营销图{slot.order}",
                    "decision_task": f"让用户理解{product_name}在真实购买前解决的问题 {slot.order}",
                    "conversion_goal": f"把{product_name}的可见事实转成具体使用结果 {slot.order}",
                    "main_scene": f"{category_name}差异化生活片段 {slot.order}",
                    "main_action": f"{category_name}明确使用动作 {slot.order}",
                    "subject_relationship": f"{product_name}始终是画面主角 {slot.order}",
                    "composition": f"{category_name}独立构图 {slot.order}",
                    "copy_intent": f"让消费者想到{product_name}带来的具体省心结果 {slot.order}",
                    "scene_family": f"{slug}-scene-{slot.order}",
                    "environment": f"{slug}-environment-{slot.order}",
                    "camera": f"{slug}-camera-{slot.order}",
                    "appearance_ids": [f"appearance.{slug}.main"],
                    "creative_strategy": creative_strategy(
                        modes[index],
                        ("fact.name.001", f"fact.{slug}.visible"),
                    ),
                }
            )
            plans.append(plan)

        normalized = _normalize_n5_plans(
            {"plans": plans},
            [SimpleNamespace(order=slot.order, name=slot.name) for slot in slots],
            fact_ids,
            set(),
            {f"appearance.{slug}.main"},
        )["plans"]
        strategy_modes = [plan["creative_strategy"]["mode"] for plan in normalized]
        assert len(set(strategy_modes)) >= 4
        assert "fab_value" in strategy_modes
        assert strategy_modes.count("personification") <= 1

        seen_signatures = set()
        for slot, plan in zip(slots, normalized):
            signature = (
                plan["scene_family"],
                plan["environment"],
                plan["camera"],
                plan["main_action"],
                plan["composition"],
            )
            if signature in seen_signatures:
                repeated_signatures.append((slug, slot.order))
            seen_signatures.add(signature)

            line = f"{copy_seed} {slot.order}"
            prompt = (
                "Create one ecommerce listing image. "
                f'Visible text: render exactly "{line}" once, no extra words.'
            )
            localized_copy = {
                "language": "vi",
                "lines": [line],
                "back_translation": [f"{category_name}用户得到具体使用结果 {slot.order}"],
                "source_fact_refs": ["fact.name.001"],
                "source_inference_refs": [],
                "quality": {
                    "relevance": 90,
                    "specificity": 90,
                    "naturalness": 90,
                    "truthfulness": 100,
                    "credibility": 90,
                    "generic_phrase_hits": [],
                },
            }
            gate = evaluate_prompt_rule_gate(
                batch,
                slot,
                prompt,
                visible_text_lines=[line],
                localized_copy=localized_copy,
                fact_ids=fact_ids,
            )
            if gate["copy_checks"]["generic_phrase_hits"]:
                generic_hits.append((slug, slot.order))
            if not gate["copy_checks"]["fact_refs_valid"]:
                invalid_fact_refs.append((slug, slot.order))
            if not gate["copy_checks"]["lines_match_visible_text"] or not gate["copy_checks"]["each_line_present_once"]:
                copy_lock_errors.append((slug, slot.order))
            assert gate["decision"] == "pass", gate
            assert gate["hard_blocks"] == []
            quality = localized_copy["quality"]
            if all(
                quality[field] >= 85
                for field in ("relevance", "specificity", "naturalness", "truthfulness", "credibility")
            ):
                high_quality_slots += 1
            total_slots += 1

    assert total_slots == 48
    assert high_quality_slots >= 42
    assert repeated_signatures == []
    assert invalid_fact_refs == []
    assert copy_lock_errors == []
    assert generic_hits == []


def test_real_prompt_node_call_fails_closed_without_published_template(settings):
    from platform_app.services import _prompt_node_json

    settings.APIMART_FAKE_MODE = False

    class Client:
        calls = 0

        def optimize_prompt(self, payload):
            self.calls += 1
            return {"output_text": "{}"}

    client = Client()

    with pytest.raises(ValueError, match="published N3"):
        _prompt_node_json(client, "N3", "Compile facts.", {"product": "mug"})

    assert client.calls == 0


def test_prompt_node_repair_preserves_original_system_and_input():
    import json

    from platform_app.models import PromptNodeTemplate
    from platform_app.services import _prompt_node_json

    PromptNodeTemplate.objects.create(
        node_name="N3",
        version="review-test",
        instruction="FULL N3 SYSTEM CONTRACT",
        user_message_template="RUNTIME N3 USER TEMPLATE {{input_json}}",
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
        status=PromptNodeTemplate.Status.PUBLISHED,
    )

    class Client:
        def __init__(self):
            self.payloads = []

        def optimize_prompt(self, payload):
            self.payloads.append(payload)
            if len(self.payloads) == 1:
                return {"output_text": json.dumps({"ok": True, "extra": "blocked"})}
            return {"output_text": json.dumps({"ok": True})}

    client = Client()
    result = _prompt_node_json(
        client,
        "N3",
        "Compile facts.",
        {"product": "mug", "confirmed": ["steel"]},
    )

    assert result == {"ok": True}
    assert [payload["system"] for payload in client.payloads] == [
        "FULL N3 SYSTEM CONTRACT",
        "FULL N3 SYSTEM CONTRACT",
    ]
    assert all("RUNTIME N3 USER TEMPLATE" in payload["text"] for payload in client.payloads)
    assert all('"additionalProperties": false' in payload["text"] for payload in client.payloads)
    assert '"product": "mug"' in client.payloads[1]["text"]
    assert '"extra": "blocked"' in client.payloads[1]["text"]


def test_prompt_node_json_passes_node_temperature_to_deepseek_payload():
    import json

    from platform_app.models import PromptNodeTemplate
    from platform_app.services import _prompt_node_json

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    for node_name in ("N5.shopee", "N6.shopee", "N7.shopee"):
        PromptNodeTemplate.objects.create(
            node_name=node_name,
            version="temperature-test",
            instruction=f"FULL {node_name} SYSTEM CONTRACT",
            user_message_template="{{input_json}}",
            output_schema=schema,
            status=PromptNodeTemplate.Status.PUBLISHED,
        )

    class Client:
        def __init__(self):
            self.payloads = []

        def optimize_prompt(self, payload):
            self.payloads.append(payload)
            return {"output_text": json.dumps({"ok": True})}

    client = Client()

    for node_name in ("N5.shopee", "N6.shopee", "N7.shopee"):
        assert _prompt_node_json(client, node_name, "Run node.", {"input": node_name}) == {"ok": True}

    assert [payload["temperature"] for payload in client.payloads] == [1.6, 0.9, 0.2]


def test_prompt_version_snapshot_records_node_temperature(tmp_path, settings):
    from platform_app.management.commands.seed_platform_templates import Command
    from platform_app.models import Asset, Batch, OutputTemplate, PromptVersion
    from platform_app.services import FakeAPIMartClient, LocalStorage, process_prompt_once, request_cluster_preparation

    settings.MEDIA_ROOT = tmp_path
    Command().handle()
    user = make_user()
    template = OutputTemplate.objects.create(
        seed_key="temperature-snapshot",
        platform="global",
        site="",
        name="Temperature snapshot",
        version="2026.08",
    )
    template.slots.create(order=1, name="标准白底产品图", purpose="standard white background product hero")
    template.slots.create(order=2, name="核心卖点图", purpose="benefit")
    batch = Batch.objects.create(owner=user, name="Temperature batch", output_template=template)
    storage_path = f"originals/{batch.id}/source.png"
    (tmp_path / storage_path).parent.mkdir(parents=True)
    (tmp_path / storage_path).write_bytes(b"png-bytes")
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="source.png",
        storage_path=storage_path,
        sha256="d" * 64,
        file_size=9,
        content_type="image/png",
    )
    cluster = make_cluster(batch)
    cluster.cluster_assets.update(asset=asset)
    request_cluster_preparation(cluster, auto_generate=False)

    assert process_prompt_once(FakeAPIMartClient(), LocalStorage(tmp_path)) == 1

    prompt = PromptVersion.objects.get(cluster=cluster, output_slot__order=2)
    assert prompt.input_snapshot["_node_temperature"] == 0.9
    assert prompt.structured_output["_node_temperature"] == 0.9


def test_configuration_change_cancels_waiting_auto_generate_intent():
    from django.core.management import call_command

    from platform_app.models import Cluster
    from platform_app.services import create_project, update_project_settings

    call_command("seed_platform_templates")
    user = make_user()
    batch = create_project(
        user,
        name="Waiting auto generation",
        platform="shopee",
        market="VN",
    )
    cluster = make_cluster(batch)
    Cluster.objects.filter(id=cluster.id).update(
        preparation_status=Cluster.PreparationStatus.BLOCKED,
        analysis_snapshot={"_preparation_revision": 3},
        auto_generate=True,
    )

    update_project_settings(
        batch,
        {
            "platform": "tiktok",
            "market": "TH",
            "seller_tier": "general",
            "size": "1:1",
            "resolution": "1k",
        },
    )

    cluster.refresh_from_db()
    assert cluster.preparation_status == Cluster.PreparationStatus.DRAFT
    assert cluster.analysis_snapshot["_preparation_revision"] == 4
    assert cluster.auto_generate is False


def test_prompt_claim_rejects_a_stale_preparation_revision():
    from platform_app.models import Batch, Cluster
    from platform_app.services import _claim_prompt_cluster

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Prompt claim")
    stale = Cluster.objects.create(
        batch=batch,
        name="Product",
        preparation_status=Cluster.PreparationStatus.PENDING,
        analysis_snapshot={"_preparation_revision": 1},
    )
    Cluster.objects.filter(id=stale.id).update(
        analysis_snapshot={"_preparation_revision": 2},
    )

    assert _claim_prompt_cluster(stale) is None
    stale.refresh_from_db()
    assert stale.preparation_status == Cluster.PreparationStatus.PENDING
