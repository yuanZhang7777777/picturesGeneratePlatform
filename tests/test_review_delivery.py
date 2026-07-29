import io
import json
import zipfile
from pathlib import Path

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse


pytestmark = pytest.mark.django_db


def make_user(username, *, role="operator"):
    return get_user_model().objects.create_user(
        username=username,
        password="long-enough-password",
        must_change_password=False,
        role=role,
    )


def make_project(owner, name="Project"):
    from platform_app.models import Batch, Cluster, OutputSlot, OutputTemplate

    template = OutputTemplate.objects.create(platform="shopee", site="SG", name=f"{name} template")
    slot = OutputSlot.objects.create(template=template, name="main", order=1)
    batch = Batch.objects.create(
        owner=owner,
        name=name,
        output_template=template,
        platform="shopee",
        site="SG",
        market="SG",
        size="1:1",
        resolution="1k",
    )
    cluster = Cluster.objects.create(
        batch=batch,
        name="SKU 1",
        product_name="Cup",
        product_facts="BPA-free silicone",
        identity_lock="Keep the sage green cup and two handles",
        prompt_override="Calm breakfast mood",
    )
    return batch, cluster, slot


def make_generation(owner, tmp_path, *, name="Project", attempt=1, status="completed", content=b"result"):
    from platform_app.models import Generation, PromptVersion, ResultAsset

    batch, cluster, slot = make_project(owner, name)
    prompt_version = PromptVersion.objects.create(
        cluster=cluster,
        created_by=owner,
        prompt_text="Original grounded prompt",
        input_snapshot={
            "product_facts": cluster.product_facts,
            "identity_lock": cluster.identity_lock,
            "reference_snapshot": [f"originals/{batch.id}/product.png"],
        },
        structured_output={"prompt": "Original grounded prompt"},
        source_snapshot={
            "product_facts": cluster.product_facts,
            "identity_lock": cluster.identity_lock,
            "reference_snapshot": [f"originals/{batch.id}/product.png"],
        },
    )
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        prompt_version=prompt_version,
        created_by=owner,
        attempt=attempt,
        status=status,
        prompt_text=prompt_version.prompt_text,
        reference_snapshot=[f"originals/{batch.id}/product.png"],
        template_snapshot={"template": "published-v1"},
        rule_snapshot={"rules": {"background": "white"}},
    )
    storage_path = (
        f"results/{batch.id}/{cluster.id}/{slot.id}/{attempt}/"
        f"{generation.id}.png"
    )
    target = tmp_path / storage_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    result = ResultAsset.objects.create(
        generation=generation,
        storage_path=storage_path,
        source_url="https://provider.invalid/private.png",
        sha256="0" * 64,
        file_size=len(content),
    )
    return batch, cluster, slot, generation, result


def post_json(client, url, payload, *, csrf_token=None):
    headers = {"HTTP_X_CSRFTOKEN": csrf_token} if csrf_token else {}
    return client.post(url, json.dumps(payload), content_type="application/json", **headers)


def test_csrf_bootstrap_and_project_creation_only_use_published_configuration(client):
    from platform_app.models import Batch, OutputTemplate, RuleProfile

    user = make_user("owner")
    draft_template = OutputTemplate.objects.create(
        platform="shopee", site="SG", name="Draft template", status="draft"
    )
    published_template = OutputTemplate.objects.create(
        platform="shopee", site="SG", name="Published template", status="published"
    )
    OutputTemplate.objects.create(
        platform="shopee", site="SG", name="Published template", version="v2", status="draft"
    )
    draft_rule = RuleProfile.objects.create(
        platform="shopee", site="SG", name="Draft rule", status="draft"
    )
    published_rule = RuleProfile.objects.create(
        platform="shopee", site="SG", name="Published rule", status="published"
    )
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(user)

    csrf_response = csrf_client.get(reverse("api_csrf"))
    assert csrf_response.status_code == 200
    token = csrf_response.json()["csrf_token"]
    assert token

    base_payload = {
        "name": "New project",
        "platform": "shopee",
        "market": "SG",
        "size": "1:1",
        "resolution": "1k",
        "global_prompt": "White background",
    }
    missing_csrf = post_json(
        csrf_client,
        reverse("api_project_create"),
        {**base_payload, "template": str(published_template.id)},
    )
    assert missing_csrf.status_code == 403

    draft = post_json(
        csrf_client,
        reverse("api_project_create"),
        {
            **base_payload,
            "template": str(draft_template.id),
            "rule_profile": str(published_rule.id),
        },
        csrf_token=token,
    )
    assert draft.status_code == 400

    draft_rule_response = post_json(
        csrf_client,
        reverse("api_project_create"),
        {
            **base_payload,
            "template": str(published_template.id),
            "rule_profile": str(draft_rule.id),
        },
        csrf_token=token,
    )
    assert draft_rule_response.status_code == 400

    created = post_json(
        csrf_client,
        reverse("api_project_create"),
        {
            **base_payload,
            "template": str(published_template.id),
            "rule_profile": str(published_rule.id),
        },
        csrf_token=token,
    )
    assert created.status_code == 201
    batch = Batch.objects.get(id=created.json()["id"])
    assert batch.owner == user
    assert batch.output_template == published_template
    assert batch.rule_profile == published_rule

    named = post_json(
        csrf_client,
        reverse("api_project_create"),
        {**base_payload, "name": "Named project", "template": "Published template"},
        csrf_token=token,
    )
    assert named.status_code == 201
    assert Batch.objects.get(id=named.json()["id"]).output_template == published_template

    defaulted = post_json(
        csrf_client,
        reverse("api_project_create"),
        {**base_payload, "name": "Default project"},
        csrf_token=token,
    )
    assert defaulted.status_code == 201
    assert Batch.objects.get(id=defaulted.json()["id"]).output_template.status == "published"


