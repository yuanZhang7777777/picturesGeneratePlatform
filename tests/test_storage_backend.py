from io import BytesIO
import hashlib
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from platform_app.models import Asset, Batch, Cluster, ClusterAsset, Generation, OutputTemplate, PromptVersion, ResultAsset
from platform_app.services import register_uploaded_asset


pytestmark = pytest.mark.django_db


def png_bytes(color="white"):
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, "PNG")
    return buffer.getvalue()


class FakeOssResult:
    def __init__(self, data):
        self.data = data
        self.content_length = len(data)

    def read(self):
        return self.data


class FakeOssBucket:
    def __init__(self):
        self.objects = {}

    def put_object(self, key, data):
        if hasattr(data, "read"):
            data = data.read()
        self.objects[key] = bytes(data)

    def get_object(self, key):
        return FakeOssResult(self.objects[key])

    def get_object_meta(self, key):
        return FakeOssResult(self.objects[key])

    def object_exists(self, key):
        return key in self.objects

    def delete_object(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def fake_oss(settings, monkeypatch, tmp_path):
    bucket = FakeOssBucket()
    settings.STORAGE_BACKEND = "oss"
    settings.MEDIA_ROOT = tmp_path
    settings.OSS_ENDPOINT = "oss-cn-shanghai.aliyuncs.com"
    settings.OSS_BUCKET = "hz-sea-np-flow-prod"
    settings.OSS_ACCESS_KEY_ID = "test-key-id"
    settings.OSS_ACCESS_KEY_SECRET = "test-key-secret"
    settings.OSS_PREFIX = "independent-image-platform"
    monkeypatch.setattr("platform_app.storage._oss_bucket", lambda: bucket)
    return bucket


def make_user(username="operator"):
    return get_user_model().objects.create_user(
        username=username,
        password="long-enough-password",
        must_change_password=False,
    )


def test_upload_asset_writes_original_to_oss_not_local_disk(fake_oss, settings):
    call_command("seed_platform_templates")
    batch = Batch.objects.create(owner=make_user(), name="OSS upload")
    content = png_bytes()

    asset = register_uploaded_asset(batch, "front.png", content, "image/png")

    key = f"independent-image-platform/{asset.storage_path}"
    assert asset.storage_path.startswith(f"originals/{batch.id}/")
    assert fake_oss.objects[key] == content
    assert not (settings.MEDIA_ROOT / asset.storage_path).exists()


def test_authorized_media_and_export_read_private_oss_objects(fake_oss, client):
    call_command("seed_platform_templates")
    user = make_user()
    template = OutputTemplate.objects.get(platform="global", site="", status=OutputTemplate.Status.PUBLISHED)
    batch = Batch.objects.create(owner=user, name="OSS media", output_template=template)
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename="front.png",
        storage_path=f"originals/{batch.id}/front.png",
        sha256=hashlib.sha256(b"asset").hexdigest(),
        file_size=len(b"asset"),
        content_type="image/png",
    )
    cluster = Cluster.objects.create(batch=batch, name="SKU", product_name="SKU")
    ClusterAsset.objects.create(cluster=cluster, asset=asset, role=ClusterAsset.Role.PRIMARY, order=1)
    slot = batch.output_template.slots.order_by("order").first()
    prompt = PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        prompt_text="prompt",
        input_snapshot={},
        structured_output={},
        source_snapshot={},
    )
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        prompt_version=prompt,
        created_by=user,
        status=Generation.Status.COMPLETED,
        review_status=Generation.ReviewStatus.ACCEPTED,
        prompt_text="prompt",
        reference_snapshot=[asset.storage_path],
        attempt=1,
    )
    result = ResultAsset.objects.create(
        generation=generation,
        storage_path=f"results/{batch.id}/{cluster.id}/{slot.id}/1/result.png",
        source_url="https://provider.invalid/result.png",
        sha256=hashlib.sha256(b"result").hexdigest(),
        file_size=len(b"result"),
    )
    fake_oss.objects[f"independent-image-platform/{asset.storage_path}"] = b"asset"
    fake_oss.objects[f"independent-image-platform/{result.storage_path}"] = b"result"
    client.force_login(user)

    asset_response = client.get(reverse("api_asset_media", args=[asset.id]))
    result_response = client.get(reverse("api_result_media", args=[result.id]))
    export_response = client.get(reverse("api_project_export", args=[batch.id]))

    assert b"".join(asset_response.streaming_content) == b"asset"
    assert b"".join(result_response.streaming_content) == b"result"
    exported = b"".join(export_response.streaming_content)
    with zipfile.ZipFile(BytesIO(exported)) as archive:
        assert archive.read(archive.namelist()[0]) == b"result"
    export_keys = [key for key in fake_oss.objects if key.startswith(f"independent-image-platform/exports/{batch.id}/")]
    assert len(export_keys) == 1
    assert fake_oss.objects[export_keys[0]] == exported
