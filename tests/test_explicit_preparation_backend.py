import json
from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image


pytestmark = pytest.mark.django_db


def make_user(username="preparation-operator", *, role="operator"):
    return get_user_model().objects.create_user(
        username=username,
        password="long-enough-password",
        must_change_password=False,
        role=role,
    )


def make_global_configuration():
    from platform_app.models import OutputSlot, OutputTemplate, RuleProfile

    template = OutputTemplate.objects.create(
        seed_key="global-marketplace-nine-slot-template",
        platform="global",
        name="Global 1+8",
        default_size="1:1",
        default_resolution="1k",
    )
    OutputSlot.objects.create(
        template=template,
        name="Standard white background product hero",
        order=1,
        purpose="standard white background product hero",
    )
    rules = RuleProfile.objects.create(
        seed_key="global-marketplace-prompt-os-v2-rule",
        platform="global",
        name="Global rules",
        status=RuleProfile.Status.PUBLISHED,
    )
    return template, rules


def png_upload(name="product.png"):
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def test_new_project_uses_generic_sea_square_1k_defaults(client):
    make_global_configuration()
    user = make_user()
    client.force_login(user)

    response = client.post(
        reverse("api_project_create"),
        data=json.dumps({"name": "SEA launch"}),
        content_type="application/json",
    )

    assert response.status_code == 201
    assert response.json()["defaultConfig"] == {
        "platform": "generic",
        "market": "SEA",
        "sellerTier": "general",
        "size": "1:1",
        "resolution": "1K",
        "globalPrompt": "",
    }


def test_organize_upload_does_not_request_preparation(client, tmp_path, settings):
    from platform_app.models import Batch
    from platform_app.services import _claim_prompt_cluster

    settings.MEDIA_ROOT = tmp_path
    template, rules = make_global_configuration()
    user = make_user()
    batch = Batch.objects.create(
        owner=user,
        name="Organize only",
        platform="generic",
        site="SEA",
        market="SEA",
        output_template=template,
        rule_profile=rules,
        size="1:1",
        resolution="1k",
    )
    client.force_login(user)

    response = client.post(
        reverse("api_project_upload_assets", args=[batch.id]),
        {"mode": "organize", "files": [png_upload()]},
    )

    assert response.status_code == 200
    cluster = batch.clusters.get()
    assert cluster.preparation_status == "draft"
    assert cluster.auto_generate is False
    assert _claim_prompt_cluster(cluster) is None


def test_auto_upload_explicitly_queues_preparation(client, tmp_path, settings):
    from platform_app.models import Batch

    settings.MEDIA_ROOT = tmp_path
    template, rules = make_global_configuration()
    user = make_user()
    batch = Batch.objects.create(
        owner=user,
        name="Auto prepare",
        platform="generic",
        site="SEA",
        market="SEA",
        output_template=template,
        rule_profile=rules,
    )
    client.force_login(user)

    response = client.post(
        reverse("api_project_upload_assets", args=[batch.id]),
        {"mode": "auto", "files": [png_upload()]},
    )

    assert response.status_code == 200
    cluster = batch.clusters.get()
    assert cluster.preparation_status == "pending"
    assert cluster.preparation_stage == "queued"
    assert cluster.auto_generate is True


