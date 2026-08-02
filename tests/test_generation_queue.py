from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from PIL import Image


pytestmark = pytest.mark.django_db


def make_user(username="operator"):
    return get_user_model().objects.create_user(
        username=username,
        password="long-enough-password",
        daily_generation_limit=100,
    )


def image_bytes():
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
    return buffer.getvalue()


def make_batch_with_images(tmp_path, settings, count=1):
    from platform_app.models import OutputSlot, OutputTemplate
    from platform_app.services import create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    template = OutputTemplate.objects.create(platform="global", site="", name="Test global baseline")
    OutputSlot.objects.create(template=template, name="main", order=1)
    batch = create_batch(user, "Batch 1")
    for index in range(count):
        register_uploaded_asset(batch, f"{index}.png", image_bytes(), "image/png")
    return user, batch


def approve_prompt(
    cluster,
    user,
    slot,
    *,
    revision=1,
    config_signature=None,
    node_name=None,
    template_version="test-v1",
    references_override=None,
):
    from platform_app.models import Cluster
    from platform_app.services import _create_gated_prompt_version
    from platform_app.template_policy import (
        is_source_product_photo_slot,
        is_standard_product_hero_slot,
    )

    cluster.refresh_from_db()
    cluster.preparation_status = Cluster.PreparationStatus.READY
    primary = cluster.cluster_assets.select_related("asset").order_by("order", "id").first()
    cluster.analysis_snapshot = {
        **cluster.analysis_snapshot,
        "_preparation_revision": revision,
        "identity": cluster.analysis_snapshot.get("identity")
        or {
            "primary_asset_id": str(primary.asset_id),
            "supporting_asset_ids": [],
        },
    }
    cluster.save(update_fields=["preparation_status", "analysis_snapshot"])
    references = [
        relation.asset.storage_path
        for relation in cluster.cluster_assets.select_related("asset").order_by("order", "id")
    ]
    source = is_source_product_photo_slot(slot)
    resolved_node = node_name or (
        "source_passthrough"
        if source
        else "N4"
        if is_standard_product_hero_slot(slot)
        else "N6"
    )
    snapshot = {
        "source_asset_id": str(primary.asset_id),
        "reference_snapshot": (
            list(references_override)
            if references_override is not None
            else references[:1] if source else references
        ),
    }
    return _create_gated_prompt_version(
        cluster=cluster,
        batch=cluster.batch,
        slot=slot,
        user=user,
        node_name=resolved_node,
        template_version=template_version,
        provider_model="none" if source else "gpt-image-2",
        prompt_text=(
            "Preserve the original seller product photo without AI modification."
            if source
            else f"Approved prompt {slot.order}"
        ),
        input_snapshot=snapshot,
        structured_output={**snapshot, "visible_text_lines": []},
        source_snapshot=snapshot,
        references=snapshot["reference_snapshot"],
        fact_policy="source-passthrough" if source else "traceable-inference",
    )


def queue_approved_hero(tmp_path, settings, username="approved-worker"):
    from platform_app.models import OutputTemplate
    from platform_app.services import ensure_cluster_generations

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    if user.username != username:
        user.username = username
        user.save(update_fields=["username"])
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    slot = template.slots.get(order=1)
    approve_prompt(cluster, user, slot)
    return user, batch, cluster, ensure_cluster_generations(cluster, user)[0]


def test_preflight_counts_clusters_and_quota(tmp_path, settings):
    from platform_app.services import preflight_batch

    user, batch = make_batch_with_images(tmp_path, settings, count=2)

    result = preflight_batch(batch, user)

    assert result["cluster_count"] == 2
    assert result["generation_count"] == 2
    assert result["org_remaining"] == 2000
    assert result["user_remaining"] == 100
    assert result["blocking_errors"] == []


@override_settings(USER_DAILY_GENERATION_LIMIT=1, GENERATION_QUOTAS_ENABLED=False)
def test_confirm_generation_ignores_daily_quota_when_business_quota_is_disabled(tmp_path, settings):
    from platform_app.services import confirm_generation

    user, batch = make_batch_with_images(tmp_path, settings, count=2)
    user.daily_generation_limit = 1
    user.save(update_fields=["daily_generation_limit"])

    generations = confirm_generation(batch, user)

    assert len(generations) == 2


def test_confirm_generation_is_idempotent(tmp_path, settings):
    from platform_app.services import confirm_generation

    user, batch = make_batch_with_images(tmp_path, settings, count=2)

    first = confirm_generation(batch, user)
    second = confirm_generation(batch, user)

    assert [item.id for item in second] == [item.id for item in first]
    assert batch.generations.count() == 2


def test_ensure_cluster_generations_creates_detail_slots_only_after_completed_hero(tmp_path, settings):
    from platform_app.models import Generation, OutputSlot, OutputTemplate, PromptVersion, ResultAsset
    from platform_app.services import ensure_cluster_generations

    settings.MEDIA_ROOT = tmp_path
    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    for order in range(2, 10):
        OutputSlot.objects.create(template=template, name=f"Slot {order}", order=order)
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    for slot in template.slots.order_by("order"):
        approve_prompt(cluster, user, slot)

    first = ensure_cluster_generations(cluster, user)
    second = ensure_cluster_generations(cluster, user)

    assert [item.id for item in second] == [item.id for item in first]
    assert list(cluster.generations.values_list("output_slot__order", flat=True)) == [1]

    hero = first[0]
    hero.status = Generation.Status.COMPLETED
    hero.save(update_fields=["status", "updated_at"])
    hero_path = f"results/{batch.id}/{cluster.id}/{hero.output_slot_id}/1/{hero.id}.png"
    ResultAsset.objects.create(
        generation=hero,
        storage_path=hero_path,
        sha256="1" * 64,
        file_size=10,
    )

    all_generations = ensure_cluster_generations(cluster, user)

    assert [item.output_slot.order for item in all_generations] == list(range(1, 10))
    detail_refs = [
        generation.reference_snapshot
        for generation in all_generations
        if generation.output_slot.order == 2
    ][0]
    assert hero_path in detail_refs


def test_ensure_cluster_generations_cannot_compile_a_missing_prompt_version(
    tmp_path,
    settings,
):
    from platform_app.models import Cluster, Generation, OutputTemplate, PromptVersion
    from platform_app.services import ensure_cluster_generations

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    cluster.preparation_status = Cluster.PreparationStatus.READY
    cluster.analysis_snapshot = {"_preparation_revision": 1}
    cluster.save(update_fields=["preparation_status", "analysis_snapshot"])

    with pytest.raises(ValueError, match="approved PromptVersion"):
        ensure_cluster_generations(cluster, user)

    assert PromptVersion.objects.filter(cluster=cluster).count() == 0
    assert Generation.objects.filter(cluster=cluster).count() == 0


@pytest.mark.parametrize(
    ("revision", "node_name", "gate", "current_config", "message"),
    [
        (1, "N4", {"decision": "pass", "hard_blocks": [], "snapshot_id": "old"}, True, "revision"),
        (2, "N4", {"decision": "pass", "hard_blocks": [], "snapshot_id": "gate"}, False, "configuration"),
        (2, "manual_edit", {"decision": "pass", "hard_blocks": [], "snapshot_id": "gate"}, True, "N4"),
        (2, "N4", {"decision": "pass", "hard_blocks": []}, True, "N7"),
        (
            2,
            "N4",
            {
                "decision": "pass",
                "hard_blocks": ["deterministic.identity"],
                "snapshot_id": "gate",
                "semantic_decision": "pass",
            },
            True,
            "blocked",
        ),
    ],
)
def test_ensure_cluster_generations_requires_current_n4_and_passing_n7(
    tmp_path,
    settings,
    revision,
    node_name,
    gate,
    current_config,
    message,
):
    from platform_app.models import Cluster, Generation, OutputTemplate, PromptVersion
    from platform_app.services import (
        _effective_config_signature,
        _image_request_snapshot,
        _preparation_lineage,
        _prompt_node_template_binding,
        ensure_cluster_generations,
    )

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    slot = template.slots.get(order=1)
    cluster.preparation_status = Cluster.PreparationStatus.READY
    cluster.analysis_snapshot = {"_preparation_revision": 2}
    cluster.save(update_fields=["preparation_status", "analysis_snapshot"])
    signature = _effective_config_signature(batch, cluster)
    prompt_signature = signature if current_config else "stale-config"
    snapshot = {
        "_preparation_revision": revision,
        "_effective_config_signature": prompt_signature,
    }
    gate = {
        **gate,
        "preparation_revision": revision,
        "effective_config_signature": prompt_signature,
    }
    PromptVersion.objects.create(
        cluster=cluster,
        output_slot=slot,
        created_by=user,
        node_name=node_name,
        prompt_text="Candidate prompt",
        input_snapshot=snapshot,
        source_snapshot=snapshot,
        structured_output=snapshot,
        evaluation={"rule_gate": gate},
    )

    with pytest.raises(ValueError, match=message):
        ensure_cluster_generations(cluster, user)

    assert Generation.objects.filter(cluster=cluster).count() == 0


