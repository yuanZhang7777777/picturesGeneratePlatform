import pytest
from django.core.management import call_command

from platform_app.models import PromptNodeTemplate


pytestmark = pytest.mark.django_db


EXPECTED_NODE_NAMES = {
    "N1",
    "N2",
    "N3",
    "N4",
    "N8",
    "N9",
    "N5.generic",
    "N6.generic",
    "N7.generic",
    "N5.shopee",
    "N6.shopee",
    "N7.shopee",
    "N5.tiktok",
    "N6.tiktok",
    "N7.tiktok",
}


def test_seed_publishes_all_prompt_os_v3_variants_and_preserves_history():
    old = PromptNodeTemplate.objects.create(
        node_name="N5.generic",
        version="2.1.0",
        status=PromptNodeTemplate.Status.PUBLISHED,
        instruction="preserve this historical prompt",
        output_schema={"type": "object"},
    )
    legacy_shared = PromptNodeTemplate.objects.create(
        node_name="N5",
        version="2.1.0",
        status=PromptNodeTemplate.Status.PUBLISHED,
        instruction="preserve legacy shared director",
        output_schema={"type": "object"},
    )

    call_command("seed_platform_templates")

    published = PromptNodeTemplate.objects.filter(
        version="3.0.0",
        status=PromptNodeTemplate.Status.PUBLISHED,
    )
    assert set(published.values_list("node_name", flat=True)) == EXPECTED_NODE_NAMES
    old.refresh_from_db()
    legacy_shared.refresh_from_db()
    assert old.status == PromptNodeTemplate.Status.RETIRED
    assert legacy_shared.status == PromptNodeTemplate.Status.RETIRED
    assert old.instruction == "preserve this historical prompt"
    assert legacy_shared.instruction == "preserve legacy shared director"


def test_seeded_v3_prompts_are_full_production_instructions():
    call_command("seed_platform_templates")
    prompts = {
        item.node_name: item.instruction
        for item in PromptNodeTemplate.objects.filter(version="3.0.0")
    }

    required_markers = {
        "N1": ("owned_product", "competitor_style", "confirmed_points", "forbidden_to_copy"),
        "N2": ("身份不变量", "可变属性", "关键部件拓扑", "verified_use_relationships"),
        "N3": ("inferred", "confidence", "risk_level", "evidence_refs", "allowed_uses"),
        "N4": ("exactly", "一对一连接拓扑", "3500", "visible_text_lines"),
        "N8": ("最小邻接区域", "blocked_change", "preserve_outside_region", "3500"),
        "N9": ("prompt_complexity", "content_safety_rejection", "manual_prompt_change_required", "N7"),
    }
    for node_name, markers in required_markers.items():
        assert len(prompts[node_name]) >= 1_000
        assert all(marker in prompts[node_name] for marker in markers)

    for platform in ("generic", "shopee", "tiktok"):
        director = prompts[f"N5.{platform}"]
        compiler = prompts[f"N6.{platform}"]
        gate = prompts[f"N7.{platform}"]
        assert all(
            marker in director
            for marker in ("八个", "购买决策", "人物", "宠物", "五维签名", "plans")
        )
        assert all(
            marker in compiler
            for marker in ("exactly", "一对一连接拓扑", "真实使用关系", "visible_text_lines", "3500")
        )
        assert all(
            marker in gate
            for marker in ("确定性", "hard_blocks", "精确数量", "真实使用关系", "竞品", "3500")
        )

def test_generic_sea_is_an_english_one_plus_eight_strategy_not_a_fallback():
    call_command("seed_platform_templates")
    director = PromptNodeTemplate.objects.get(node_name="N5.generic", version="3.0.0").instruction
    compiler = PromptNodeTemplate.objects.get(node_name="N6.generic", version="3.0.0").instruction

    assert "1+8" in director
    assert "英语" in director
    assert "fallback" not in director.lower()
    assert all(
        task in director
        for task in (
            "第二视角与结构确认",
            "核心收益",
            "事实证明",
            "使用理解",
            "细节信任",
            "尺度或适配",
            "规格包装或包含物",
            "场景体验与购买收尾",
        )
    )
    assert "English" in compiler
    assert "visible_text_lines" in compiler