def test_cluster_effective_config_owns_template_and_rules_in_both_directions():
    from django.core.management import call_command

    from platform_app.models import Batch, Cluster
    from platform_app.services import (
        _applicable_rules,
        _effective_cluster_resources,
        is_source_product_photo_slot,
        standard_product_hero_slot,
    )

    call_command("seed_platform_templates")
    user = make_user()
    global_template = Batch._meta.get_field("output_template").remote_field.model.objects.get(
        seed_key="global-marketplace-baseline-template"
    )
    global_rules = Batch._meta.get_field("rule_profile").remote_field.model.objects.get(
        seed_key="global-marketplace-prompt-os-v2-rule"
    )
    generic_batch = Batch.objects.create(
        owner=user,
        name="Generic project",
        platform="generic",
        site="SEA",
        market="SEA",
        output_template=global_template,
        rule_profile=global_rules,
    )
    shopee_cluster = Cluster.objects.create(
        batch=generic_batch,
        name="Shopee VN product",
        platform_override="shopee",
        market_override="VN",
    )

    shopee_template, shopee_rules, shopee_config = _effective_cluster_resources(
        generic_batch, shopee_cluster
    )
    shopee_slots = list(shopee_template.slots.order_by("order"))
    assert shopee_template.seed_key == "shopee-vn-general-nine-slot-v2-template"
    assert len(shopee_slots) == 9
    assert is_source_product_photo_slot(shopee_slots[0])
    assert standard_product_hero_slot(shopee_template).order == 2
    assert shopee_rules.seed_key == "shopee-vn-verified-20260730-rule"
    assert any(
        rule["rule_id"].startswith("shopee.vn.")
        for rule in _applicable_rules(
            generic_batch,
            shopee_slots[0],
            effective_config=shopee_config,
            rule_profile=shopee_rules,
        )
    )

    shopee_batch = Batch.objects.create(
        owner=user,
        name="Shopee project",
        platform="shopee",
        site="VN",
        market="VN",
        output_template=shopee_template,
        rule_profile=shopee_rules,
    )
    generic_cluster = Cluster.objects.create(
        batch=shopee_batch,
        name="Generic product",
        platform_override="generic",
        market_override="SEA",
    )
    generic_template, generic_rules, generic_config = _effective_cluster_resources(
        shopee_batch, generic_cluster
    )
    generic_slots = list(generic_template.slots.order_by("order"))
    assert generic_template.id == global_template.id
    assert len(generic_slots) == 9
    assert not any(is_source_product_photo_slot(slot) for slot in generic_slots)
    assert standard_product_hero_slot(generic_template).order == 1
    assert generic_rules.id == global_rules.id
    assert not any(
        rule["rule_id"].startswith("shopee.")
        for slot in generic_slots
        for rule in _applicable_rules(
            shopee_batch,
            slot,
            effective_config=generic_config,
            rule_profile=generic_rules,
        )
    )


def test_formal_gated_prompt_runs_platform_n7_and_preserves_all_blocks(monkeypatch):
    import json

    from django.core.management import call_command

    from platform_app.models import Asset, Batch, Cluster, PromptNodeTemplate, PromptVersion
    from platform_app.services import _create_gated_prompt_version

    call_command("seed_platform_templates")
    user = make_user()
    template = Batch._meta.get_field("output_template").remote_field.model.objects.get(
        seed_key="global-marketplace-baseline-template"
    )
    rules = Batch._meta.get_field("rule_profile").remote_field.model.objects.get(
        seed_key="global-marketplace-prompt-os-v2-rule"
    )
    batch = Batch.objects.create(
        owner=user,
        name="Formal gate",
        platform="generic",
        site="SEA",
        market="SEA",
        output_template=template,
        rule_profile=rules,
    )
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="product.png",
        storage_path="originals/product.png",
        sha256="f" * 64,
        file_size=10,
        content_type="image/png",
    )
    cluster = Cluster.create_for_asset(batch, asset)
    cluster.analysis_snapshot = {
        "identity": {"primary_asset_id": str(asset.id), "supporting_asset_ids": []},
        "_preparation_revision": 1,
    }
    cluster.save(update_fields=["analysis_snapshot"])
    slot = template.slots.get(order=1)
    n4 = PromptNodeTemplate.objects.get(node_name="N4", version="3.0.0")

    class GateClient:
        def __init__(self, block=None):
            self.block = block
            self.calls = []

        def optimize_prompt(self, payload):
            self.calls.append(payload)
            blocks = [self.block] if self.block else []
            return {
                "output_text": json.dumps(
                    {
                        "decision": "block" if blocks else "pass",
                        "hard_blocks": blocks,
                        "semantic_risks": [],
                        "warnings": [],
                        "prompt_checks": {
                            "character_count": 20,
                            "text_line_count": 0,
                            "main_scene_count": 1,
                            "main_action_count": 1,
                            "reference_assets_valid": True,
                        },
                        "resolved_rule_refs": [],
                        "review_required": True,
                    }
                )
            }

    def create(client):
        return _create_gated_prompt_version(
            cluster=cluster,
            batch=batch,
            slot=slot,
            user=user,
            node_name="N4",
            template_version=n4.version,
            provider_model="gpt-image-2",
            prompt_text="Accurate product on pure white.",
            input_snapshot={},
            structured_output={"visible_text_lines": []},
            source_snapshot={},
            references=[asset.storage_path],
            client=client,
        )

    passing = GateClient()
    version = create(passing)
    cluster.refresh_from_db()
    assert version.evaluation["rule_gate"]["decision"] == "pass"
    assert cluster.analysis_snapshot["prompt_os"][-1]["node_id"] == "N7.generic"
    assert "NODE N7.generic" in passing.calls[0]["text"]

    with pytest.raises(ValueError, match="semantic.block"):
        create(GateClient("semantic.block"))
    assert PromptVersion.objects.filter(cluster=cluster).count() == 1

    monkeypatch.setattr(
        "platform_app.services.evaluate_prompt_rule_gate",
        lambda *args, **kwargs: {
            "decision": "block",
            "hard_blocks": ["deterministic.block"],
            "semantic_risks": [],
            "warnings": [],
            "resolved_rule_refs": [],
            "prompt_checks": {},
            "review_required": True,
        },
    )
    with pytest.raises(ValueError, match="deterministic.block"):
        create(GateClient())


