from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from PIL import Image


pytestmark = pytest.mark.django_db


def make_user():
    return get_user_model().objects.create_user(
        username="operator",
        password="long-enough-password",
    )


def image_bytes(fmt="PNG"):
    buffer = BytesIO()
    Image.new("RGB", (8, 6), "white").save(buffer, fmt)
    return buffer.getvalue()


@override_settings(MEDIA_ROOT=None)
def test_register_png_creates_asset_and_default_cluster(tmp_path, settings):
    from platform_app.services import create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    batch = create_batch(make_user(), "Batch 1")

    asset = register_uploaded_asset(batch, "product.png", image_bytes(), "image/png")

    assert asset.kind == "image"
    assert asset.width == 8
    assert asset.height == 6
    assert batch.clusters.count() == 1
    assert batch.clusters.first().assets.get() == asset


def test_register_txt_keeps_utf8_text_without_cluster(tmp_path, settings):
    from platform_app.services import create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    batch = create_batch(make_user(), "Batch 1")

    asset = register_uploaded_asset(batch, "brief.txt", "hello".encode(), "text/plain")

    assert asset.kind == "txt"
    assert asset.text_content == "hello"
    assert batch.clusters.count() == 0


def test_invalid_upload_is_rejected(tmp_path, settings):
    from platform_app.services import create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    batch = create_batch(make_user(), "Batch 1")

    with pytest.raises(ValueError, match="JPEG、PNG、WebP"):
        register_uploaded_asset(batch, "bad.exe", b"not an image", "application/octet-stream")


def test_merge_asset_into_cluster_moves_from_default_cluster(tmp_path, settings):
    from platform_app.services import create_batch, merge_asset_into_cluster, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    batch = create_batch(make_user(), "Batch 1")
    first = register_uploaded_asset(batch, "a.png", image_bytes(), "image/png")
    second = register_uploaded_asset(batch, "b.png", image_bytes(), "image/png")
    target = first.clusters.get()

    merge_asset_into_cluster(second, target, expected_version=target.version)

    assert target.assets.count() == 2
    assert batch.clusters.count() == 1


def test_move_asset_to_new_cluster_splits_reference(tmp_path, settings):
    from platform_app.services import (
        create_batch,
        merge_asset_into_cluster,
        move_asset_to_new_cluster,
        register_uploaded_asset,
    )

    settings.MEDIA_ROOT = tmp_path
    batch = create_batch(make_user(), "Batch 1")
    first = register_uploaded_asset(batch, "a.png", image_bytes(), "image/png")
    second = register_uploaded_asset(batch, "b.png", image_bytes(), "image/png")
    target = first.clusters.get()
    merge_asset_into_cluster(second, target, expected_version=target.version)

    new_cluster = move_asset_to_new_cluster(second)

    assert target.assets.count() == 1
    assert new_cluster.assets.get() == second
    assert batch.clusters.count() == 2


def test_cluster_version_conflict_is_rejected(tmp_path, settings):
    from platform_app.services import create_batch, merge_asset_into_cluster, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    batch = create_batch(make_user(), "Batch 1")
    first = register_uploaded_asset(batch, "a.png", image_bytes(), "image/png")
    second = register_uploaded_asset(batch, "b.png", image_bytes(), "image/png")
    target = first.clusters.get()

    with pytest.raises(ValueError, match="changed"):
        merge_asset_into_cluster(second, target, expected_version=target.version - 1)


def test_remove_asset_deletes_unused_reference_and_promotes_remaining_image(tmp_path, settings):
    from platform_app.models import Asset
    from platform_app.services import (
        create_batch,
        merge_asset_into_cluster,
        register_uploaded_asset,
        remove_asset_from_cluster,
    )

    settings.MEDIA_ROOT = tmp_path
    batch = create_batch(make_user(), "Batch 1")
    first = register_uploaded_asset(batch, "a.png", image_bytes(), "image/png")
    second = register_uploaded_asset(batch, "b.png", image_bytes(), "image/png")
    target = first.clusters.get()
    merge_asset_into_cluster(second, target, expected_version=target.version)

    result = remove_asset_from_cluster(first)

    assert result == "deleted"
    assert not Asset.objects.filter(id=first.id).exists()
    assert list(target.cluster_assets.values_list("asset_id", "role", "order")) == [
        (second.id, "primary", 1)
    ]


