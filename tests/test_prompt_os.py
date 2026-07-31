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
    assert len(prompts) == 10
    assert all("Silicone cup" in prompt.prompt_text for prompt in prompts)
    assert all(prompt.evaluation["rule_gate"]["decision"] == "pass" for prompt in prompts)
    assert list(
        Generation.objects.filter(cluster=cluster).values_list(
            "output_slot__order",
            flat=True,
        )
    ) == [1]

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
                output = {
                    "plans": [
                        {
                            "slot_order": order,
                            "role": f"role-{order}",
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
                        "source_fact_refs": [],
                        "source_inference_refs": [],
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
    assert prompt_client.n7_calls == ["NODE N7.generic"] * 9
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


def test_tiktok_us_official_no_digital_rendering_rule_blocks_paid_generation():
    from platform_app.models import Batch, OutputSlot, OutputTemplate, RuleProfile
    from platform_app.services import confirm_generation

    user = make_user()
    template = OutputTemplate.objects.create(platform="tiktok", site="US", name="US template")
    OutputSlot.objects.create(template=template, name="Hero", order=1)
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
    make_cluster(batch)

    with pytest.raises(ValueError, match="no_digital_rendering"):
        confirm_generation(batch, user)


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
    assert provider.later_calls == ["N3", "N4", "N7"]
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


def test_prompt_worker_blocks_erp_name_when_n2_reports_visual_identity_conflict(
    tmp_path,
    settings,
):
    import json

    from django.core.management import call_command

    from platform_app.models import Asset, Batch, Cluster, OutputSlot, OutputTemplate, PromptVersion
    from platform_app.services import LocalStorage, process_prompt_once, request_cluster_preparation

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

    class ConflictClient:
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
                raise AssertionError("N3-N7 must not run after an identity conflict")
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

    assert cluster.preparation_status == Cluster.PreparationStatus.BLOCKED
    assert cluster.product_name == "ERP electric kettle"
    assert cluster.analysis_snapshot["identity"]["conflict_state"] == "conflict"
    assert "conflict" in cluster.preparation_error
    assert PromptVersion.objects.filter(cluster=cluster).count() == 0


def test_n1_target_observation_requires_nonempty_visible_identity():
    from platform_app.services import _normalize_n1_observation

    asset_id = "11111111-1111-1111-1111-111111111111"

    with pytest.raises(ValueError, match="observed_identity"):
        _normalize_n1_observation(
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


def test_n1_rejects_schema_placeholder_strings_as_identity_evidence():
    from platform_app.services import _normalize_n1_observation

    asset_id = "11111111-1111-1111-1111-111111111111"

    with pytest.raises(ValueError, match="placeholder"):
        _normalize_n1_observation(
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


def test_blocked_identity_does_not_write_placeholder_product_name(tmp_path, settings):
    import json

    from platform_app.models import Asset, Batch, Cluster
    from platform_app.services import FakeAPIMartClient, LocalStorage, process_prompt_once, request_cluster_preparation

    settings.MEDIA_ROOT = tmp_path
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
                        "contains_target_product": True,
                        "target_is_physical_product": True,
                        "target_visibility": 90,
                        "target_complete": True,
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
                            "standardization_mode": "reuse",
                            "standardization_reason": "",
                        })
                    ),
                    "raw": {},
                }
            return super().optimize_prompt(payload)

    assert process_prompt_once(PlaceholderIdentityClient(), LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()

    assert cluster.preparation_status == Cluster.PreparationStatus.BLOCKED
    assert cluster.product_name == ""
    assert cluster.name != "string"
    assert cluster.analysis_snapshot["identity"]["product_name"] == ""
    assert cluster.analysis_snapshot["identity"]["product_profile"]["category"] == ""


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
        ("image_role", "unrecognized_role", "image_role"),
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
def test_n1_requires_real_owned_product_identity_schema(field, value, message):
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

    with pytest.raises(ValueError, match=message):
        _normalize_n1_observation(payload, asset_id)


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
        ("recommended_use", "evidence_only", "recommended_use"),
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

    with pytest.raises(ValueError, match=message):
        _normalize_n1_observation(payload, asset_id)


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
