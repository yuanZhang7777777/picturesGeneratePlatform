import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction


pytestmark = pytest.mark.django_db


def make_user(username="operator"):
    return get_user_model().objects.create_user(
        username=username,
        password="long-enough-password",
    )


def test_image_asset_can_create_default_cluster():
    from platform_app.models import Asset, Batch, Cluster

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Batch 1")
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="product.jpg",
        storage_path="originals/product.jpg",
        sha256="a" * 64,
        file_size=10,
        content_type="image/jpeg",
    )

    cluster = Cluster.create_for_asset(batch=batch, asset=asset)

    assert cluster.batch == batch
    assert cluster.assets.count() == 1
    assert cluster.cluster_assets.get(asset=asset).role == "primary"


def test_cluster_rejects_more_than_sixteen_reference_images():
    from platform_app.models import Asset, Batch, Cluster

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Batch 1")
    first = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="0.jpg",
        storage_path="originals/0.jpg",
        sha256="0" * 64,
        file_size=10,
        content_type="image/jpeg",
    )
    cluster = Cluster.create_for_asset(batch=batch, asset=first)

    for index in range(1, 16):
        asset = Asset.objects.create(
            batch=batch,
            kind=Asset.Kind.IMAGE,
            original_filename=f"{index}.jpg",
            storage_path=f"originals/{index}.jpg",
            sha256=f"{index:x}".rjust(64, "0"),
            file_size=10,
            content_type="image/jpeg",
        )
        cluster.add_asset(asset)

    overflow = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="overflow.jpg",
        storage_path="originals/overflow.jpg",
        sha256="f" * 64,
        file_size=10,
        content_type="image/jpeg",
    )

    with pytest.raises(ValueError, match="16"):
        cluster.add_asset(overflow)


def test_generation_attempt_is_unique_per_cluster_and_slot():
    from platform_app.models import Batch, Cluster, Generation, OutputSlot, OutputTemplate

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Batch 1")
    cluster = Cluster.objects.create(batch=batch, name="Product 1")
    template = OutputTemplate.objects.create(name="Shopee main", platform="shopee")
    slot = OutputSlot.objects.create(template=template, name="main", order=1)

    Generation.objects.create(batch=batch, cluster=cluster, output_slot=slot, attempt=1)

    with pytest.raises(IntegrityError):
        Generation.objects.create(batch=batch, cluster=cluster, output_slot=slot, attempt=1)


def test_output_slot_order_must_start_at_one():
    from platform_app.models import OutputSlot, OutputTemplate

    template = OutputTemplate.objects.create(name="Invalid slots", platform="global")

    with pytest.raises(IntegrityError), transaction.atomic():
        OutputSlot.objects.create(template=template, name="Before hero", order=0)


def test_output_slot_position_and_template_are_immutable_after_generation_uses_it():
    from django.contrib import admin

    from platform_app.models import Batch, Cluster, Generation, OutputSlot, OutputTemplate

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Used slot")
    cluster = Cluster.objects.create(batch=batch, name="Product 1")
    template = OutputTemplate.objects.create(name="Original", platform="global")
    replacement = OutputTemplate.objects.create(name="Replacement", platform="global")
    slot = OutputSlot.objects.create(template=template, name="Hero", order=1)
    slot.template = replacement
    slot.order = 2
    slot.save()
    slot.template = template
    slot.order = 1
    slot.save()
    Generation.objects.create(batch=batch, cluster=cluster, output_slot=slot)

    slot.order = 2
    with pytest.raises(ValidationError, match="immutable"):
        slot.save()

    slot.refresh_from_db()
    slot.template = replacement
    with pytest.raises(ValidationError, match="immutable"):
        slot.save()

    readonly_fields = admin.site._registry[OutputSlot].get_readonly_fields(None, slot)
    assert {"template", "order"} <= set(readonly_fields)


def test_generation_slot_and_prompt_version_are_readonly_in_admin_after_creation():
    from django.contrib import admin

    from platform_app.models import Batch, Cluster, Generation, OutputSlot, OutputTemplate

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Generation admin")
    cluster = Cluster.objects.create(batch=batch, name="Product 1")
    template = OutputTemplate.objects.create(name="Template", platform="global")
    slot = OutputSlot.objects.create(template=template, name="Hero", order=1)
    generation = Generation.objects.create(batch=batch, cluster=cluster, output_slot=slot)

    readonly_fields = admin.site._registry[Generation].get_readonly_fields(None, generation)
    assert {
        "batch",
        "cluster",
        "output_slot",
        "prompt_version",
        "created_by",
        "attempt",
        "prompt_text",
        "size",
        "resolution",
        "reference_snapshot",
        "template_snapshot",
        "rule_snapshot",
    } <= set(readonly_fields)


