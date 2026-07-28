from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image


pytestmark = pytest.mark.django_db


def make_user(username="operator"):
    return get_user_model().objects.create_user(
        username=username,
        password="long-enough-password",
        must_change_password=False,
    )


def image_file(name="product.png"):
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def test_batch_detail_page_renders_workspace(client):
    from platform_app.models import Batch

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Workspace batch")
    client.force_login(user)

    response = client.get(reverse("batch_detail", args=[batch.id]))

    assert response.status_code == 200
    assert b"Workspace batch" in response.content
    assert b"Upload source files" in response.content


def test_upload_api_creates_assets_and_default_clusters(client, tmp_path, settings):
    from platform_app.models import Batch

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = Batch.objects.create(owner=user, name="Batch 1")
    client.force_login(user)

    response = client.post(
        reverse("api_upload_assets", args=[batch.id]),
        {"files": [image_file("a.png"), image_file("b.png")]},
    )

    assert response.status_code == 200
    assert response.json()["asset_count"] == 2
    assert batch.clusters.count() == 2


def test_snapshot_includes_cluster_and_generation_state(client, tmp_path, settings):
    from platform_app.services import confirm_generation, create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = create_batch(user, "Batch 1")
    register_uploaded_asset(batch, "a.png", image_file("a.png").read(), "image/png")
    confirm_generation(batch, user)
    client.force_login(user)

    response = client.get(reverse("api_batch_snapshot", args=[batch.id]))

    assert response.status_code == 200
    body = response.json()
    assert body["batch"]["name"] == "Batch 1"
    assert body["clusters"][0]["generations"][0]["status"] == "queued"


def test_retry_endpoint_creates_new_attempt_for_failed_generation(client, tmp_path, settings):
    from platform_app.models import Generation
    from platform_app.services import confirm_generation, create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = create_batch(user, "Batch 1")
    register_uploaded_asset(batch, "a.png", image_file("a.png").read(), "image/png")
    generation = confirm_generation(batch, user)[0]
    generation.status = Generation.Status.FAILED
    generation.save(update_fields=["status"])
    client.force_login(user)

    response = client.post(reverse("api_generation_retry", args=[generation.id]))

    assert response.status_code == 200
    assert response.json()["attempt"] == 2
    assert Generation.objects.filter(cluster=generation.cluster, output_slot=generation.output_slot).count() == 2


def test_update_cluster_prompt_requires_current_version(client, tmp_path, settings):
    from platform_app.services import create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = create_batch(user, "Batch 1")
    asset = register_uploaded_asset(batch, "a.png", image_file("a.png").read(), "image/png")
    cluster = asset.clusters.get()
    client.force_login(user)

    stale = client.post(
        reverse("api_update_cluster", args=[cluster.id]),
        {"expected_version": cluster.version - 1, "prompt_override": "new prompt"},
        content_type="application/json",
    )
    assert stale.status_code == 409

    response = client.post(
        reverse("api_update_cluster", args=[cluster.id]),
        {"expected_version": cluster.version, "prompt_override": "new prompt"},
        content_type="application/json",
    )

    assert response.status_code == 200
    cluster.refresh_from_db()
    assert cluster.prompt_override == "new prompt"
    assert cluster.version == 2