def test_generation_gate_rejects_forged_n7_snapshot_id_without_same_slot_record(
    tmp_path,
    settings,
):
    from platform_app.models import Cluster, OutputTemplate, PromptVersion
    from platform_app.services import (
        _effective_config_signature,
        _image_request_snapshot,
        _preparation_lineage,
        _prompt_node_template_binding,
        ensure_cluster_generations,
    )

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    slot = template.slots.get(order=1)
    cluster.preparation_status = Cluster.PreparationStatus.READY
    cluster.analysis_snapshot = {
        "_preparation_revision": 1,
        "identity": {
            "primary_asset_id": str(cluster.cluster_assets.get().asset_id),
            "supporting_asset_ids": [],
        },
    }
    cluster.save(update_fields=["preparation_status", "analysis_snapshot"])
    signature = _effective_config_signature(batch, cluster)
    forged = {
        "_preparation_revision": 1,
        "_effective_config_signature": signature,
        "_preparation_lineage": _preparation_lineage(cluster, batch, slot),
        "_prompt_node_template": _prompt_node_template_binding("N4", "builtin-v1"),
        "_image_request": _image_request_snapshot(
            size=batch.size or template.default_size,
            resolution=batch.resolution or template.default_resolution,
        ),
    }
    PromptVersion.objects.create(
        cluster=cluster,
        output_slot=slot,
        created_by=user,
        node_name="N4",
        prompt_text="Forged prompt",
        input_snapshot=forged,
        source_snapshot=forged,
        structured_output=forged,
        evaluation={
            "rule_gate": {
                "decision": "pass",
                "hard_blocks": [],
                "snapshot_id": "forged",
                "preparation_revision": 1,
                "effective_config_signature": signature,
                "lineage": forged["_preparation_lineage"],
            }
        },
    )

    with pytest.raises(ValueError, match="same-slot N7"):
        ensure_cluster_generations(cluster, user)


@pytest.mark.parametrize("mutated", ["template", "rule"])
def test_generation_gate_detects_template_and_rule_content_changes(
    tmp_path,
    settings,
    mutated,
):
    from platform_app.models import OutputTemplate, RuleProfile
    from platform_app.services import ensure_cluster_generations

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    slot = template.slots.get(order=1)
    rule = RuleProfile.objects.create(
        platform="global",
        site="",
        name="Rules",
        status=RuleProfile.Status.PUBLISHED,
        rules=[],
    )
    batch.rule_profile = rule
    batch.save(update_fields=["rule_profile"])
    approve_prompt(cluster, user, slot)

    if mutated == "template":
        slot.purpose = "Changed after approval"
        slot.save(update_fields=["purpose"])
    else:
        rule.rules = [{"rule_id": "changed", "severity": "HARD_PLATFORM"}]
        rule.save(update_fields=["rules"])

    with pytest.raises(ValueError, match="content"):
        ensure_cluster_generations(cluster, user)


def test_source_passthrough_requires_current_source_prompt_and_n7(
    tmp_path,
    settings,
):
    from django.core.management import call_command

    from platform_app.models import Generation
    from platform_app.services import (
        create_project,
        ensure_cluster_generations,
        register_uploaded_asset,
    )

    settings.MEDIA_ROOT = tmp_path
    call_command("seed_platform_templates")
    user = make_user("source-gate")
    batch = create_project(
        user,
        name="Shopee VN source gate",
        platform="shopee",
        market="VN",
    )
    asset = register_uploaded_asset(
        batch,
        "source.png",
        image_bytes(),
        "image/png",
    )
    cluster = asset.clusters.get()
    for slot in batch.output_template.slots.exclude(name="Seller original product photo"):
        approve_prompt(cluster, user, slot)

    with pytest.raises(ValueError, match="source PromptVersion"):
        ensure_cluster_generations(cluster, user)

    assert Generation.objects.filter(cluster=cluster).count() == 0


def test_generation_references_follow_n2_white_and_marketing_order(
    tmp_path,
    settings,
):
    from platform_app.models import (
        Asset,
        ClusterAsset,
        Generation,
        OutputSlot,
        OutputTemplate,
        ResultAsset,
    )
    from platform_app.services import ensure_cluster_generations

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    hero_slot = template.slots.get(order=1)
    detail_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    primary = cluster.cluster_assets.get().asset
    supports = []
    for index in range(3):
        path = f"originals/{batch.id}/support-{index}.png"
        (tmp_path / path).write_bytes(image_bytes())
        asset = Asset.objects.create(
            batch=batch,
            kind=Asset.Kind.IMAGE,
            original_filename=f"support-{index}.png",
            storage_path=path,
            sha256=str(index + 3).rjust(64, "0"),
            file_size=len(image_bytes()),
            content_type="image/png",
        )
        ClusterAsset.objects.create(
            cluster=cluster,
            asset=asset,
            role=ClusterAsset.Role.REFERENCE,
            order=index + 2,
        )
        supports.append(asset)
    cluster.analysis_snapshot = {
        "_preparation_revision": 3,
        "identity": {
            "primary_asset_id": str(primary.id),
            "supporting_asset_ids": [str(asset.id) for asset in supports],
            "target_appearances": [
                {
                    "appearance_id": "appearance.primary",
                    "asset_ids": [str(primary.id)],
                    "primary_asset_id": str(primary.id),
                },
                {
                    "appearance_id": "appearance.variant",
                    "asset_ids": [str(asset.id) for asset in supports],
                    "primary_asset_id": str(supports[0].id),
                },
            ],
        },
    }
    cluster.save(update_fields=["analysis_snapshot"])
    approve_prompt(cluster, user, hero_slot, revision=3)
    approve_prompt(
        cluster,
        user,
        detail_slot,
        revision=3,
        references_override=[supports[0].storage_path],
    )

    hero = ensure_cluster_generations(cluster, user)[0]

    assert hero.reference_snapshot == [
        primary.storage_path,
        supports[0].storage_path,
    ]

    hero.status = Generation.Status.COMPLETED
    hero.save(update_fields=["status"])
    hero_path = f"results/{batch.id}/{cluster.id}/{hero_slot.id}/1/hero.png"
    ResultAsset.objects.create(
        generation=hero,
        storage_path=hero_path,
        sha256="9" * 64,
        file_size=10,
    )

    generations = ensure_cluster_generations(cluster, user)
    detail = next(item for item in generations if item.output_slot_id == detail_slot.id)

    assert detail.reference_snapshot == [hero_path, supports[0].storage_path]
    cluster.refresh_from_db()
    n7_by_id = {
        snapshot["snapshot_id"]: snapshot
        for snapshot in cluster.analysis_snapshot["prompt_os"]
        if snapshot["node_id"].startswith("N7")
    }
    for generation in (hero, detail):
        gate = generation.prompt_version.evaluation["rule_gate"]
        n7 = n7_by_id[gate["snapshot_id"]]
        assert n7["slot_id"] == str(generation.output_slot_id)
        assert (
            n7["input_snapshot"]["reference_snapshot"]
            == generation.reference_snapshot
        )
        assert n7["input_snapshot"]["structural_asset_id"] == str(primary.id)
    assert detail.prompt_version.evaluation["rule_gate"]["snapshot_id"] in n7_by_id
    detail_n7 = n7_by_id[
        detail.prompt_version.evaluation["rule_gate"]["snapshot_id"]
    ]
    assert detail_n7["input_snapshot"]["hero_generation_id"] == str(hero.id)