def test_remove_last_unused_asset_deletes_the_product(tmp_path, settings):
    from platform_app.models import Asset, Cluster
    from platform_app.services import create_batch, register_uploaded_asset, remove_asset_from_cluster

    settings.MEDIA_ROOT = tmp_path
    batch = create_batch(make_user(), "Batch 1")
    asset = register_uploaded_asset(batch, "a.png", image_bytes(), "image/png")
    cluster_id = asset.clusters.get().id

    result = remove_asset_from_cluster(asset)

    assert result == "deleted"
    assert not Asset.objects.filter(id=asset.id).exists()
    assert not Cluster.objects.filter(id=cluster_id).exists()


def test_product_with_generation_history_is_archived_instead_of_deleted(tmp_path, settings):
    from platform_app.models import Generation, OutputSlot, OutputTemplate
    from platform_app.services import archive_or_delete_cluster, create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = create_batch(user, "Batch 1")
    asset = register_uploaded_asset(batch, "a.png", image_bytes(), "image/png")
    cluster = asset.clusters.get()
    template = OutputTemplate.objects.create(
        platform="global",
        site="",
        name="Archive template",
        version="v1",
        status=OutputTemplate.Status.PUBLISHED,
    )
    slot = OutputSlot.objects.create(template=template, order=1, name="Hero", purpose="Hero")
    Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        created_by=user,
        status=Generation.Status.COMPLETED,
    )

    result = archive_or_delete_cluster(cluster)

    cluster.refresh_from_db()
    asset.refresh_from_db()
    assert result == "archived"
    assert cluster.archived_at is not None
    assert asset.archived_at is not None


def test_active_generation_blocks_product_removal(tmp_path, settings):
    from platform_app.models import Generation, OutputSlot, OutputTemplate
    from platform_app.services import archive_or_delete_cluster, create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = create_batch(user, "Batch 1")
    asset = register_uploaded_asset(batch, "a.png", image_bytes(), "image/png")
    cluster = asset.clusters.get()
    template = OutputTemplate.objects.create(
        platform="global",
        site="",
        name="Active template",
        version="v1",
        status=OutputTemplate.Status.PUBLISHED,
    )
    slot = OutputSlot.objects.create(template=template, order=1, name="Hero", purpose="Hero")
    Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        created_by=user,
        status=Generation.Status.PROCESSING,
    )

    with pytest.raises(ValueError, match="active generation"):
        archive_or_delete_cluster(cluster)


def test_archived_product_cannot_be_requeued_for_preparation(tmp_path, settings):
    from django.utils import timezone

    from platform_app.services import create_batch, register_uploaded_asset, request_cluster_preparation

    settings.MEDIA_ROOT = tmp_path
    batch = create_batch(make_user(), "Batch 1")
    asset = register_uploaded_asset(batch, "a.png", image_bytes(), "image/png")
    cluster = asset.clusters.get()
    cluster.archived_at = timezone.now()
    cluster.save(update_fields=["archived_at"])

    with pytest.raises(ValueError, match="archived"):
        request_cluster_preparation(cluster, auto_generate=True)


def test_product_being_prepared_cannot_be_removed(tmp_path, settings):
    from platform_app.models import Cluster
    from platform_app.services import archive_or_delete_cluster, create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    batch = create_batch(make_user(), "Batch 1")
    asset = register_uploaded_asset(batch, "a.png", image_bytes(), "image/png")
    cluster = asset.clusters.get()
    cluster.preparation_status = Cluster.PreparationStatus.PREPARING
    cluster.save(update_fields=["preparation_status"])

    with pytest.raises(ValueError, match="being prepared"):
        archive_or_delete_cluster(cluster)


def test_product_being_prepared_cannot_change_reference_images(tmp_path, settings):
    from platform_app.models import Cluster
    from platform_app.services import (
        create_batch,
        merge_asset_into_cluster,
        move_asset_to_new_cluster,
        register_uploaded_asset,
        remove_asset_from_cluster,
        update_cluster_content,
    )

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = create_batch(user, "Batch 1")
    first = register_uploaded_asset(batch, "a.png", image_bytes(), "image/png")
    second = register_uploaded_asset(batch, "b.png", image_bytes(), "image/png")
    cluster = first.clusters.get()
    cluster.preparation_status = Cluster.PreparationStatus.PREPARING
    cluster.save(update_fields=["preparation_status"])

    with pytest.raises(ValueError, match="being prepared"):
        merge_asset_into_cluster(second, cluster)
    with pytest.raises(ValueError, match="being prepared"):
        remove_asset_from_cluster(first)
    with pytest.raises(ValueError, match="being prepared"):
        move_asset_to_new_cluster(first)
    with pytest.raises(ValueError, match="being prepared"):
        update_cluster_content(cluster, user, {"expected_version": cluster.version, "name": "Changed"})
