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


def webp_file(name="product.webp"):
    buffer = BytesIO()
    Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(buffer, "WEBP")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/webp")


def make_global_baseline():
    from platform_app.models import OutputSlot, OutputTemplate

    template = OutputTemplate.objects.create(platform="global", site="", name="Test global baseline")
    OutputSlot.objects.create(template=template, name="main", order=1)


def approve_view_prompt(cluster, user, slot, revision=1):
    from platform_app.models import Cluster, PromptVersion
    from platform_app.services import _effective_config_signature

    primary = cluster.cluster_assets.select_related("asset").get()
    cluster.preparation_status = Cluster.PreparationStatus.READY
    cluster.analysis_snapshot = {
        "_preparation_revision": revision,
        "identity": {
            "primary_asset_id": str(primary.asset_id),
            "supporting_asset_ids": [],
        },
    }
    cluster.save(update_fields=["preparation_status", "analysis_snapshot"])
    signature = _effective_config_signature(cluster.batch, cluster)
    snapshot = {
        "_preparation_revision": revision,
        "_effective_config_signature": signature,
    }
    return PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        output_slot=slot,
        node_name="N4",
        prompt_text=f"Approved {cluster.id}",
        input_snapshot=snapshot,
        source_snapshot=snapshot,
        structured_output=snapshot,
        evaluation={
            "rule_gate": {
                "decision": "pass",
                "hard_blocks": [],
                "snapshot_id": f"gate-{cluster.id}",
                "preparation_revision": revision,
                "effective_config_signature": signature,
            }
        },
    )


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


