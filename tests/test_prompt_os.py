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
    assert generation.prompt_version.evaluation["fact_policy"] == "user-provided-only"

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