def test_submission_rejects_white_reference_outside_current_n2_assets(
    tmp_path,
    settings,
):
    from platform_app.models import Generation
    from platform_app.services import (
        _claim_generation_for_submission,
        _create_gated_prompt_version,
        _current_rule_bundle_snapshot,
        _template_snapshot,
        _validate_generation_submission,
    )

    user, batch, cluster, valid = queue_approved_hero(
        tmp_path,
        settings,
        username="forged-white-reference",
    )
    forged_references = ["originals/other-product/competitor.png"]
    prompt_version = _create_gated_prompt_version(
        cluster=cluster,
        batch=batch,
        slot=valid.output_slot,
        user=user,
        node_name="N4",
        template_version="test-v1",
        provider_model="gpt-image-2",
        prompt_text="Forged but N7-gated prompt",
        input_snapshot={},
        structured_output={"visible_text_lines": []},
        source_snapshot={},
        references=forged_references,
    )
    forged = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=valid.output_slot,
        prompt_version=prompt_version,
        created_by=user,
        attempt=2,
        prompt_text=prompt_version.prompt_text,
        size=valid.size,
        resolution=valid.resolution,
        reference_snapshot=forged_references,
        template_snapshot=_template_snapshot(batch.output_template, valid.output_slot),
        rule_snapshot=_current_rule_bundle_snapshot(batch, valid.output_slot),
    )

    claimed = _claim_generation_for_submission(forged.id)
    with pytest.raises(ValueError, match="N2|reference"):
        _validate_generation_submission(claimed)


def test_submission_rejects_marketing_structural_reference_from_other_product(
    tmp_path,
    settings,
):
    from platform_app.models import Generation, OutputSlot, OutputTemplate, ResultAsset
    from platform_app.services import (
        _claim_generation_for_submission,
        _create_gated_prompt_version,
        _current_rule_bundle_snapshot,
        _template_snapshot,
        _validate_generation_submission,
        ensure_cluster_generations,
    )

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    hero_slot = template.slots.get(order=1)
    detail_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    approve_prompt(cluster, user, hero_slot)
    approve_prompt(cluster, user, detail_slot)
    hero = ensure_cluster_generations(cluster, user)[0]
    hero.status = Generation.Status.COMPLETED
    hero.save(update_fields=["status", "updated_at"])
    hero_path = f"results/{batch.id}/{cluster.id}/{hero_slot.id}/1/hero.png"
    ResultAsset.objects.create(
        generation=hero,
        storage_path=hero_path,
        sha256="8" * 64,
        file_size=10,
    )
    valid_detail = next(
        item
        for item in ensure_cluster_generations(cluster, user)
        if item.output_slot_id == detail_slot.id
    )
    cluster.refresh_from_db()
    forged_references = [hero_path, "originals/other-product/history.png"]
    prompt_version = _create_gated_prompt_version(
        cluster=cluster,
        batch=batch,
        slot=detail_slot,
        user=user,
        node_name="N6",
        template_version="test-v1",
        provider_model="gpt-image-2",
        prompt_text=valid_detail.prompt_text,
        input_snapshot={},
        structured_output={"visible_text_lines": []},
        source_snapshot={},
        references=forged_references,
        hero_generation=hero,
    )
    forged = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=detail_slot,
        prompt_version=prompt_version,
        created_by=user,
        attempt=2,
        prompt_text=prompt_version.prompt_text,
        size=valid_detail.size,
        resolution=valid_detail.resolution,
        reference_snapshot=forged_references,
        template_snapshot=_template_snapshot(template, detail_slot),
        rule_snapshot=_current_rule_bundle_snapshot(batch, detail_slot),
    )

    claimed = _claim_generation_for_submission(forged.id)
    with pytest.raises(ValueError, match="N2|structural|reference"):
        _validate_generation_submission(claimed)


@pytest.mark.parametrize("mutation", ["status", "default_size"])
def test_worker_rechecks_published_template_and_default_request_parameters(
    tmp_path,
    settings,
    mutation,
):
    from platform_app.models import Generation, OutputTemplate
    from platform_app.services import LocalStorage, process_generation_once

    class CapturingClient:
        def __init__(self):
            self.calls = 0

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.calls += 1
            return "must-not-submit"

    _, batch, _, generation = queue_approved_hero(
        tmp_path,
        settings,
        username=f"template-{mutation}",
    )
    template = OutputTemplate.objects.get(id=batch.output_template_id)
    if mutation == "status":
        template.status = OutputTemplate.Status.RETIRED
        template.save(update_fields=["status"])
    else:
        template.default_size = "3:4"
        template.save(update_fields=["default_size"])

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()
    assert client.calls == 0
    assert generation.status == Generation.Status.FAILED


def test_generation_worker_respects_active_provider_limit(tmp_path, settings):
    from platform_app.models import Generation, OutputSlot, OutputTemplate
    from platform_app.services import (
        LocalStorage,
        create_batch,
        ensure_cluster_generations,
        process_generation_once,
        register_uploaded_asset,
    )

    class PollingOnlyClient:
        def submit_generation(self, prompt, image_paths, size, resolution):
            raise AssertionError("must not submit when active limit is full")

        def get_task(self, task_id):
            return {"status": "processing"}

    def queued_generation(user, name):
        batch = create_batch(user, name)
        register_uploaded_asset(batch, f"{name}.png", image_bytes(), "image/png")
        batch.output_template = template
        batch.save(update_fields=["output_template"])
        cluster = batch.clusters.get()
        approve_prompt(cluster, user, slot)
        return ensure_cluster_generations(cluster, user)[0]

    settings.MAX_ACTIVE_GENERATIONS = 1
    settings.MEDIA_ROOT = tmp_path
    template = OutputTemplate.objects.create(platform="global", site="", name="Active limit template")
    slot = OutputSlot.objects.create(template=template, name="main", order=1)
    active = queued_generation(make_user("active-limit-a"), "active-limit-a")
    active.status = Generation.Status.PROCESSING
    active.provider_task_id = "task-active"
    active.save(update_fields=["status", "provider_task_id", "updated_at"])
    queued = queued_generation(make_user("active-limit-b"), "active-limit-b")

    assert process_generation_once(PollingOnlyClient(), LocalStorage(tmp_path)) == 1

    queued.refresh_from_db()
    assert queued.status == Generation.Status.QUEUED


def test_generation_worker_lets_other_users_use_idle_capacity(tmp_path, settings):
    from platform_app.models import Generation, OutputSlot, OutputTemplate
    from platform_app.services import (
        LocalStorage,
        create_batch,
        ensure_cluster_generations,
        process_generation_once,
        register_uploaded_asset,
    )

    class CapturingClient:
        def __init__(self):
            self.calls = []

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.calls.append(prompt)
            return f"task-{len(self.calls)}"

    def queued_generation(user, name):
        batch = create_batch(user, name)
        register_uploaded_asset(batch, f"{name}.png", image_bytes(), "image/png")
        batch.output_template = template
        batch.save(update_fields=["output_template"])
        cluster = batch.clusters.get()
        approve_prompt(cluster, user, slot)
        return ensure_cluster_generations(cluster, user)[0]

    settings.MAX_ACTIVE_GENERATIONS = 2
    settings.GENERATION_USER_ACTIVE_SOFT_LIMIT = 1
    settings.MEDIA_ROOT = tmp_path
    user_a = make_user("fair-a")
    user_b = make_user("fair-b")
    template = OutputTemplate.objects.create(platform="global", site="", name="Fair template")
    slot = OutputSlot.objects.create(template=template, name="main", order=1)
    active_a = queued_generation(user_a, "active-a")
    active_a.status = Generation.Status.PROCESSING
    active_a.provider_task_id = "task-active-a"
    active_a.save(update_fields=["status", "provider_task_id", "updated_at"])
    queued_a = queued_generation(user_a, "queued-a")
    queued_b = queued_generation(user_b, "queued-b")

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1

    queued_a.refresh_from_db()
    queued_b.refresh_from_db()
    assert queued_a.status == Generation.Status.QUEUED
    assert queued_b.status == Generation.Status.SUBMITTED