def test_workspace_and_project_snapshots_are_scoped_and_sanitized(client, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    owner = make_user("owner")
    other = make_user("other")
    platform_admin = make_user("admin", role="admin")
    owner_batch, _, _, generation, result = make_generation(owner, tmp_path, name="Owner project")
    other_batch, *_ = make_project(other, "Other project")
    generation.provider_task_id = "provider-secret"
    generation.provider_payload = {"raw": "provider-secret"}
    generation.save(update_fields=["provider_task_id", "provider_payload"])

    client.force_login(owner)
    workspace = client.get(reverse("api_workspace_snapshot"))
    assert workspace.status_code == 200
    assert client.post(reverse("api_workspace_snapshot")).status_code == 405
    assert [project["id"] for project in workspace.json()["projects"]] == [str(owner_batch.id)]
    project = workspace.json()["projects"][0]
    assert set(
        ["id", "name", "platform", "market", "template", "size", "status", "updatedAt", "assets", "skus"]
    ) <= set(project)
    sku = project["skus"][0]
    assert sku["facts"] == "BPA-free silicone"
    assert sku["identityLock"] == "Keep the sage green cup and two handles"
    assert sku["brief"] == "Calm breakfast mood"
    output = sku["outputs"][0]
    assert output["attempt"] == 1
    assert output["version"] == 1
    assert output["imageUrl"] == reverse("api_result_media", args=[result.id])
    serialized = json.dumps(project)
    for secret_key in [
        "daily_generation_limit",
        "org_remaining",
        "user_remaining",
        "provider",
        "prompt_text",
        "source_url",
        "raw",
    ]:
        assert secret_key not in serialized

    client.force_login(other)
    assert client.get(reverse("api_project_snapshot", args=[owner_batch.id])).status_code == 404

    client.force_login(platform_admin)
    admin_workspace = client.get(reverse("api_workspace_snapshot"))
    assert {project["id"] for project in admin_workspace.json()["projects"]} == {
        str(owner_batch.id),
        str(other_batch.id),
    }
    assert client.get(reverse("api_project_snapshot", args=[owner_batch.id])).status_code == 200


def test_asset_and_result_media_are_permission_checked_and_reject_traversal(client, tmp_path, settings):
    from platform_app.models import Asset

    settings.MEDIA_ROOT = tmp_path
    owner = make_user("owner")
    other = make_user("other")
    platform_admin = make_user("admin", role="admin")
    batch, _, _, _, result = make_generation(owner, tmp_path)
    asset_path = f"originals/{batch.id}/asset.png"
    (tmp_path / asset_path).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / asset_path).write_bytes(b"asset")
    asset = Asset.objects.create(
        batch=batch,
        kind="image",
        original_filename="private.png",
        storage_path=asset_path,
        sha256="1" * 64,
        file_size=5,
        content_type="image/png",
    )

    client.force_login(owner)
    assert b"".join(client.get(reverse("api_asset_media", args=[asset.id])).streaming_content) == b"asset"
    assert b"".join(client.get(reverse("api_result_media", args=[result.id])).streaming_content) == b"result"
    assert client.post(reverse("api_asset_media", args=[asset.id])).status_code == 405
    assert client.post(reverse("api_result_media", args=[result.id])).status_code == 405

    client.force_login(other)
    assert client.get(reverse("api_asset_media", args=[asset.id])).status_code == 404
    assert client.get(reverse("api_result_media", args=[result.id])).status_code == 404

    client.force_login(platform_admin)
    assert client.get(reverse("api_asset_media", args=[asset.id])).status_code == 200

    outside = tmp_path.parent / f"{tmp_path.name}-outside-secret.txt"
    outside.write_bytes(b"secret")
    asset.storage_path = f"../{outside.name}"
    asset.save(update_fields=["storage_path"])
    client.force_login(owner)
    assert client.get(reverse("api_asset_media", args=[asset.id])).status_code == 404