def test_prepare_selected_returns_item_statuses_without_generations(client):
    from platform_app.models import Batch, Cluster, Generation
    from platform_app.services import _prompt_runtime_contract_fingerprint

    template, rules = make_global_configuration()
    user = make_user()
    batch = Batch.objects.create(
        owner=user,
        name="Prepare selected",
        platform="generic",
        site="SEA",
        market="SEA",
        output_template=template,
        rule_profile=rules,
        size="1:1",
        resolution="1k",
    )
    queued = Cluster.objects.create(batch=batch, name="Queued")
    ready = Cluster.objects.create(
        batch=batch,
        name="Ready",
        preparation_status=Cluster.PreparationStatus.READY,
    )
    ready.analysis_snapshot = {
        "_runtime_contract_fingerprint": _prompt_runtime_contract_fingerprint(batch, ready)
    }
    ready.save(update_fields=["analysis_snapshot"])
    blocked = Cluster.objects.create(
        batch=batch,
        name="Blocked",
        preparation_status=Cluster.PreparationStatus.BLOCKED,
        preparation_stage="blocked",
    )
    client.force_login(user)

    response = client.post(
        reverse("api_project_prepare", args=[batch.id]),
        data=json.dumps({"cluster_ids": [str(queued.id), str(ready.id), str(blocked.id)]}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "cluster_id": str(queued.id),
            "status": "queued",
            "stage": "queued",
        },
        {
            "cluster_id": str(ready.id),
            "status": "already_ready",
            "stage": "ready",
        },
        {
            "cluster_id": str(blocked.id),
            "status": "queued",
            "stage": "queued",
        },
    ]
    assert Generation.objects.count() == 0