@pytest.mark.parametrize("mutation", ["status", "instruction", "output_schema"])
def test_worker_rechecks_exact_prompt_node_template_content(
    tmp_path,
    settings,
    mutation,
):
    from platform_app.models import Generation, OutputTemplate, PromptNodeTemplate
    from platform_app.services import (
        LocalStorage,
        ensure_cluster_generations,
        process_generation_once,
    )

    class CapturingClient:
        def __init__(self):
            self.calls = 0

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.calls += 1
            return "must-not-submit"

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    slot = template.slots.get(order=1)
    node_template = PromptNodeTemplate.objects.create(
        node_name="N4",
        version="2.1.0",
        status=PromptNodeTemplate.Status.PUBLISHED,
        instruction="Complete N4 instruction.",
        output_schema={"type": "object"},
    )
    approve_prompt(
        cluster,
        user,
        slot,
        template_version=node_template.version,
    )
    generation = ensure_cluster_generations(cluster, user)[0]
    if mutation == "status":
        node_template.status = PromptNodeTemplate.Status.RETIRED
        node_template.save(update_fields=["status"])
    elif mutation == "instruction":
        node_template.instruction = "Changed instruction."
        node_template.save(update_fields=["instruction"])
    else:
        node_template.output_schema = {"type": "array"}
        node_template.save(update_fields=["output_schema"])

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()
    assert client.calls == 0
    assert generation.status == Generation.Status.FAILED


@pytest.mark.parametrize("mutation", ["model", "size"])
def test_submission_rechecks_actual_image_request_bound_by_n7(
    tmp_path,
    settings,
    mutation,
):
    from platform_app.models import Generation
    from platform_app.services import (
        _claim_generation_for_submission,
        _current_rule_bundle_snapshot,
        _template_snapshot,
        _validate_generation_submission,
    )

    user, batch, cluster, valid = queue_approved_hero(
        tmp_path,
        settings,
        username=f"request-{mutation}",
    )
    candidate = valid
    if mutation == "model":
        settings.APIMART_IMAGE_MODEL = "different-image-model"
    else:
        candidate = Generation.objects.create(
            batch=batch,
            cluster=cluster,
            output_slot=valid.output_slot,
            prompt_version=valid.prompt_version,
            created_by=user,
            attempt=2,
            prompt_text=valid.prompt_text,
            size="3:4",
            resolution=valid.resolution,
            reference_snapshot=valid.reference_snapshot,
            template_snapshot=_template_snapshot(
                batch.output_template,
                valid.output_slot,
            ),
            rule_snapshot=_current_rule_bundle_snapshot(
                batch,
                valid.output_slot,
            ),
        )

    claimed = _claim_generation_for_submission(candidate.id)
    with pytest.raises(ValueError, match="request|model|size"):
        _validate_generation_submission(claimed)


def test_worker_rechecks_submission_fingerprint_after_interleaved_config_change(
    tmp_path,
    settings,
):
    from contextlib import contextmanager

    from platform_app.models import Batch, Generation
    from platform_app.services import LocalStorage, process_generation_once

    class CapturingClient:
        def __init__(self):
            self.calls = 0

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.calls += 1
            return "must-not-submit-stale-request"

    class InterleavingStorage:
        @contextmanager
        def reference_paths(self, storage_paths):
            Batch.objects.filter(id=batch.id).update(size="3:4")
            with LocalStorage(tmp_path).reference_paths(storage_paths) as paths:
                yield paths

    _, batch, _, generation = queue_approved_hero(
        tmp_path,
        settings,
        username="submission-interleaving",
    )
    client = CapturingClient()

    assert process_generation_once(client, InterleavingStorage()) == 1
    generation.refresh_from_db()
    assert client.calls == 0
    assert generation.status == Generation.Status.FAILED
    assert generation.provider_payload["submission"]["fingerprint"]


@pytest.mark.django_db(transaction=True)
def test_provider_submit_holds_every_submission_dependency_row_until_post_returns(
    tmp_path,
    settings,
):
    import threading
    import time

    from django.db import OperationalError, close_old_connections

    from platform_app.models import (
        Asset,
        Batch,
        Cluster,
        Generation,
        OutputTemplate,
        PromptNodeTemplate,
        RuleProfile,
    )
    from platform_app.services import (
        LocalStorage,
        ensure_cluster_generations,
        process_generation_once,
    )

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    rule_profile = RuleProfile.objects.create(
        platform="shopee",
        site="SG",
        name="Submission lock rule",
        version="lock-v1",
        status=RuleProfile.Status.PUBLISHED,
        rules=[],
    )
    node_template = PromptNodeTemplate.objects.create(
        node_name="N4",
        version="lock-v1",
        status=PromptNodeTemplate.Status.PUBLISHED,
        instruction="Complete locked N4 instruction.",
        output_schema={"type": "object"},
    )
    batch.output_template = template
    batch.rule_profile = rule_profile
    batch.save(update_fields=["output_template", "rule_profile"])
    slot = template.slots.get(order=1)
    approve_prompt(cluster, user, slot, template_version=node_template.version)
    generation = ensure_cluster_generations(cluster, user)[0]
    asset = cluster.cluster_assets.get().asset

    mutations = {
        "batch": lambda: Batch.objects.filter(id=batch.id).update(size="3:4"),
        "cluster": lambda: Cluster.objects.filter(id=cluster.id).update(
            analysis_snapshot={}
        ),
        "asset": lambda: Asset.objects.filter(id=asset.id).update(
            storage_path=f"{asset.storage_path}.changed"
        ),
        "template": lambda: OutputTemplate.objects.filter(id=template.id).update(
            default_size="3:4"
        ),
        "rule": lambda: RuleProfile.objects.filter(id=rule_profile.id).update(
            rules=[{"rule_id": "changed"}]
        ),
        "node": lambda: PromptNodeTemplate.objects.filter(
            id=node_template.id
        ).update(instruction="Changed while posting."),
    }

    class InterleavingClient:
        def __init__(self):
            self.threads = []
            self.started = {}
            self.finished = {}
            self.errors = []
            self.finished_before_post = []

        def _mutate(self, name, action):
            close_old_connections()
            self.started[name].set()
            deadline = time.monotonic() + 5
            while True:
                try:
                    action()
                except OperationalError as exc:
                    if "lock" in str(exc).lower() and time.monotonic() < deadline:
                        close_old_connections()
                        time.sleep(0.01)
                        continue
                    self.errors.append((name, str(exc)))
                    return
                finally:
                    close_old_connections()
                self.finished[name].set()
                return

        def submit_generation(self, prompt, image_paths, size, resolution):
            for name, action in mutations.items():
                self.started[name] = threading.Event()
                self.finished[name] = threading.Event()
                thread = threading.Thread(
                    target=self._mutate,
                    args=(name, action),
                    daemon=True,
                )
                self.threads.append(thread)
                thread.start()
            assert all(event.wait(1) for event in self.started.values())
            time.sleep(0.15)
            self.finished_before_post = [
                name for name, event in self.finished.items() if event.is_set()
            ]
            return "locked-provider-task"

    client = InterleavingClient()

    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    for thread in client.threads:
        thread.join(5)
    generation.refresh_from_db()

    assert client.finished_before_post == []
    assert client.errors == []
    assert all(event.is_set() for event in client.finished.values())
    assert generation.status == Generation.Status.SUBMITTED