def test_accept_review_is_audited_and_enables_safe_export(client, tmp_path, settings):
    from platform_app.models import AuditEvent, Generation

    settings.MEDIA_ROOT = tmp_path
    owner = make_user("owner")
    other = make_user("other")
    batch, cluster, slot, generation, _ = make_generation(owner, tmp_path)
    client.force_login(owner)

    reviewed = post_json(
        client,
        reverse("api_generation_review", args=[generation.id]),
        {"decision": "accept", "issue_tags": [], "description": "", "annotations": []},
    )
    assert reviewed.status_code == 200
    generation.refresh_from_db()
    assert generation.review_status == Generation.ReviewStatus.ACCEPTED
    assert generation.cluster.generations.count() == 1
    assert AuditEvent.objects.filter(
        actor=owner, action="generation.accept", object_id=str(generation.id)
    ).exists()

    exported = client.get(reverse("api_project_export", args=[batch.id]))
    assert exported.status_code == 200
    assert client.post(reverse("api_project_export", args=[batch.id])).status_code == 405
    archive = zipfile.ZipFile(io.BytesIO(exported.content))
    assert archive.namelist() == [
        f"project-{batch.id}/cluster-{cluster.id}/slot-{slot.id}/attempt-1.png"
    ]
    assert archive.read(archive.namelist()[0]) == b"result"
    assert AuditEvent.objects.filter(
        actor=owner, action="project.export", object_id=str(batch.id)
    ).exists()

    client.force_login(other)
    assert client.get(reverse("api_project_export", args=[batch.id])).status_code == 404


def test_export_rejects_unaccepted_outputs_and_uses_only_latest_accepted_attempt(
    client, tmp_path, settings
):
    from platform_app.models import Generation, PromptVersion, ResultAsset

    settings.MEDIA_ROOT = tmp_path
    owner = make_user("owner")
    batch, cluster, slot, first, _ = make_generation(owner, tmp_path, content=b"old")
    client.force_login(owner)

    unavailable = client.get(reverse("api_project_export", args=[batch.id]))
    assert unavailable.status_code == 400

    first.review_status = Generation.ReviewStatus.ACCEPTED
    first.save(update_fields=["review_status"])
    second_prompt = PromptVersion.objects.create(
        cluster=cluster,
        created_by=owner,
        prompt_text="Second prompt",
    )
    second = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        prompt_version=second_prompt,
        created_by=owner,
        attempt=2,
        status=Generation.Status.COMPLETED,
        review_status=Generation.ReviewStatus.ACCEPTED,
    )
    second_path = f"results/{batch.id}/{cluster.id}/{slot.id}/2/{second.id}.jpg"
    (tmp_path / second_path).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / second_path).write_bytes(b"new")
    ResultAsset.objects.create(
        generation=second,
        storage_path=second_path,
        sha256="2" * 64,
        file_size=3,
    )

    exported = client.get(reverse("api_project_export", args=[batch.id]))
    archive = zipfile.ZipFile(io.BytesIO(exported.content))
    assert archive.namelist() == [
        f"project-{batch.id}/cluster-{cluster.id}/slot-{slot.id}/attempt-2.jpg"
    ]
    assert archive.read(archive.namelist()[0]) == b"new"