def test_prepare_requeues_ready_cluster_when_prompt_runtime_fingerprint_is_stale(client):
    from platform_app.models import Batch, Cluster

    template, rules = make_global_configuration()
    user = make_user()
    batch = Batch.objects.create(
        owner=user,
        name="Stale ready",
        platform="generic",
        site="SEA",
        market="SEA",
        output_template=template,
        rule_profile=rules,
    )
    cluster = Cluster.objects.create(
        batch=batch,
        name="Product",
        preparation_status=Cluster.PreparationStatus.READY,
        analysis_snapshot={"_runtime_contract_fingerprint": "old-runtime"},
    )
    client.force_login(user)

    response = client.post(
        reverse("api_project_prepare", args=[batch.id]),
        data=json.dumps({"cluster_ids": [str(cluster.id)]}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["status"] == "queued"
    cluster.refresh_from_db()
    assert cluster.preparation_status == Cluster.PreparationStatus.PENDING


def test_image_model_change_makes_ready_preparation_stale(settings):
    from platform_app.models import Batch, Cluster
    from platform_app.services import (
        _prompt_runtime_contract_fingerprint,
        cluster_preparation_is_current,
    )

    template, rules = make_global_configuration()
    batch = Batch.objects.create(
        owner=make_user(),
        name="Image model fingerprint",
        platform="generic",
        site="SEA",
        market="SEA",
        output_template=template,
        rule_profile=rules,
    )
    cluster = Cluster.objects.create(
        batch=batch,
        name="Product",
        preparation_status=Cluster.PreparationStatus.READY,
    )
    cluster.analysis_snapshot = {
        "_runtime_contract_fingerprint": _prompt_runtime_contract_fingerprint(
            batch, cluster
        )
    }
    cluster.save(update_fields=["analysis_snapshot"])
    assert cluster_preparation_is_current(cluster) is True

    settings.APIMART_IMAGE_MODEL = "gpt-image-next"

    assert cluster_preparation_is_current(cluster) is False


def test_snapshot_exposes_preparation_contract_and_distinct_product_style(client):
    from platform_app.models import Batch, Cluster

    template, rules = make_global_configuration()
    user = make_user()
    batch = Batch.objects.create(
        owner=user,
        name="Snapshot",
        platform="generic",
        site="SEA",
        market="SEA",
        output_template=template,
        rule_profile=rules,
        size="1:1",
        resolution="1k",
        global_prompt="Project style",
    )
    cluster = Cluster.objects.create(
        batch=batch,
        name="Product",
        product_facts="Employee creative brief",
        prompt_override="Product-only style",
        platform_override="shopee",
        market_override="VN",
        preparation_status=Cluster.PreparationStatus.PREPARING,
        preparation_stage="N3",
        preparation_current=3,
        preparation_total=7,
        analysis_snapshot={
            "identity": {"product_profile": {"category": "cup"}},
            "fact_ledger": {"facts": [{"fact_id": "fact.1"}]},
            "marketing_plan": {"plans": [{"slot_order": 2, "decision_task": "use"}]},
        },
    )
    client.force_login(user)

    sku = client.get(reverse("api_project_snapshot", args=[batch.id])).json()["skus"][0]

    assert sku["overrides"] == {
        "platform": "shopee",
        "market": "VN",
        "sellerTier": None,
    }
    assert sku["effectiveConfig"]["globalPrompt"] == "Product-only style"
    assert sku["preparation"] == {
        "status": "preparing",
        "stage": "N3",
        "current": 3,
        "total": 7,
        "error": "",
    }
    assert sku["identity"] == {"product_profile": {"category": "cup"}}
    assert sku["factLedger"] == {"facts": [{"fact_id": "fact.1"}]}
    assert sku["productFacts"] == "Employee creative brief"
    assert sku["marketingPlan"]["plans"][0]["slot_order"] == 2
    assert sku["productStyle"] == "Product-only style"


def test_snapshot_hides_schema_placeholder_identity_values(client):
    from platform_app.models import Batch, Cluster

    template, rules = make_global_configuration()
    user = make_user()
    batch = Batch.objects.create(
        owner=user,
        name="Snapshot placeholders",
        output_template=template,
        rule_profile=rules,
    )
    Cluster.objects.create(
        batch=batch,
        name="Product",
        product_name="string",
        preparation_status=Cluster.PreparationStatus.BLOCKED,
        analysis_snapshot={
            "identity": {
                "product_name": "string",
                "confidence": 0,
                "product_profile": {
                    "category": "string",
                    "primary_appearance": "string",
                    "shared_structure": ["string"],
                },
                "identity_lock": {"must_not_change": ["string"]},
            },
            "readiness": {"status": "blocked", "code": "identity_needs_input"},
        },
    )
    client.force_login(user)

    sku = client.get(reverse("api_project_snapshot", args=[batch.id])).json()["skus"][0]

    assert sku["name"] == ""
    assert sku["productName"] == ""
    assert sku["identity"] == {"confidence": 0}


def test_cluster_patch_accepts_platform_market_and_product_style(client):
    from platform_app.models import Batch, Cluster

    template, rules = make_global_configuration()
    user = make_user()
    batch = Batch.objects.create(owner=user, name="Patch", output_template=template, rule_profile=rules)
    cluster = Cluster.objects.create(
        batch=batch,
        name="Product",
        preparation_status=Cluster.PreparationStatus.READY,
        analysis_snapshot={
            "_preparation_revision": 2,
            "identity": {"product_profile": {"category": "cup"}},
        },
    )
    client.force_login(user)

    response = client.patch(
        reverse("api_update_cluster", args=[cluster.id]),
        data=json.dumps(
            {
                "expected_version": cluster.version,
                "platform_override": "tiktok_shop",
                "market_override": "TH",
                "prompt_override": "Bright studio",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    cluster.refresh_from_db()
    assert (cluster.platform_override, cluster.market_override, cluster.prompt_override) == (
        "tiktok_shop",
        "TH",
        "Bright studio",
    )
    assert cluster.analysis_snapshot["identity"]["product_profile"]["category"] == "cup"
    assert cluster.analysis_snapshot["_preparation_revision"] == 3


def test_prompt_node_json_apis_are_admin_only_and_publish_exact_version(client, settings):
    from platform_app.models import PromptNodeTemplate

    operator = make_user()
    admin = make_user("prompt-admin", role="admin")
    client.force_login(operator)
    assert client.get(reverse("api_admin_prompt_nodes")).status_code == 403

    client.force_login(admin)
    draft_payload = {
        "node_name": "N6.generic",
        "version": "3.0.0",
        "instruction": "Full system prompt",
        "user_message_template": "Input: {{input_json}}",
        "output_schema": {"type": "object", "required": ["prompt"]},
    }
    created = client.post(
        reverse("api_admin_prompt_nodes"),
        data=json.dumps(draft_payload),
        content_type="application/json",
    )
    assert created.status_code == 201
    assert created.json()["status"] == "draft"

    published = client.post(
        reverse("api_admin_prompt_nodes_publish"),
        data=json.dumps({"node_name": "N6.generic", "version": "3.0.0"}),
        content_type="application/json",
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert PromptNodeTemplate.objects.get(node_name="N6.generic", version="3.0.0").status == "published"
    listed = client.get(reverse("api_admin_prompt_nodes")).json()["nodes"]
    assert listed[0]["user_message_template"] == "Input: {{input_json}}"
    assert listed[0]["model"] == settings.APIMART_PROMPT_MODEL
    assert listed[0]["platform_scope"] == "generic"


def test_workspace_snapshot_exposes_current_user_role(client):
    admin = make_user(role="admin")
    client.force_login(admin)

    response = client.get(reverse("api_workspace_snapshot"))

    assert response.status_code == 200
    assert response.json()["currentUser"] == {"role": "admin"}


def test_n7_semantic_pass_cannot_remove_deterministic_hard_block():
    from platform_app.services import _merge_n7_gate

    merged = _merge_n7_gate(
        {"decision": "block", "hard_blocks": ["platform.no_text"], "resolved_rule_refs": []},
        {
            "decision": "pass",
            "hard_blocks": [],
            "semantic_risks": [],
            "warnings": [],
            "advice": [],
            "prompt_checks": {},
            "resolved_rule_refs": [],
            "inference_disclosures": [],
        },
    )

    assert merged["decision"] == "block"
    assert merged["hard_blocks"] == ["platform.no_text"]


def test_n6_reference_plan_allows_only_one_n2_approved_supporting_asset():
    from platform_app.services import _normalize_n6_prompt

    identity = {
        "primary_asset_id": "primary",
        "supporting_asset_ids": ["support-1", "support-2"],
    }
    ledger = {"facts": []}
    payload = {
        "slot_id": "2",
        "main_scene": "kitchen",
        "main_action": "none",
        "visible_text_lines": [],
        "localized_copy": {
            "language": "en",
            "lines": [],
            "source_fact_refs": [],
            "source_inference_refs": [],
        },
        "prompt": "Show the product in one kitchen scene.",
        "character_count": 38,
        "reference_plan": {
            "primary_asset_id": "primary",
            "supporting_asset_ids": ["support-1"],
            "completed_white_result_id": None,
        },
        "fact_trace": [],
        "inference_trace": [],
        "rule_refs": [],
        "generation_parameters": {"model": "gpt-image-2", "n": 1, "size": "1:1", "resolution": "1k"},
        "review_required": True,
    }

    assert _normalize_n6_prompt(payload, 2, identity, ledger, [])["reference_plan"]["supporting_asset_ids"] == ["support-1"]
    payload["reference_plan"]["supporting_asset_ids"] = ["support-1", "support-2"]
    with pytest.raises(ValueError, match="at most one"):
        _normalize_n6_prompt(payload, 2, identity, ledger, [])


@pytest.mark.parametrize(
    "field",
    [
        "observed_use_relationships",
        "non_target_objects",
        "package_or_text_clues",
        "conflicts_with_confirmed_points",
        "style_dna",
        "reason",
    ],
)
def test_n1_requires_every_authoritative_top_level_field(field):
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
            "dominant_colors": ["green"],
            "overall_shape": "cylindrical",
            "visible_material_cues": [],
            "logos_or_markings": [],
            "controls_ports_connectors": [],
            "distinctive_parts": [],
            "count_observations": [],
        },
        "observed_use_relationships": [],
        "non_target_objects": [],
        "package_or_text_clues": [],
        "conflicts_with_confirmed_points": [],
        "recommended_use": "reuse",
        "style_dna": None,
        "reason": "",
        "candidate_product_name": "Travel mug",
        "candidate_product_name_confidence": 0.9,
    }
    payload.pop(field)

    with pytest.raises(ValueError, match=field):
        _normalize_n1_observation(payload, asset_id)


@pytest.mark.parametrize(
    ("container", "field"),
    [
        ("product_profile", "shared_structure"),
        ("product_profile", "visible_fixed_counts"),
        ("product_profile", "verified_use_relationships"),
        ("product_profile", "included_items"),
        ("product_profile", "other_variants"),
        ("product_profile", "known_conflicts"),
        ("identity_lock", "family_invariants"),
        ("identity_lock", "primary_variant_attributes"),
        ("identity_lock", "exact_component_constraints"),
        ("identity_lock", "verified_hidden_or_internal_structure"),
        ("identity_lock", "use_relationship_constraints"),
    ],
)
def test_n2_requires_every_authoritative_identity_field(container, field):
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
            "primary_appearance": "green mug",
            "shared_structure": [],
            "visible_fixed_counts": [],
            "verified_use_relationships": [],
            "included_items": [],
            "other_variants": [],
            "known_conflicts": [],
        },
        "identity_lock": {
            "family_invariants": [],
            "primary_variant_attributes": [],
            "exact_component_constraints": [],
            "verified_hidden_or_internal_structure": [],
            "use_relationship_constraints": [],
            "must_not_change": ["visible handle"],
        },
        "primary_asset_id": asset_id,
        "supporting_asset_ids": [],
        "standardization_mode": "reuse",
        "standardization_reason": "",
    }
    payload[container].pop(field)

    with pytest.raises(ValueError, match=field):
        _normalize_n2_identity(payload, {asset_id})
