import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError


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
    assert retry.prompt_text == failed.prompt_text
    assert Generation.objects.filter(cluster=cluster, output_slot=slot).count() == 2


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