def test_project_settings_reject_change_while_sealed_submission_is_active(
    tmp_path,
    settings,
):
    from platform_app.services import (
        _claim_generation_for_submission,
        _seal_generation_submission,
        update_project_settings,
    )

    _, batch, _, generation = queue_approved_hero(
        tmp_path,
        settings,
        username="sealed-settings",
    )
    claimed = _claim_generation_for_submission(generation.id)
    sealed = _seal_generation_submission(claimed.id)

    with pytest.raises(ValueError, match="active submission"):
        update_project_settings(
            batch,
            {
                "platform": batch.platform,
                "market": batch.market or batch.site,
                "seller_tier": batch.seller_tier,
                "size": "3:4",
                "resolution": batch.resolution,
                "global_prompt": batch.global_prompt,
            },
        )

    sealed.refresh_from_db()
    assert sealed.provider_payload["submission"]["fingerprint"]


def test_sealed_submission_fingerprint_cannot_be_overwritten(
    tmp_path,
    settings,
):
    from django.core.exceptions import ValidationError

    from platform_app.models import Generation
    from platform_app.services import (
        _claim_generation_for_submission,
        _seal_generation_submission,
    )

    _, _, _, generation = queue_approved_hero(
        tmp_path,
        settings,
        username="immutable-submission",
    )
    claimed = _claim_generation_for_submission(generation.id)
    sealed = _seal_generation_submission(claimed.id)
    original = sealed.provider_payload["submission"]

    sealed.provider_payload = {
        "submission": {**original, "fingerprint": "forged"},
    }
    with pytest.raises(ValidationError, match="submission"):
        sealed.save(update_fields=["provider_payload", "updated_at"])

    with pytest.raises(ValidationError, match="submission"):
        Generation.objects.filter(id=sealed.id).update(
            provider_payload={
                "submission": {**original, "fingerprint": "forged"},
            }
        )


@pytest.mark.parametrize(
    "status",
    [
        "queued",
        "submitting",
        "processing",
    ],
)
def test_regenerate_rejects_active_source_generation(
    tmp_path,
    settings,
    status,
):
    from platform_app.models import Generation
    from platform_app.services import regenerate_generation

    user, _, cluster, source = queue_approved_hero(
        tmp_path,
        settings,
        username=f"active-regenerate-{status}",
    )
    source.status = status
    source.save(update_fields=["status", "updated_at"])

    with pytest.raises(ValueError, match="terminal|active|status"):
        regenerate_generation(source, user)

    assert Generation.objects.filter(cluster=cluster).count() == 1


def test_duplicate_n9_retry_rolls_back_orphan_prompt_and_n7(
    tmp_path,
    settings,
):
    import json

    from platform_app.models import Generation, PromptVersion
    from platform_app.services import _create_simplified_failure_retry

    class ComplexityClient:
        def optimize_prompt(self, payload):
            return {
                "output_text": json.dumps(
                    {
                        "decision": "retry_with_simplified_prompt",
                        "simplified_prompt": "Exact.",
                        "visible_text_lines": [],
                    }
                )
            }

    _, _, cluster, source = queue_approved_hero(
        tmp_path,
        settings,
        username="n9-atomicity",
    )
    source.status = Generation.Status.FAILED
    source.failure_reason = "prompt too complex"
    source.provider_payload = {
        "status": "failed",
        "error_code": "prompt_complexity",
    }
    source.save(
        update_fields=[
            "status",
            "failure_reason",
            "provider_payload",
            "updated_at",
        ]
    )
    client = ComplexityClient()
    first = _create_simplified_failure_retry(source, client)
    cluster.refresh_from_db()
    n7_count = len(
        [
            snapshot
            for snapshot in cluster.analysis_snapshot["prompt_os"]
            if snapshot["node_id"].startswith("N7")
        ]
    )

    with pytest.raises(ValueError, match="newer generation"):
        _create_simplified_failure_retry(source, client)

    cluster.refresh_from_db()
    assert PromptVersion.objects.filter(cluster=cluster, node_name="N9").count() == 1
    assert len(
        [
            snapshot
            for snapshot in cluster.analysis_snapshot["prompt_os"]
            if snapshot["node_id"].startswith("N7")
        ]
    ) == n7_count
    assert cluster.generations.exclude(id=source.id).get().id == first.id


def test_generation_creation_reports_only_ids_created_by_that_call(
    tmp_path,
    settings,
):
    from platform_app.models import OutputTemplate
    from platform_app.services import ensure_cluster_generations

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    slot = template.slots.get(order=1)
    approve_prompt(cluster, user, slot)

    first_items, first_created_ids = ensure_cluster_generations(
        cluster,
        user,
        include_created=True,
    )
    second_items, second_created_ids = ensure_cluster_generations(
        cluster,
        user,
        include_created=True,
    )

    assert first_created_ids == [first_items[0].id]
    assert second_created_ids == []
    assert [item.id for item in second_items] == [item.id for item in first_items]


def test_stale_completed_hero_is_not_used_after_product_revision_changes(
    tmp_path,
    settings,
):
    from platform_app.models import Cluster, Generation, OutputSlot, OutputTemplate, ResultAsset
    from platform_app.services import ensure_cluster_generations

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    hero_slot = template.slots.get(order=1)
    detail_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    approve_prompt(cluster, user, hero_slot, revision=1)
    approve_prompt(cluster, user, detail_slot, revision=1)
    old_hero = ensure_cluster_generations(cluster, user)[0]
    old_hero.status = Generation.Status.COMPLETED
    old_hero.save(update_fields=["status", "updated_at"])
    old_path = f"results/{batch.id}/{cluster.id}/{hero_slot.id}/1/old.png"
    ResultAsset.objects.create(
        generation=old_hero,
        storage_path=old_path,
        sha256="6" * 64,
        file_size=10,
    )

    Cluster.objects.filter(id=cluster.id).update(
        version=cluster.version + 1,
        preparation_status=Cluster.PreparationStatus.READY,
    )
    cluster.refresh_from_db()
    approve_prompt(cluster, user, hero_slot, revision=2)
    approve_prompt(cluster, user, detail_slot, revision=2)

    ensure_cluster_generations(cluster, user)

    newest_hero = cluster.generations.filter(output_slot=hero_slot).order_by(
        "-attempt"
    ).first()
    assert newest_hero.attempt == 2
    assert newest_hero.status == Generation.Status.QUEUED
    assert not cluster.generations.filter(output_slot=detail_slot).exists()


def test_source_passthrough_creates_new_history_after_product_revision(
    tmp_path,
    settings,
):
    from django.core.management import call_command

    from platform_app.models import Cluster
    from platform_app.services import create_project, ensure_cluster_generations, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    call_command("seed_platform_templates")
    user = make_user("source-history")
    batch = create_project(
        user,
        name="Source history",
        platform="shopee",
        market="VN",
    )
    cluster = register_uploaded_asset(
        batch,
        "source.png",
        image_bytes(),
        "image/png",
    ).clusters.get()
    slots = list(batch.output_template.slots.order_by("order"))
    for slot in slots:
        approve_prompt(cluster, user, slot, revision=1)
    ensure_cluster_generations(cluster, user)
    source_slot = next(slot for slot in slots if slot.name == "Seller original product photo")
    first_source = cluster.generations.get(output_slot=source_slot)

    Cluster.objects.filter(id=cluster.id).update(
        version=cluster.version + 1,
        preparation_status=Cluster.PreparationStatus.READY,
    )
    cluster.refresh_from_db()
    for slot in slots:
        approve_prompt(cluster, user, slot, revision=2)
    ensure_cluster_generations(cluster, user)

    source_attempts = list(
        cluster.generations.filter(output_slot=source_slot).order_by("attempt")
    )
    assert [item.attempt for item in source_attempts] == [1, 2]
    assert source_attempts[0].id == first_source.id
    assert source_attempts[1].prompt_version_id != first_source.prompt_version_id


