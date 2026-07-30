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


def make_global_baseline():
    from platform_app.models import OutputSlot, OutputTemplate

    template = OutputTemplate.objects.create(platform="global", site="", name="Test global baseline")
    OutputSlot.objects.create(template=template, name="main", order=1)


def test_legacy_batch_detail_redirects_to_react_workspace(client):
    from platform_app.models import Batch

    user = make_user()
    batch = Batch.objects.create(owner=user, name="Workspace batch")
    client.force_login(user)

    response = client.get(f"/batches/{batch.id}/")

    assert response.status_code == 302
    assert response["Location"] == f"/projects/{batch.id}"


def test_legacy_batch_list_and_new_redirect_to_react(client):
    user = make_user()
    client.force_login(user)

    list_response = client.get("/batches/")
    new_response = client.get("/batches/new/")

    assert list_response.status_code == 302
    assert list_response["Location"] == "/"
    assert new_response.status_code == 302
    assert new_response["Location"] == "/projects/new"


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


def test_upload_api_records_import_mode_and_preparation_request(client, tmp_path, settings):
    from platform_app.models import Batch

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = Batch.objects.create(owner=user, name="Batch 1")
    client.force_login(user)

    response = client.post(
        reverse("api_upload_assets", args=[batch.id]),
        {"mode": "auto", "files": [image_file("a.png")]},
    )

    assert response.status_code == 200
    batch.refresh_from_db()
    cluster = batch.clusters.get()
    assert batch.last_import_mode == "auto"
    assert cluster.auto_generate is True
    assert cluster.preparation_status == "pending"


def test_snapshot_includes_cluster_and_generation_state(client, tmp_path, settings):
    from platform_app.services import confirm_generation, create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    make_global_baseline()
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
    make_global_baseline()
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


def test_project_generate_api_is_cluster_scoped_and_idempotent(client, tmp_path, settings):
    from platform_app.models import OutputSlot, OutputTemplate, PromptVersion
    from platform_app.services import create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    template = OutputTemplate.objects.create(platform="global", site="", name="Template")
    slot = OutputSlot.objects.create(template=template, name="Hero", order=1)
    batch = create_batch(user, "Batch 1")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    first = register_uploaded_asset(batch, "a.png", image_file("a.png").read(), "image/png").clusters.get()
    second = register_uploaded_asset(batch, "b.png", image_file("b.png").read(), "image/png").clusters.get()
    for cluster in (first, second):
        PromptVersion.objects.create(
            cluster=cluster,
            created_by=user,
            output_slot=slot,
            prompt_text=f"Prompt {cluster.id}",
        )
    client.force_login(user)

    payload = {"cluster_ids": [str(first.id)], "slot_orders": [1]}
    first_response = client.post(
        reverse("api_project_generate", args=[batch.id]),
        data=__import__("json").dumps(payload),
        content_type="application/json",
    )
    second_response = client.post(
        reverse("api_project_generate", args=[batch.id]),
        data=__import__("json").dumps(payload),
        content_type="application/json",
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["generation_count"] == 1
    assert second_response.json()["generation_count"] == 1
    assert first.generations.count() == 1
    assert second.generations.count() == 0


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


def test_optimize_prompt_returns_draft_without_saving(client, tmp_path, settings):
    from platform_app.services import create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    settings.APIMART_FAKE_MODE = True
    user = make_user()
    batch = create_batch(user, "Batch 1")
    batch.global_prompt = "white background"
    batch.save(update_fields=["global_prompt"])
    asset = register_uploaded_asset(batch, "a.png", image_file("a.png").read(), "image/png")
    cluster = asset.clusters.get()
    cluster.product_name = "Desk lamp"
    cluster.save(update_fields=["product_name"])
    client.force_login(user)

    response = client.post(reverse("api_optimize_prompt", args=[cluster.id]))

    assert response.status_code == 200
    assert "Desk lamp" in response.json()["suggested_prompt"]
    cluster.refresh_from_db()
    assert cluster.prompt_override == ""