def test_upload_api_keeps_successes_and_reports_each_rejected_file(client, tmp_path, settings):
    from platform_app.models import Asset, Batch

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = Batch.objects.create(owner=user, name="Partial upload")
    client.force_login(user)

    response = client.post(
        reverse("api_upload_assets", args=[batch.id]),
        {
            "files": [
                image_file("front.png"),
                SimpleUploadedFile("raw.heic", b"not-heic", content_type="image/heic"),
            ],
            "relative_paths": ["lamp/front.png", "lamp/raw.heic"],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "asset_count": 1,
        "imported": [
            {
                "filename": "lamp/front.png",
                "asset_id": str(Asset.objects.get(batch=batch).id),
                "cluster_id": str(batch.clusters.get().id),
            }
        ],
        "rejected": [
            {
                "filename": "lamp/raw.heic",
                "code": "unsupported_format",
                "message": "仅支持 JPEG、PNG、WebP 图片和 UTF-8 TXT",
            }
        ],
    }


def test_upload_api_normalizes_webp_to_png(client, tmp_path, settings):
    from platform_app.models import Asset, Batch

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = Batch.objects.create(owner=user, name="WebP upload")
    client.force_login(user)

    response = client.post(
        reverse("api_upload_assets", args=[batch.id]),
        {"files": [webp_file()], "relative_paths": ["catalog/product.webp"]},
    )

    assert response.status_code == 200
    asset = Asset.objects.get(batch=batch)
    assert asset.original_filename == "catalog/product.webp"
    assert asset.content_type == "image/png"
    assert asset.storage_path.endswith(".png")
    with Image.open(tmp_path / asset.storage_path) as stored:
        assert stored.format == "PNG"


def test_upload_api_merges_txt_by_relative_path_before_image_preparation(client, tmp_path, settings):
    from platform_app.models import Batch

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = Batch.objects.create(owner=user, name="TXT seed")
    client.force_login(user)

    response = client.post(
        reverse("api_upload_assets", args=[batch.id]),
        {
            "files": [
                image_file("front.png"),
                SimpleUploadedFile("z.txt", "暖色木质".encode(), content_type="text/plain"),
                SimpleUploadedFile("a.txt", "自然日光".encode(), content_type="text/plain"),
            ],
            "relative_paths": ["store/front.png", "store/z.txt", "store/a.txt"],
        },
    )

    assert response.status_code == 200
    batch.refresh_from_db()
    assert batch.global_prompt == "自然日光\n\n暖色木质"
    assert batch.clusters.get().preparation_status == "pending"


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
        approve_view_prompt(cluster, user, slot)
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

    assert first_response.status_code == 202
    assert second_response.status_code == 202
    assert first_response.json()["generation_count"] == 1
    assert second_response.json()["generation_count"] == 1
    assert first_response.json()["items"] == [
        {"cluster_id": str(first.id), "status": "queued"}
    ]
    assert first.generations.count() == 1
    assert second.generations.count() == 0


def test_project_generate_api_isolates_queued_waiting_and_blocked_products(
    client,
    tmp_path,
    settings,
):
    from platform_app.models import Cluster, OutputSlot, OutputTemplate
    from platform_app.services import create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    template = OutputTemplate.objects.create(platform="global", site="", name="Queue states")
    slot = OutputSlot.objects.create(template=template, name="Hero", order=1)
    batch = create_batch(user, "Queue states")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    clusters = [
        register_uploaded_asset(
            batch,
            f"{index}.png",
            image_file(f"{index}.png").read(),
            "image/png",
        ).clusters.get()
        for index in range(6)
    ]
    ready, pending, preparing, blocked, failed, invalid_ready = clusters
    approve_view_prompt(ready, user, slot)
    Cluster.objects.filter(id=preparing.id).update(
        preparation_status=Cluster.PreparationStatus.PREPARING
    )
    Cluster.objects.filter(id=blocked.id).update(
        preparation_status=Cluster.PreparationStatus.BLOCKED,
        preparation_error="identity_conflict",
    )
    Cluster.objects.filter(id=failed.id).update(
        preparation_status=Cluster.PreparationStatus.FAILED,
        preparation_error="provider failed",
    )
    Cluster.objects.filter(id=invalid_ready.id).update(
        preparation_status=Cluster.PreparationStatus.READY,
        analysis_snapshot={"_preparation_revision": 1},
    )
    client.force_login(user)

    response = client.post(
        reverse("api_project_generate", args=[batch.id]),
        data=__import__("json").dumps(
            {"cluster_ids": [str(cluster.id) for cluster in clusters]}
        ),
        content_type="application/json",
    )

    assert response.status_code == 202
    assert response.json()["generation_count"] == 1
    items = {
        item["cluster_id"]: item
        for item in response.json()["items"]
    }
    assert items[str(ready.id)] == {
        "cluster_id": str(ready.id),
        "status": "queued",
    }
    assert items[str(pending.id)]["status"] == "waiting"
    assert items[str(preparing.id)]["status"] == "waiting"
    assert items[str(blocked.id)]["status"] == "blocked"
    assert items[str(failed.id)]["status"] == "blocked"
    assert items[str(invalid_ready.id)]["status"] == "blocked"
    assert items[str(invalid_ready.id)]["code"] == "prompt_not_ready"
    pending.refresh_from_db()
    preparing.refresh_from_db()
    assert pending.auto_generate is True
    assert preparing.auto_generate is True
    assert batch.generations.count() == 1


def test_legacy_confirm_endpoint_cannot_bypass_prompt_preparation(
    client,
    tmp_path,
    settings,
):
    from platform_app.models import OutputSlot, OutputTemplate
    from platform_app.services import create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    template = OutputTemplate.objects.create(platform="global", site="", name="Legacy gate")
    OutputSlot.objects.create(template=template, name="Hero", order=1)
    batch = create_batch(user, "Legacy gate")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    cluster = register_uploaded_asset(
        batch,
        "source.png",
        image_file("source.png").read(),
        "image/png",
    ).clusters.get()
    client.force_login(user)

    response = client.post(
        reverse("api_confirm_generation", args=[batch.id]),
        data="{}",
        content_type="application/json",
    )

    assert response.status_code == 202
    assert response.json()["generation_count"] == 0
    assert response.json()["items"] == [
        {
            "cluster_id": str(cluster.id),
            "status": "waiting",
            "code": "preparation_in_progress",
            "message": "Product preparation will queue generation automatically.",
        }
    ]
    cluster.refresh_from_db()
    assert cluster.auto_generate is True
    assert batch.generations.count() == 0


def test_update_cluster_prompt_requires_current_version(client, tmp_path, settings):
    from platform_app.models import OutputSlot, OutputTemplate, PromptVersion
    from platform_app.services import create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = create_batch(user, "Batch 1")
    template = OutputTemplate.objects.create(platform="global", site="", name="Editable template")
    first_slot = OutputSlot.objects.create(template=template, name="Hero", order=1)
    second_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    batch.output_template = template
    batch.save(update_fields=["output_template"])
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
        {
            "expected_version": cluster.version,
            "prompt_override": "new prompt",
            "prompts": [
                {"slot_order": 1, "prompt": "Accurate white background product hero"},
                {"slot_order": 2, "prompt": "Close product detail in soft daylight"},
            ],
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    cluster.refresh_from_db()
    assert cluster.prompt_override == "new prompt"
    assert cluster.version == 2
    prompts = list(PromptVersion.objects.filter(cluster=cluster).order_by("output_slot__order"))
    assert [item.output_slot_id for item in prompts] == [first_slot.id, second_slot.id]
    assert "Standard product hero:" in prompts[0].prompt_text
    assert prompts[1].prompt_text == "Close product detail in soft daylight"
    assert all(item.structured_output["manual_edit"] is True for item in prompts)


def test_update_cluster_accepts_editor_fields_and_requeues_blocked_preparation(client, tmp_path, settings):
    from platform_app.models import Cluster, OutputSlot, OutputTemplate
    from platform_app.services import create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    template = OutputTemplate.objects.create(platform="global", site="", name="Editor template")
    OutputSlot.objects.create(template=template, name="Hero", order=1)
    batch = create_batch(user, "Batch 1")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    cluster = register_uploaded_asset(batch, "a.png", image_file("a.png").read(), "image/png").clusters.get()
    cluster.preparation_status = Cluster.PreparationStatus.BLOCKED
    cluster.preparation_error = "product identity needs confirmation"
    cluster.save(update_fields=["preparation_status", "preparation_error"])
    client.force_login(user)

    response = client.post(
        reverse("api_update_cluster", args=[cluster.id]),
        {
            "expected_version": cluster.version,
            "name": "绿色陶瓷马克杯",
            "relation_type": "same_product",
            "prompt_override": "自然日光家居风",
            "prompts": [],
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    cluster.refresh_from_db()
    assert cluster.name == "绿色陶瓷马克杯"
    assert cluster.product_name == "绿色陶瓷马克杯"
    assert cluster.relation_type == "same_product"
    assert cluster.prompt_override == "自然日光家居风"
    assert cluster.preparation_status == Cluster.PreparationStatus.PENDING
    assert cluster.preparation_error == ""


def test_update_cluster_rejects_stringified_or_invalid_slot_prompts(client, tmp_path, settings):
    from platform_app.models import OutputSlot, OutputTemplate, PromptVersion
    from platform_app.services import create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    template = OutputTemplate.objects.create(platform="global", site="", name="Editable template")
    OutputSlot.objects.create(template=template, name="Hero", order=1)
    batch = create_batch(user, "Batch 1")
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    cluster = register_uploaded_asset(batch, "a.png", image_file("a.png").read(), "image/png").clusters.get()
    client.force_login(user)

    stringified = client.post(
        reverse("api_update_cluster", args=[cluster.id]),
        {
            "expected_version": cluster.version,
            "prompts": '[{"slot_order":1,"prompt":"not an array"}]',
        },
        content_type="application/json",
    )
    unknown_slot = client.post(
        reverse("api_update_cluster", args=[cluster.id]),
        {
            "expected_version": cluster.version,
            "prompts": [{"slot_order": 99, "prompt": "unknown"}],
        },
        content_type="application/json",
    )
    invalid_relation = client.post(
        reverse("api_update_cluster", args=[cluster.id]),
        {
            "expected_version": cluster.version,
            "relation_type": "different_products",
            "prompts": [],
        },
        content_type="application/json",
    )

    assert stringified.status_code == 400
    assert unknown_slot.status_code == 400
    assert invalid_relation.status_code == 400
    assert PromptVersion.objects.filter(cluster=cluster).count() == 0


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


def test_delete_asset_requires_the_project_owner(client, tmp_path, settings):
    from platform_app.models import Asset, Batch, Cluster
    from platform_app.services import register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    owner = make_user("owner")
    other_user = make_user("other")
    batch = Batch.objects.create(owner=owner, name="Private project")
    asset = register_uploaded_asset(batch, "a.png", image_file("a.png").read(), "image/png")
    client.force_login(other_user)

    response = client.delete(reverse("api_delete_asset", args=[asset.id]))

    assert response.status_code == 404
    assert Asset.objects.filter(id=asset.id).exists()
    assert Cluster.objects.filter(batch=batch).exists()


def test_delete_asset_removes_an_unused_product_and_its_image(client, tmp_path, settings):
    from platform_app.models import Asset, Batch, Cluster
    from platform_app.services import register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    batch = Batch.objects.create(owner=user, name="Disposable project")
    asset = register_uploaded_asset(batch, "a.png", image_file("a.png").read(), "image/png")
    client.force_login(user)

    response = client.delete(reverse("api_delete_asset", args=[asset.id]))

    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    assert not Asset.objects.filter(id=asset.id).exists()
    assert not Cluster.objects.filter(batch=batch).exists()


def test_delete_cluster_archives_product_with_generation_history(client, tmp_path, settings):
    from platform_app.models import Batch, Generation, OutputSlot, OutputTemplate
    from platform_app.services import register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    template = OutputTemplate.objects.create(platform="global", site="", name="Archive template")
    slot = OutputSlot.objects.create(template=template, name="Hero", order=1)
    batch = Batch.objects.create(owner=user, name="Historical project")
    asset = register_uploaded_asset(batch, "a.png", image_file("a.png").read(), "image/png")
    cluster = asset.clusters.get()
    Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        status=Generation.Status.COMPLETED,
    )
    client.force_login(user)

    response = client.delete(reverse("api_update_cluster", args=[cluster.id]))

    assert response.status_code == 200
    assert response.json() == {"status": "archived"}
    cluster.refresh_from_db()
    asset.refresh_from_db()
    assert cluster.archived_at is not None
    assert asset.archived_at is not None
    assert cluster.generations.count() == 1


def test_delete_asset_rejects_a_product_with_an_active_generation(client, tmp_path, settings):
    from platform_app.models import Batch, Generation, OutputSlot, OutputTemplate
    from platform_app.services import register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    template = OutputTemplate.objects.create(platform="global", site="", name="Active template")
    slot = OutputSlot.objects.create(template=template, name="Hero", order=1)
    batch = Batch.objects.create(owner=user, name="Active project")
    asset = register_uploaded_asset(batch, "a.png", image_file("a.png").read(), "image/png")
    cluster = asset.clusters.get()
    Generation.objects.create(batch=batch, cluster=cluster, output_slot=slot, status=Generation.Status.PROCESSING)
    client.force_login(user)

    response = client.delete(reverse("api_delete_asset", args=[asset.id]))

    assert response.status_code == 409
    assert response.json() == {"error": "Product has an active generation"}