def test_archived_product_cannot_create_generations(tmp_path, settings):
    from django.utils import timezone

    from platform_app.services import ensure_cluster_generations

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    cluster.archived_at = timezone.now()
    cluster.save(update_fields=["archived_at"])

    with pytest.raises(ValueError, match="archived"):
        ensure_cluster_generations(cluster, user)


@override_settings(USER_DAILY_GENERATION_LIMIT=1, GENERATION_QUOTAS_ENABLED=True)
def test_confirm_generation_rejects_user_quota(tmp_path, settings):
    from platform_app.services import confirm_generation

    user, batch = make_batch_with_images(tmp_path, settings, count=2)
    user.daily_generation_limit = 1
    user.save(update_fields=["daily_generation_limit"])

    with pytest.raises(ValueError, match="user daily quota"):
        confirm_generation(batch, user)


def test_worker_archives_result_and_marks_completed(tmp_path, settings):
    from platform_app.models import Generation, ResultAsset
    from platform_app.services import FakeAPIMartClient, LocalStorage, process_generation_once

    _, _, _, generation = queue_approved_hero(tmp_path, settings)

    assert process_generation_once(FakeAPIMartClient(), LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()
    assert generation.status == Generation.Status.SUBMITTED

    assert process_generation_once(FakeAPIMartClient(), LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()
    assert generation.status == Generation.Status.COMPLETED
    assert ResultAsset.objects.filter(generation=generation).count() == 1


def test_worker_enqueues_detail_slots_after_hero_completion(tmp_path, settings):
    from platform_app.models import (
        Generation,
        OutputSlot,
        OutputTemplate,
    )
    from platform_app.services import FakeAPIMartClient, LocalStorage, ensure_cluster_generations, process_generation_once

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    for order in range(2, 10):
        OutputSlot.objects.create(template=template, name=f"Slot {order}", order=order)
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    for slot in template.slots.order_by("order"):
        approve_prompt(cluster, user, slot)

    ensure_cluster_generations(cluster, user)
    assert list(cluster.generations.values_list("output_slot__order", flat=True)) == [1]

    assert process_generation_once(FakeAPIMartClient(), LocalStorage(tmp_path)) == 1
    assert process_generation_once(FakeAPIMartClient(), LocalStorage(tmp_path)) == 1

    orders = list(cluster.generations.order_by("output_slot__order").values_list("output_slot__order", flat=True))
    statuses = list(cluster.generations.order_by("output_slot__order").values_list("status", flat=True))
    assert orders == list(range(1, 10))
    assert statuses == [Generation.Status.COMPLETED, *[Generation.Status.QUEUED] * 8]


def test_shopee_vn_preserves_source_then_generates_white_hero_and_seven_marketing_images(
    tmp_path, settings
):
    from django.core.management import call_command

    from platform_app.models import Generation
    from platform_app.services import (
        FakeAPIMartClient,
        LocalStorage,
        create_project,
        ensure_cluster_generations,
        preflight_batch,
        process_generation_once,
        register_uploaded_asset,
    )

    settings.MEDIA_ROOT = tmp_path
    call_command("seed_platform_templates")
    user = make_user("vn-operator")
    batch = create_project(
        owner=user,
        name="Shopee VN",
        platform="shopee",
        market="VN",
        seller_tier="general",
    )
    source_bytes = image_bytes()
    asset = register_uploaded_asset(batch, "seller-photo.png", source_bytes, "image/png")
    cluster = asset.clusters.get()
    for slot in batch.output_template.slots.order_by("order"):
        approve_prompt(cluster, user, slot)

    assert preflight_batch(batch, user)["slot_count"] == 9
    assert preflight_batch(batch, user)["generation_count"] == 8

    initial = ensure_cluster_generations(cluster, user)

    assert [item.output_slot.order for item in initial] == [1, 2]
    source, hero = initial
    assert source.status == Generation.Status.COMPLETED
    assert source.prompt_version.provider_model == "none"
    source_result = source.result_assets.get()
    assert source_result.storage_path.startswith(f"results/{batch.id}/{cluster.id}/{source.output_slot_id}/1/")
    assert LocalStorage(tmp_path).read(source_result.storage_path) == source_bytes
    assert hero.status == Generation.Status.QUEUED

    assert process_generation_once(FakeAPIMartClient(), LocalStorage(tmp_path)) == 1
    assert process_generation_once(FakeAPIMartClient(), LocalStorage(tmp_path)) == 1

    generations = list(cluster.generations.order_by("output_slot__order"))
    assert [item.output_slot.order for item in generations] == list(range(1, 10))
    assert generations[1].status == Generation.Status.COMPLETED
    assert all(item.status == Generation.Status.QUEUED for item in generations[2:])
    white_result_path = generations[1].result_assets.get().storage_path
    assert all(white_result_path in item.reference_snapshot for item in generations[2:])


def test_submit_unknown_is_not_reposted_automatically(tmp_path, settings):
    from platform_app.models import Generation
    from platform_app.services import LocalStorage, SubmitUnknown, process_generation_once

    class UnknownClient:
        def submit_generation(self, prompt, image_paths, size, resolution):
            raise SubmitUnknown("network timeout")

    _, _, cluster, generation = queue_approved_hero(tmp_path, settings)

    assert process_generation_once(UnknownClient(), LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()
    assert generation.status == Generation.Status.SUBMIT_UNKNOWN

    assert process_generation_once(UnknownClient(), LocalStorage(tmp_path)) == 0
    assert Generation.objects.filter(cluster=cluster).count() == 1


def test_prompt_complexity_failure_creates_one_shorter_n9_retry(tmp_path, settings):
    import json

    from platform_app.models import (
        Generation,
        OutputSlot,
        OutputTemplate,
        PromptNodeTemplate,
        PromptVersion,
    )
    from platform_app.services import LocalStorage, process_generation_once

    class ComplexityClient:
        def submit_generation(self, prompt, image_paths, size, resolution):
            return "complex-task"

        def get_task(self, task_id):
            return {"status": "failed", "error_code": "prompt_complexity", "error": "prompt too complex"}

        def optimize_prompt(self, payload):
            assert "NODE N9" in payload["text"]
            return {
                "output_text": json.dumps(
                    {
                        "decision": "retry_with_simplified_prompt",
                        "simplified_prompt": "Exact.",
                        "visible_text_lines": [],
                    }
                ),
                "raw": {},
            }

    settings.MEDIA_ROOT = tmp_path
    PromptNodeTemplate.objects.create(
        node_name="N9",
        version="2.1.0",
        status=PromptNodeTemplate.Status.PUBLISHED,
        instruction="Complete failure simplifier instruction.",
    )
    _, _, cluster, generation = queue_approved_hero(
        tmp_path,
        settings,
        username="complexity-worker",
    )
    generation.status = Generation.Status.SUBMITTED
    generation.provider_task_id = "complex-task"
    generation.save(
        update_fields=["status", "provider_task_id", "updated_at"]
    )

    assert process_generation_once(ComplexityClient(), LocalStorage(tmp_path)) == 1

    generation.refresh_from_db()
    retry = Generation.objects.filter(cluster=cluster).exclude(id=generation.id).get()
    assert generation.status == Generation.Status.FAILED
    assert retry.status == Generation.Status.QUEUED
    assert retry.prompt_version.node_name == "N9"
    assert retry.prompt_version.template_version == "2.1.0"
    assert len(retry.prompt_text) < len(generation.prompt_text)


def test_worker_rejects_legacy_prompt_before_paid_submission(tmp_path, settings):
    from platform_app.models import Generation, OutputTemplate, PromptVersion
    from platform_app.services import LocalStorage, process_generation_once

    class CapturingClient:
        def __init__(self):
            self.calls = 0

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.calls += 1
            return "legacy-policy-task"

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    first_slot = template.slots.get(order=1)
    legacy_prompt = PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        prompt_text="Legacy queued prompt",
        input_snapshot={"reference_snapshot": [batch.assets.first().storage_path]},
    )
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=first_slot,
        prompt_version=legacy_prompt,
        created_by=user,
        status=Generation.Status.QUEUED,
        prompt_text=legacy_prompt.prompt_text,
        reference_snapshot=[batch.assets.first().storage_path],
    )

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()

    assert client.calls == 0
    assert generation.status == Generation.Status.FAILED
    assert generation.prompt_version_id == legacy_prompt.id
    assert "submission readiness" in generation.failure_reason


def test_generation_submission_claim_has_one_cas_winner(tmp_path, settings):
    from platform_app.models import Generation, OutputTemplate
    from platform_app.services import _claim_generation_for_submission

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=OutputTemplate.objects.get(platform="global", site="").slots.get(order=1),
        created_by=user,
        status=Generation.Status.QUEUED,
    )

    first = _claim_generation_for_submission(generation.id)
    second = _claim_generation_for_submission(generation.id)

    assert first is not None
    assert first.status == Generation.Status.SUBMITTING
    assert second is None