def test_generation_identity_and_snapshot_cannot_be_displaced_after_creation():
    from platform_app.models import Batch, Cluster, Generation, OutputSlot, OutputTemplate

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Original batch")
    other_batch = Batch.objects.create(owner=user, name="Other batch")
    cluster = Cluster.objects.create(batch=batch, name="Original SKU")
    other_cluster = Cluster.objects.create(batch=other_batch, name="Other SKU")
    template = OutputTemplate.objects.create(name="Template", platform="global")
    slot = OutputSlot.objects.create(template=template, name="Hero", order=1)
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        prompt_text="Original prompt",
        reference_snapshot=["original.png"],
    )

    generation.cluster = other_cluster
    with pytest.raises(ValidationError, match="immutable"):
        generation.save()

    generation.refresh_from_db()
    generation.batch = other_batch
    with pytest.raises(ValidationError, match="immutable"):
        generation.save()

    with pytest.raises(ValidationError, match="immutable"):
        Generation.objects.filter(id=generation.id).update(attempt=2)


def test_generation_is_undeletable_after_it_is_queued():
    from django.contrib import admin

    from platform_app.models import Batch, Cluster, Generation, OutputSlot, OutputTemplate

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Queued hero")
    cluster = Cluster.objects.create(batch=batch, name="SKU 1")
    template = OutputTemplate.objects.create(name="Template", platform="global")
    slot = OutputSlot.objects.create(template=template, name="Hero", order=1)
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        status=Generation.Status.QUEUED,
    )

    with pytest.raises(ValidationError, match="undeletable"):
        generation.delete()
    with pytest.raises(ValidationError, match="undeletable"):
        Generation.objects.filter(id=generation.id).delete()

    assert not admin.site._registry[Generation].has_delete_permission(None, generation)
    assert Generation.objects.filter(id=generation.id).exists()


def test_generated_batch_and_cluster_cannot_be_deleted_but_unused_drafts_can():
    from platform_app.models import Batch, Cluster, Generation, OutputSlot, OutputTemplate

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Generated project")
    cluster = Cluster.objects.create(batch=batch, name="Generated SKU")
    template = OutputTemplate.objects.create(name="Template", platform="global")
    slot = OutputSlot.objects.create(template=template, name="Hero", order=1)
    Generation.objects.create(batch=batch, cluster=cluster, output_slot=slot)

    with pytest.raises(ValidationError, match="archive"):
        batch.delete()
    with pytest.raises(ValidationError, match="archive"):
        Batch.objects.filter(id=batch.id).delete()
    with pytest.raises(ValidationError, match="archive"):
        cluster.delete()
    with pytest.raises(ValidationError, match="archive"):
        Cluster.objects.filter(id=cluster.id).delete()

    draft = Batch.objects.create(owner=user, name="Unused draft")
    draft_cluster = Cluster.objects.create(batch=draft, name="Unused SKU")
    draft_cluster.delete()
    draft.delete()
    assert not Batch.objects.filter(id=draft.id).exists()


def test_retry_failed_generation_preserves_old_attempt():
    from platform_app.models import Batch, Cluster, Generation, OutputSlot, OutputTemplate

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Batch 1")
    cluster = Cluster.objects.create(batch=batch, name="Product 1")
    template = OutputTemplate.objects.create(name="Shopee main", platform="shopee")
    slot = OutputSlot.objects.create(template=template, name="main", order=1)
    failed = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        attempt=1,
        status=Generation.Status.FAILED,
        prompt_text="make product image",
    )

    retry = failed.retry_failed(user)

    assert retry.attempt == 2
    assert retry.status == Generation.Status.QUEUED
    assert failed.prompt_text == "make product image"
    assert "Hero restrictions: no promotional text" in retry.prompt_text
    assert retry.prompt_version.prompt_text == retry.prompt_text
    assert Generation.objects.filter(cluster=cluster, output_slot=slot).count() == 2


def test_retry_of_legacy_first_slot_rewrites_the_new_attempt_prompt_without_mutating_history():
    from platform_app.models import Batch, Cluster, Generation, OutputSlot, OutputTemplate, PromptVersion

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Legacy retry")
    cluster = Cluster.objects.create(batch=batch, name="Product 1")
    template = OutputTemplate.objects.create(name="Shopee main", platform="shopee")
    slot = OutputSlot.objects.create(template=template, name="main", order=1)
    legacy_prompt = PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        prompt_text="Legacy product prompt",
        input_snapshot={"reference_snapshot": []},
    )
    failed = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        prompt_version=legacy_prompt,
        created_by=user,
        attempt=1,
        status=Generation.Status.FAILED,
        prompt_text=legacy_prompt.prompt_text,
    )

    retry = failed.retry_failed(user)

    assert failed.prompt_text == "Legacy product prompt"
    assert failed.prompt_version_id == legacy_prompt.id
    assert retry.prompt_version_id != legacy_prompt.id
    assert "Hero restrictions: no promotional text" in retry.prompt_text
    assert retry.prompt_version.prompt_text == retry.prompt_text
    assert retry.prompt_version.input_snapshot["standard_product_hero"] is True


def test_retry_non_failed_generation_is_rejected():
    from platform_app.models import Batch, Cluster, Generation, OutputSlot, OutputTemplate

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Batch 1")
    cluster = Cluster.objects.create(batch=batch, name="Product 1")
    template = OutputTemplate.objects.create(name="Shopee main", platform="shopee")
    slot = OutputSlot.objects.create(template=template, name="main", order=1)
    completed = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        attempt=1,
        status=Generation.Status.COMPLETED,
    )

    with pytest.raises(ValueError, match="failed"):
        completed.retry_failed(user)