def test_v3_output_schemas_are_strict_and_match_seed_source_and_runtime():
    from platform_app.prompt_templates_v3 import PROMPT_TEMPLATES
    from platform_app.services import _prompt_node_contract

    call_command("seed_platform_templates")

    assert set(PROMPT_TEMPLATES) == EXPECTED_NODE_NAMES
    for node_name, source in PROMPT_TEMPLATES.items():
        seeded = PromptNodeTemplate.objects.get(node_name=node_name, version="3.0.0")
        runtime_instruction, runtime_version = _prompt_node_contract(node_name)
        schema = seeded.output_schema

        assert seeded.instruction == source["instruction"] == runtime_instruction
        assert runtime_version == "3.0.0"
        assert schema == source["output_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        assert len(schema["properties"]) >= 1

    assert set(PROMPT_TEMPLATES["N1"]["output_schema"]["properties"]) >= {
        "asset_id",
        "asset_kind",
        "observed_identity",
        "style_dna",
        "candidate_product_name",
        "candidate_product_name_confidence",
    }
    assert set(PROMPT_TEMPLATES["N2"]["output_schema"]["properties"]) >= {
        "product_name",
        "conflict_state",
    }
    assert set(PROMPT_TEMPLATES["N5.generic"]["output_schema"]["properties"]) == {"plans"}
    assert set(PROMPT_TEMPLATES["N6.generic"]["output_schema"]["properties"]) >= {
        "slot_id",
        "localized_copy",
        "prompt",
        "character_count",
        "reference_plan",
    }
    assert set(PROMPT_TEMPLATES["N7.generic"]["output_schema"]["properties"]) >= {
        "decision",
        "hard_blocks",
        "semantic_risks",
        "prompt_checks",
    }


def test_v3_user_message_templates_are_seeded_and_bound_exactly_at_runtime():
    from platform_app.prompt_templates_v3 import PROMPT_TEMPLATES
    from platform_app.services import _prompt_node_template_binding, _snapshot_hash

    call_command("seed_platform_templates")

    expected_input_keys = {
        "N1": ("asset_id", "asset_kind", "product_name", "confirmed_points"),
        "N2": ("product_name", "confirmed_points", "observations"),
        "N3": ("product_profile", "identity_lock", "market_context"),
        "N4": ("slot_order", "primary_asset_id", "supporting_asset_ids", "prompt_limits"),
        "N5.generic": ("slots", "market_context", "seed_style"),
        "N6.generic": ("slot_order", "slot_plan", "primary_asset_id", "supporting_asset_ids"),
        "N7.generic": ("slot_order", "prompt", "rule_snapshot", "image_request", "lineage"),
        "N8": ("source_generation_id", "current_prompt", "rule_snapshot", "review"),
        "N9": ("failure_class", "original_prompt", "rule_snapshot", "max_simplification_attempts"),
    }
    for node_name, source in PROMPT_TEMPLATES.items():
        seeded = PromptNodeTemplate.objects.get(node_name=node_name, version="3.0.0")
        user_template = source["user_message_template"]
        base_node = node_name.split(".", 1)[0]
        representative = expected_input_keys.get(node_name) or expected_input_keys[f"{base_node}.generic"]

        assert seeded.user_message_template == user_template
        assert "{{input_json}}" in user_template
        assert all(key in user_template for key in representative)

        binding = _prompt_node_template_binding(node_name, "3.0.0")
        expected_hash = _snapshot_hash(
            {
                "instruction": source["instruction"],
                "user_message_template": user_template,
                "output_schema": source["output_schema"],
            }
        )
        assert binding["content_hash"] == expected_hash


def test_v3_schemas_match_runtime_envelopes_and_marketing_reference_policy():
    from platform_app.prompt_templates_v3 import PROMPT_TEMPLATES

    expected_top_level = {
        "N1": {
            "asset_id", "asset_kind", "image_role", "contains_target_product",
            "target_is_physical_product", "target_visibility", "target_complete",
            "reference_quality", "background_complexity", "observed_identity",
            "observed_use_relationships", "non_target_objects", "package_or_text_clues",
            "conflicts_with_confirmed_points", "recommended_use", "style_dna", "reason",
            "candidate_product_name", "candidate_product_name_confidence", "product_facts",
            "identity_lock", "target_consumer",
        },
        "N2": {
            "decision", "confidence", "needs_input_reason", "product_name", "conflict_state",
            "product_profile", "identity_lock", "primary_asset_id", "supporting_asset_ids",
            "standardization_mode", "standardization_reason",
        },
        "N3": {"ledger_version", "facts", "blocked_claim_topics", "unresolved_questions", "review_summary"},
        "N4": {
            "slot_id", "main_scene", "main_action", "visible_text_lines", "prompt",
            "character_count", "reference_plan", "fact_trace", "inference_trace",
            "rule_refs", "generation_parameters", "review_required",
        },
        "N5.generic": {"plans"},
        "N6.generic": {
            "slot_id", "main_scene", "main_action", "visible_text_lines", "localized_copy",
            "prompt", "character_count", "reference_plan", "fact_trace", "inference_trace",
            "rule_refs", "generation_parameters", "review_required",
        },
        "N7.generic": {
            "decision", "hard_blocks", "semantic_risks", "warnings", "prompt_checks",
            "resolved_rule_refs", "review_required",
        },
        "N8": {
            "operation", "change_intent", "preserve_outside_region", "visible_text_lines",
            "delta_prompt", "review_required",
        },
        "N9": {
            "decision", "simplified_prompt", "character_count", "visible_text_lines",
            "preserved_fact_refs", "preserved_inference_refs", "preserved_rule_refs",
            "removed_elements", "safety_changes", "review_required",
        },
    }
    for node_name, fields in expected_top_level.items():
        assert set(PROMPT_TEMPLATES[node_name]["output_schema"]["properties"]) == fields
    for platform in ("shopee", "tiktok"):
        for node in ("N5", "N6", "N7"):
            assert PROMPT_TEMPLATES[f"{node}.{platform}"]["output_schema"] == PROMPT_TEMPLATES[f"{node}.generic"]["output_schema"]

    n5_plan = PROMPT_TEMPLATES["N5.generic"]["output_schema"]["properties"]["plans"]["items"]
    assert set(n5_plan["properties"]) >= {
        "slot_order",
        "role",
        "scene_family",
        "environment",
        "camera",
        "decision_task",
        "main_scene",
        "main_action",
        "subject_relationship",
        "composition",
    }

    n7 = PROMPT_TEMPLATES["N7.generic"]["output_schema"]
    assert set(n7["properties"]) == {
        "decision",
        "hard_blocks",
        "semantic_risks",
        "warnings",
        "resolved_rule_refs",
        "prompt_checks",
        "review_required",
    }

    n8 = PROMPT_TEMPLATES["N8"]["output_schema"]
    assert set(n8["properties"]) == {
        "operation",
        "change_intent",
        "preserve_outside_region",
        "visible_text_lines",
        "delta_prompt",
        "review_required",
    }

    n6_reference = PROMPT_TEMPLATES["N6.generic"]["output_schema"]["properties"]["reference_plan"]
    assert n6_reference["properties"]["supporting_asset_ids"]["maxItems"] == 1
    assert "已完成白底图" in PROMPT_TEMPLATES["N6.generic"]["instruction"]
    assert "最多一张" in PROMPT_TEMPLATES["N6.generic"]["instruction"]


def test_shopee_director_keeps_all_eight_market_visual_mappings():
    from platform_app.prompt_templates_v3 import PROMPT_TEMPLATES

    instruction = PROMPT_TEMPLATES["N5.shopee"]["instruction"]
    for market, marker in {
        "SG": "HDB",
        "MY": "热带公寓",
        "TH": "奶油黄",
        "VN": "紧凑整洁",
        "PH": "明亮通风",
        "ID": "温暖实用",
        "TW": "莫兰迪灰",
        "BR": "珊瑚",
    }.items():
        assert market in instruction
        assert marker in instruction
