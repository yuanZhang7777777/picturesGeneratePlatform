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