def test_n8_followup_has_current_n7_and_valid_parent_lineage(tmp_path, settings):
    from platform_app.models import Generation, OutputTemplate
    from platform_app.services import (
        _claim_generation_for_submission,
        _validate_generation_submission,
        ensure_cluster_generations,
        request_generation_revision,
    )

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    slot = template.slots.get(order=1)
    approve_prompt(cluster, user, slot)
    source = ensure_cluster_generations(cluster, user)[0]
    source.status = Generation.Status.COMPLETED
    source.save(update_fields=["status", "updated_at"])

    revision = request_generation_revision(
        source,
        user,
        issue_tags=["composition"],
        description="Move the product slightly left.",
        annotations=[],
    )
    claimed = _claim_generation_for_submission(revision.id)

    assert claimed.prompt_version.node_name == "N8"
    assert (
        claimed.prompt_version.source_snapshot["parent_prompt_version_id"]
        == str(source.prompt_version_id)
    )
    assert _validate_generation_submission(claimed) == claimed


def test_n9_retry_has_current_n7_and_valid_parent_lineage(tmp_path, settings):
    import json

    from platform_app.models import Generation, OutputTemplate
    from platform_app.services import (
        LocalStorage,
        _claim_generation_for_submission,
        _validate_generation_submission,
        ensure_cluster_generations,
        process_generation_once,
    )

    class ComplexityClient:
        def get_task(self, task_id):
            return {
                "status": "failed",
                "error_code": "prompt_complexity",
                "error": "prompt too complex",
            }

        def optimize_prompt(self, payload):
            assert "NODE N9" in payload["text"]
            return {
                "output_text": json.dumps(
                        {
                            "decision": "retry_with_simplified_prompt",
                            "simplified_prompt": "Exact.",
                            "visible_text_lines": [],
                        }
                )
            }

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    slot = template.slots.get(order=1)
    approve_prompt(cluster, user, slot)
    source = ensure_cluster_generations(cluster, user)[0]
    source.status = Generation.Status.SUBMITTED
    source.provider_task_id = "complexity"
    source.save(
        update_fields=["status", "provider_task_id", "updated_at"]
    )

    assert process_generation_once(ComplexityClient(), LocalStorage(tmp_path)) == 1

    retry = cluster.generations.exclude(id=source.id).get()
    claimed = _claim_generation_for_submission(retry.id)
    assert claimed.prompt_version.node_name == "N9"
    assert (
        claimed.prompt_version.source_snapshot["parent_prompt_version_id"]
        == str(source.prompt_version_id)
    )
    assert _validate_generation_submission(claimed) == claimed


def test_regenerate_rejects_legacy_prompt_without_rewriting_history(tmp_path, settings):
    from platform_app.models import Generation, OutputTemplate, PromptVersion
    from platform_app.services import regenerate_generation

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    slot = OutputTemplate.objects.get(platform="global", site="").slots.get(order=1)
    prompt = PromptVersion.objects.create(
        cluster=cluster,
        output_slot=slot,
        created_by=user,
        prompt_text="Legacy prompt",
    )
    source = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        prompt_version=prompt,
        created_by=user,
        status=Generation.Status.FAILED,
        prompt_text=prompt.prompt_text,
    )

    with pytest.raises(ValueError, match="readiness"):
        regenerate_generation(source, user)

    source.refresh_from_db()
    assert source.prompt_version_id == prompt.id
    assert source.prompt_text == "Legacy prompt"
    assert cluster.generations.count() == 1


def test_followup_creation_locks_batch_cluster_generation_in_order(
    tmp_path,
    settings,
    monkeypatch,
):
    from platform_app.models import Batch, Cluster, Generation, OutputTemplate
    from platform_app.services import ensure_cluster_generations, regenerate_generation

    user, batch = make_batch_with_images(tmp_path, settings)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    slot = template.slots.get(order=1)
    approve_prompt(cluster, user, slot)
    source = ensure_cluster_generations(cluster, user)[0]
    source.status = Generation.Status.FAILED
    source.save(update_fields=["status", "updated_at"])

    events = []
    batch_lock = Batch.objects.select_for_update
    cluster_lock = Cluster.objects.select_for_update
    generation_lock = Generation.objects.select_for_update

    def track_batch(*args, **kwargs):
        events.append("batch")
        return batch_lock(*args, **kwargs)

    def track_cluster(*args, **kwargs):
        events.append("cluster")
        return cluster_lock(*args, **kwargs)

    def track_generation(*args, **kwargs):
        events.append("generation")
        return generation_lock(*args, **kwargs)

    monkeypatch.setattr(Batch.objects, "select_for_update", track_batch)
    monkeypatch.setattr(Cluster.objects, "select_for_update", track_cluster)
    monkeypatch.setattr(Generation.objects, "select_for_update", track_generation)

    followup = regenerate_generation(source, user)

    assert followup.attempt == 2
    assert events[:3] == ["batch", "cluster", "generation"]


def test_worker_rejects_generation_whose_prompt_differs_from_prompt_version(tmp_path, settings):
    from platform_app.models import Generation, OutputTemplate, PromptVersion
    from platform_app.services import LocalStorage, process_generation_once
    from platform_app.template_policy import STANDARD_PRODUCT_HERO_PROMPT_LINES

    class CapturingClient:
        def __init__(self):
            self.prompt = ""

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.prompt = prompt
            return "canonical-policy-task"

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    first_slot = OutputTemplate.objects.get(platform="global", site="").slots.get(order=1)
    canonical_prompt = "\n".join(("Canonical product prompt", *STANDARD_PRODUCT_HERO_PROMPT_LINES))
    prompt_version = PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        prompt_text=canonical_prompt,
        input_snapshot={"standard_product_hero": True},
    )
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=first_slot,
        prompt_version=prompt_version,
        created_by=user,
        status=Generation.Status.QUEUED,
        prompt_text="Lifestyle campaign with a sale headline",
    )

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()

    assert client.prompt == ""
    assert generation.status == Generation.Status.FAILED
    assert generation.prompt_text == "Lifestyle campaign with a sale headline"
    assert generation.prompt_version_id == prompt_version.id


def test_generation_cannot_be_reassigned_away_from_its_hero_before_submission(tmp_path, settings):
    from django.core.exceptions import ValidationError

    from platform_app.models import Generation, OutputSlot, OutputTemplate, PromptVersion
    from platform_app.services import LocalStorage, process_generation_once
    from platform_app.template_policy import STANDARD_PRODUCT_HERO_PROMPT_LINES

    class CapturingClient:
        def __init__(self):
            self.prompt = ""

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.prompt = prompt
            return "locked-hero-task"

    user, batch, cluster, generation = queue_approved_hero(
        tmp_path,
        settings,
        username="immutable-generation",
    )
    template = OutputTemplate.objects.get(platform="global", site="")
    hero_slot = generation.output_slot
    detail_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    canonical_prompt = generation.prompt_text
    hero_prompt = generation.prompt_version
    detail_prompt = PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        prompt_text="Lifestyle campaign with a sale headline",
    )

    generation.output_slot = detail_slot
    with pytest.raises(ValidationError, match="immutable"):
        generation.save()

    generation.refresh_from_db()
    generation.prompt_version = detail_prompt
    with pytest.raises(ValidationError, match="immutable"):
        generation.save()

    with pytest.raises(ValidationError, match="immutable"):
        Generation.objects.filter(id=generation.id).update(output_slot=detail_slot)

    with pytest.raises(ValidationError, match="immutable"):
        Generation.objects.filter(id=generation.id).update(prompt_version=detail_prompt)

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()

    assert client.prompt == canonical_prompt
    assert generation.output_slot_id == hero_slot.id
    assert generation.prompt_version_id == hero_prompt.id