def test_changes_requested_preserves_original_and_creates_clean_revision_attempt(
    client, tmp_path, settings
):
    from platform_app.models import Generation, PromptVersion, ReviewAnnotation, ReviewFeedback

    settings.MEDIA_ROOT = tmp_path
    owner = make_user("owner")
    batch, _, _, original, _ = make_generation(owner, tmp_path)
    original_prompt_id = original.prompt_version_id
    original_prompt = original.prompt_version.prompt_text
    original_references = list(original.reference_snapshot)
    client.force_login(owner)
    payload = {
        "decision": "changes_requested",
        "issue_tags": [" Composition ", "lighting", "composition"],
        "description": "Move the product left and soften the shadow.",
        "annotations": [
            {
                "kind": "stroke",
                "points": [[0, 0.25], [1, 0.75]],
                "color": "#ff0000",
                "width": 3,
            },
            {
                "kind": "circle",
                "rect": {"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.5},
            },
        ],
    }

    response = post_json(client, reverse("api_generation_review", args=[original.id]), payload)
    assert response.status_code == 200
    original.refresh_from_db()
    revision = Generation.objects.get(id=response.json()["generation"]["id"])
    feedback = ReviewFeedback.objects.get(generation=original)
    annotations = {
        annotation.kind: annotation
        for annotation in ReviewAnnotation.objects.filter(feedback=feedback)
    }

    assert original.review_status == Generation.ReviewStatus.CHANGES_REQUESTED
    assert original.prompt_version_id == original_prompt_id
    assert original.prompt_version.prompt_text == original_prompt
    assert original.reference_snapshot == original_references
    assert feedback.issue_tags == ["composition", "lighting"]
    assert annotations["stroke"].points == [[0.0, 0.25], [1.0, 0.75]]
    assert annotations["circle"].rect == [0.2, 0.3, 0.4, 0.5]
    assert revision.attempt == 2
    assert revision.status == Generation.Status.QUEUED
    batch.refresh_from_db()
    assert batch.status == batch.Status.QUEUED
    assert revision.reference_snapshot == original_references
    assert revision.prompt_version_id != original_prompt_id
    assert revision.prompt_version.input_snapshot["identity_lock"] == "Keep the sage green cup and two handles"
    assert revision.prompt_version.input_snapshot["product_facts"] == "BPA-free silicone"
    assert revision.prompt_version.input_snapshot["revision_delta"] == {
        "issue_tags": ["composition", "lighting"],
        "description": payload["description"],
    }
    revision_text = json.dumps(
        {
            "prompt": revision.prompt_text,
            "references": revision.reference_snapshot,
            "snapshot": revision.prompt_version.input_snapshot,
        }
    )
    assert payload["description"] in revision.prompt_text
    assert "stroke" not in revision_text
    assert "circle" not in revision_text
    assert "#ff0000" not in revision_text

    original_prompt_version = PromptVersion.objects.get(id=original_prompt_id)
    assert original_prompt_version.prompt_text == original_prompt

    feedback.description = "tampered"
    with pytest.raises(Exception, match="immutable"):
        feedback.save()
    annotations["stroke"].color = "#000000"
    with pytest.raises(Exception, match="immutable"):
        annotations["stroke"].save()

    feedback_admin = admin.site._registry[ReviewFeedback]
    annotation_admin = admin.site._registry[ReviewAnnotation]
    assert set(feedback_admin.get_readonly_fields(None, feedback)) >= {
        "generation",
        "reviewer",
        "decision",
        "issue_tags",
        "description",
    }
    assert set(annotation_admin.get_readonly_fields(None, annotations["stroke"])) >= {
        "feedback",
        "kind",
        "points",
        "rect",
        "color",
        "width",
    }


def test_review_validation_and_technical_retry_are_separate(client, tmp_path, settings):
    from platform_app.models import Generation, ReviewFeedback

    settings.MEDIA_ROOT = tmp_path
    owner = make_user("owner")
    _, _, _, completed, _ = make_generation(owner, tmp_path, name="Completed")
    failed_batch, _, _, failed, _ = make_generation(
        owner, tmp_path, name="Failed", status=Generation.Status.FAILED
    )
    client.force_login(owner)

    invalid = post_json(
        client,
        reverse("api_generation_review", args=[completed.id]),
        {
            "decision": "changes_requested",
            "issue_tags": ["composition"],
            "description": "Fix it",
            "annotations": [{"kind": "circle", "rect": [-0.1, 0.2, 0.3, 0.4]}],
        },
    )
    assert invalid.status_code == 400
    assert ReviewFeedback.objects.count() == 0

    failed_review = post_json(
        client,
        reverse("api_generation_review", args=[failed.id]),
        {"decision": "accept", "issue_tags": [], "description": "", "annotations": []},
    )
    assert failed_review.status_code == 400

    retry = client.post(reverse("api_generation_retry", args=[failed.id]))
    assert retry.status_code == 200
    retry_generation = Generation.objects.get(id=retry.json()["id"])
    assert retry_generation.prompt_version_id == failed.prompt_version_id
    assert retry_generation.attempt == 2
    failed_batch.refresh_from_db()
    assert failed_batch.status == failed_batch.Status.QUEUED
    assert ReviewFeedback.objects.count() == 0

    assert client.post(reverse("api_generation_retry", args=[completed.id])).status_code == 400
    assert failed_batch.generations.count() == 2
