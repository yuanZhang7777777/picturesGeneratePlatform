import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext


pytestmark = pytest.mark.django_db


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
    assert generation.prompt_version.provider_model == "gpt-image-2"
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
            "platform": "shopee",
            "site": "US",
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
    batch = Batch.objects.create(owner=user, name="override", market="BR", output_template=template)
    cluster = make_cluster(batch, product_name="Baby care kit")
    cluster.target_consumer = "adult"
    cluster.save(update_fields=["target_consumer"])

    compiled = compile_slot_prompt(cluster, slot)

    assert compiled["target_consumer"] == "adult"
    assert "Model persona: adult" in compiled["prompt"]
    assert compiled["provider_model"] == "gpt-image-2"
    assert "provider_model" not in compiled["prompt"]


def test_confirm_generation_keeps_prompt_override_as_extra_creative_requirements():
    """A creative override must not replace grounded product or identity constraints."""
    from platform_app.models import Batch, OutputSlot, OutputTemplate
    from platform_app.services import confirm_generation

    user = make_user()
    template = OutputTemplate.objects.create(platform="shopee", name="template")
    OutputSlot.objects.create(template=template, name="main", order=1, purpose="Main image")
    batch = Batch.objects.create(owner=user, name="creative", output_template=template)
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
        batch = Batch.objects.create(owner=user, name=name, output_template=template)
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
    assert two_selects == one_selects


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

    from platform_app.models import Asset, Batch, Cluster, OutputTemplate, PromptVersion
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
    request_cluster_preparation(cluster, auto_generate=False)

    class PromptClient(FakeAPIMartClient):
        def observe_images(self, instruction, image_paths):
            assert image_paths
            return {
                "output_text": json.dumps(
                    {
                        "product_name": "Silicone cup",
                        "confidence": 0.91,
                        "product_facts": ["green silicone cup", "two handles"],
                        "identity_lock": "Keep green cup and two handles",
                        "target_consumer": "adult",
                    }
                ),
                "raw": {"node": "vision"},
            }

        def optimize_prompt(self, payload):
            return super().optimize_prompt(payload)

    assert process_prompt_once(PromptClient(), LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()

    assert cluster.preparation_status == "ready"
    assert cluster.product_name == "Silicone cup"
    assert cluster.product_facts == "green silicone cup; two handles"
    assert cluster.identity_lock == "Keep green cup and two handles"
    assert cluster.analysis_snapshot["observations"][0]["confidence"] == 0.91
    assert cluster.analysis_snapshot["fact_ledger"]["review_summary"]["confirmed_count"] == 1
    prompts = list(PromptVersion.objects.filter(cluster=cluster).order_by("output_slot__order"))
    assert [prompt.output_slot.order for prompt in prompts] == list(range(1, 10))
    assert all("Silicone cup" in prompt.prompt_text for prompt in prompts)
    assert all(prompt.evaluation["rule_gate"]["decision"] == "pass" for prompt in prompts)

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
            if "NODE N2" in payload["text"]:
                return {
                    "output_text": json.dumps(
                        {
                            "decision": "needs_confirmation",
                            "confidence": 0.1,
                            "product_name": "Uncertain object",
                            "product_profile": {},
                            "identity_lock": {},
                            "primary_asset_id": str(confirmed_asset.id),
                            "supporting_asset_ids": [],
                        }
                    ),
                    "raw": {},
                }
            if "NODE N5" in payload["text"]:
                return {
                    "output_text": json.dumps(
                        {
                            "strategy_summary": "Distinct purchase decisions",
                            "slot_plans": [
                                {
                                    "slot_id": f"{order:02d}",
                                    "role": f"role-{order}",
                                    "decision_task": f"decision-{order}",
                                    "main_scene": f"scene-{order}",
                                    "main_action": "none",
                                }
                                for order in range(2, 10)
                            ],
                        }
                    ),
                    "raw": {},
                }
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
                    {
                        "product_name": "Storage box",
                        "confidence": 0.9,
                        "product_facts": ["blue box"],
                        "identity_lock": "Keep blue box",
                        "target_consumer": "adult",
                    }
                ),
                "raw": {},
            }

        def optimize_prompt(self, payload):
            if "Previous response:" in payload["text"]:
                self.repairs.append(payload["text"])
                return {
                    "output_text": json.dumps(
                        {
                            "decision": "continue",
                            "confidence": 90,
                            "product_name": "Storage box",
                            "product_profile": {"category": "storage box", "primary_appearance": "blue"},
                            "identity_lock": {"must_not_change": ["blue box"]},
                            "primary_asset_id": str(asset.id),
                            "supporting_asset_ids": [],
                        }
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

    from platform_app.management.commands.seed_platform_templates import GLOBAL_SLOTS
    from platform_app.models import Asset, Batch, Cluster, OutputTemplate, PromptVersion
    from platform_app.services import LocalStorage, process_prompt_once, request_cluster_preparation

    settings.MEDIA_ROOT = tmp_path
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
        def observe_images(self, instruction, image_paths):
            assert "NODE N1" in instruction
            return {
                "output_text": json.dumps(
                    {
                        "asset_id": str(asset.id),
                        "image_role": "clean_product",
                        "contains_target_product": True,
                        "target_complete": True,
                        "reference_quality": 95,
                        "observed_identity": {
                            "category_candidates": ["storage container"],
                            "dominant_colors": ["sage green"],
                            "distinctive_parts": ["two handles"],
                        },
                        "recommended_use": "reuse",
                    }
                ),
                "raw": {},
            }

        def optimize_prompt(self, payload):
            text = payload["text"]
            if "NODE N2" in text:
                output = {
                    "decision": "continue",
                    "confidence": 93,
                    "product_name": "Sage storage container",
                    "product_profile": {"category": "storage container", "primary_appearance": "sage green"},
                    "identity_lock": {
                        "family_invariants": ["storage container"],
                        "primary_variant_attributes": ["sage green"],
                        "must_not_change": ["two handles"],
                    },
                    "primary_asset_id": str(asset.id),
                    "supporting_asset_ids": [],
                }
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
                    "main_scene": "pure white commercial studio",
                    "main_action": "none",
                    "visible_text_lines": [],
                    "prompt": "Front-facing complete product on pure white.",
                }
            elif "NODE N5" in text:
                output = {
                    "plans": [
                        {
                            "slot_order": order,
                            "scene_family": f"family-{order}",
                            "conversion_goal": f"goal-{order}",
                            "main_scene": f"scene-{order}",
                            "main_action": "none",
                            "visible_text_lines": [],
                        }
                        for order in range(2, 10)
                    ]
                }
            elif "NODE N6" in text:
                order = int(text.split("SLOT_ORDER=", 1)[1].splitlines()[0])
                output = {
                    "slot_order": order,
                    "main_scene": f"scene-{order}",
                    "main_action": "none",
                    "visible_text_lines": [],
                    "prompt": f"Show purchase decision scene {order}.",
                }
            else:
                raise AssertionError(text)
            return {"output_text": json.dumps(output), "raw": {}}

    assert process_prompt_once(PromptOSClient(), LocalStorage(tmp_path)) == 1
    cluster.refresh_from_db()

    assert cluster.preparation_status == Cluster.PreparationStatus.READY
    assert cluster.product_name == "Sage storage container"
    assert cluster.analysis_snapshot["fact_ledger"]["review_summary"]["observed_count"] == 1
    assert [snapshot["node_id"] for snapshot in cluster.analysis_snapshot["prompt_os"]] == [
        "N1",
        "N2",
        "N3",
        "N4",
        "N5",
        *["N6"] * 8,
        *["N7"] * 9,
    ]
    prompts = list(PromptVersion.objects.filter(cluster=cluster).order_by("output_slot__order"))
    assert len(prompts) == 9
    assert all("Sage storage container" in prompt.prompt_text for prompt in prompts)
    assert all("two handles" in prompt.prompt_text for prompt in prompts)
    assert all(len(prompt.prompt_text) <= 3500 for prompt in prompts)
    assert all(prompt.evaluation["rule_gate"]["decision"] == "pass" for prompt in prompts)


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
        market="US",
        output_template=template,
        rule_profile=rule,
    )
    make_cluster(batch)

    with pytest.raises(ValueError, match="no_digital_rendering"):
        confirm_generation(batch, user)