def test_used_prompt_version_cannot_be_mutated_before_hero_submission(tmp_path, settings):
    from django.core.exceptions import ValidationError

    from platform_app.models import Generation, OutputTemplate, PromptVersion
    from platform_app.services import LocalStorage, process_generation_once
    from platform_app.template_policy import STANDARD_PRODUCT_HERO_PROMPT_LINES

    class CapturingClient:
        def __init__(self):
            self.prompt = ""

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.prompt = prompt
            return "immutable-prompt-task"

    user, batch, cluster, generation = queue_approved_hero(
        tmp_path,
        settings,
        username="immutable-prompt",
    )
    canonical_prompt = generation.prompt_text
    prompt_version = generation.prompt_version
    unused_prompt = PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        prompt_text="Editable draft",
    )

    with pytest.raises(ValidationError, match="immutable"):
        PromptVersion.objects.filter(id=prompt_version.id).update(prompt_text="Lifestyle campaign")
    with pytest.raises(ValidationError, match="immutable"):
        PromptVersion.objects.filter(id=prompt_version.id).update(input_snapshot={"sale": True})

    PromptVersion.objects.filter(id=unused_prompt.id).update(prompt_text="Editable revision")
    unused_prompt.refresh_from_db()
    assert unused_prompt.prompt_text == "Editable revision"

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()

    assert client.prompt == canonical_prompt
    assert generation.prompt_version_id == prompt_version.id


def test_worker_rejects_a_queued_detail_image_without_its_required_hero(tmp_path, settings):
    from platform_app.models import Generation, OutputSlot, OutputTemplate
    from platform_app.services import LocalStorage, process_generation_once

    class CapturingClient:
        def __init__(self):
            self.calls = 0

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.calls += 1
            return "unexpected-detail-task"

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    detail_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=detail_slot,
        created_by=user,
        status=Generation.Status.QUEUED,
        prompt_text="A direct detail image",
    )

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()

    assert client.calls == 0
    assert generation.status == Generation.Status.FAILED
    assert "standard product hero" in generation.failure_reason.lower()


def test_worker_requires_a_completed_hero_from_the_same_template_before_detail_submission(tmp_path, settings):
    from platform_app.models import Generation, OutputSlot, OutputTemplate, ResultAsset
    from platform_app.services import (
        LocalStorage,
        ensure_cluster_generations,
        process_generation_once,
    )

    class CapturingClient:
        def __init__(self):
            self.calls = 0

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.calls += 1
            return f"detail-task-{self.calls}"

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    hero_slot = template.slots.get(order=1)
    detail_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    approve_prompt(cluster, user, hero_slot)
    approve_prompt(cluster, user, detail_slot)
    other_template = OutputTemplate.objects.create(platform="global", name="Other template")
    other_hero_slot = OutputSlot.objects.create(template=other_template, name="Hero", order=1)
    client = CapturingClient()

    Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=other_hero_slot,
        created_by=user,
        status=Generation.Status.COMPLETED,
    )
    wrong_template_detail = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=detail_slot,
        created_by=user,
        status=Generation.Status.QUEUED,
        prompt_text="Detail after wrong-template hero",
    )
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    wrong_template_detail.refresh_from_db()
    assert client.calls == 0
    assert wrong_template_detail.status == Generation.Status.FAILED

    hero = next(
        item
        for item in ensure_cluster_generations(cluster, user)
        if item.output_slot_id == hero_slot.id
    )
    hero.status = Generation.Status.COMPLETED
    hero.save(update_fields=["status", "updated_at"])
    hero_path = f"results/{batch.id}/{cluster.id}/{hero_slot.id}/{hero.attempt}/hero.png"
    (tmp_path / hero_path).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / hero_path).write_bytes(image_bytes())
    ResultAsset.objects.create(
        generation=hero,
        storage_path=hero_path,
        sha256="7" * 64,
        file_size=len(image_bytes()),
    )
    completed_hero_detail = next(
        item
        for item in ensure_cluster_generations(cluster, user)
        if item.output_slot_id == detail_slot.id
        and item.status == Generation.Status.QUEUED
    )
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    completed_hero_detail.refresh_from_db()
    assert client.calls == 1
    assert (
        completed_hero_detail.status,
        completed_hero_detail.failure_reason,
    ) == (Generation.Status.SUBMITTED, "")


@pytest.mark.parametrize("hero_status", ["failed", "canceled"])
def test_worker_keeps_detail_queued_until_a_failed_or_canceled_hero_is_redone(tmp_path, settings, hero_status):
    from platform_app.models import Generation, OutputSlot, OutputTemplate, ResultAsset
    from platform_app.services import (
        LocalStorage,
        ensure_cluster_generations,
        process_generation_once,
    )

    class CapturingClient:
        def __init__(self):
            self.calls = 0

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.calls += 1
            return f"detail-task-{self.calls}"

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    hero_slot = template.slots.get(order=1)
    detail_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    approve_prompt(cluster, user, hero_slot)
    approve_prompt(cluster, user, detail_slot)
    client = CapturingClient()
    hero = ensure_cluster_generations(cluster, user)[0]
    hero.status = hero_status
    hero.save(update_fields=["status", "updated_at"])
    assert not cluster.generations.filter(output_slot=detail_slot).exists()

    retry = hero.retry_failed(user)
    assert retry.attempt == 2
    assert retry.status == Generation.Status.QUEUED
    retry.status = Generation.Status.COMPLETED
    retry.save(update_fields=["status", "updated_at"])
    hero_path = f"results/{batch.id}/{cluster.id}/{hero_slot.id}/{retry.attempt}/hero.png"
    (tmp_path / hero_path).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / hero_path).write_bytes(image_bytes())
    ResultAsset.objects.create(
        generation=retry,
        storage_path=hero_path,
        sha256="8" * 64,
        file_size=len(image_bytes()),
    )
    detail = next(
        item
        for item in ensure_cluster_generations(cluster, user)
        if item.output_slot_id == detail_slot.id
    )
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    detail.refresh_from_db()
    assert client.calls == 1
    assert detail.status == Generation.Status.SUBMITTED


def test_worker_defers_detail_until_hero_completes_without_blocking_hero_or_polling(tmp_path, settings):
    from platform_app.models import Generation, OutputSlot, OutputTemplate
    from platform_app.services import (
        LocalStorage,
        ensure_cluster_generations,
        process_generation_once,
    )

    class CapturingClient:
        def __init__(self):
            self.submitted_prompts = []
            self.polls = 0

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.submitted_prompts.append(prompt)
            return "hero-task"

        def get_task(self, task_id):
            self.polls += 1
            return {"status": "processing"}

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    hero_slot = template.slots.get(order=1)
    detail_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    approve_prompt(cluster, user, hero_slot)
    approve_prompt(cluster, user, detail_slot)
    hero = ensure_cluster_generations(cluster, user)[0]

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    hero.refresh_from_db()
    assert hero.status == Generation.Status.SUBMITTED
    assert client.submitted_prompts == [hero.prompt_text]
    assert not cluster.generations.filter(output_slot=detail_slot).exists()

    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    hero.refresh_from_db()
    assert hero.status == Generation.Status.PROCESSING
    assert client.polls == 1
    assert not cluster.generations.filter(output_slot=detail_slot).exists()
