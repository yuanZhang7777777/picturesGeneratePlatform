import hashlib
import uuid
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from PIL import Image, UnidentifiedImageError

from .models import Asset, Batch, Cluster, ClusterAsset


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TXT_BYTES = 256 * 1024


def create_batch(owner, name, platform="shopee", site="SG"):
    return Batch.objects.create(owner=owner, name=name, platform=platform, site=site)


def _bytes(content):
    if hasattr(content, "read"):
        return content.read()
    return bytes(content)


def _store(batch, filename, data):
    suffix = Path(filename).suffix.lower()
    object_name = f"originals/{batch.id}/{uuid.uuid4().hex}{suffix}"
    root = Path(settings.MEDIA_ROOT)
    target = root / object_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return object_name


def _decode_txt(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Upload must be JPEG, PNG, or UTF-8 TXT") from exc


def _inspect_image(data):
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG"}:
                raise ValueError("Upload must be JPEG, PNG, or UTF-8 TXT")
            return image.format, image.width, image.height
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Upload must be JPEG, PNG, or UTF-8 TXT") from exc


@transaction.atomic
def register_uploaded_asset(batch, filename, content, content_type):
    data = _bytes(content)
    suffix = Path(filename).suffix.lower()
    sha256 = hashlib.sha256(data).hexdigest()

    if suffix == ".txt" or content_type == "text/plain":
        if len(data) > MAX_TXT_BYTES:
            raise ValueError("TXT file is too large")
        text = _decode_txt(data)
        storage_path = _store(batch, filename, data)
        return Asset.objects.create(
            batch=batch,
            kind=Asset.Kind.TXT,
            original_filename=filename,
            storage_path=storage_path,
            sha256=sha256,
            file_size=len(data),
            content_type="text/plain",
            text_content=text,
        )

    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image file is too large")

    image_format, width, height = _inspect_image(data)
    content_type = "image/jpeg" if image_format == "JPEG" else "image/png"
    storage_path = _store(batch, filename, data)
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename=filename,
        storage_path=storage_path,
        sha256=sha256,
        file_size=len(data),
        content_type=content_type,
        width=width,
        height=height,
    )
    Cluster.create_for_asset(batch=batch, asset=asset)
    if batch.status == Batch.Status.DRAFT:
        batch.status = Batch.Status.ORGANIZING
        batch.save(update_fields=["status", "updated_at"])
    return asset


def _promote_primary_if_needed(cluster):
    if not cluster.cluster_assets.exists():
        cluster.delete()
        return
    if cluster.cluster_assets.filter(role=ClusterAsset.Role.PRIMARY).exists():
        cluster.version += 1
        cluster.save(update_fields=["version", "updated_at"])
        return
    first = cluster.cluster_assets.order_by("order", "id").first()
    first.role = ClusterAsset.Role.PRIMARY
    first.order = 1
    first.save(update_fields=["role", "order"])
    cluster.version += 1
    cluster.save(update_fields=["version", "updated_at"])


@transaction.atomic
def merge_asset_into_cluster(asset, target_cluster, expected_version=None):
    target = Cluster.objects.select_for_update().get(id=target_cluster.id)
    if expected_version is not None and target.version != expected_version:
        raise ValueError("Cluster changed; refresh before saving")
    old_cluster = None
    old_relation = ClusterAsset.objects.select_related("cluster").filter(asset=asset).first()
    if old_relation is not None and old_relation.cluster_id != target.id:
        old_cluster = Cluster.objects.select_for_update().get(id=old_relation.cluster_id)
    relation = target.add_asset(asset)
    if old_cluster is not None:
        _promote_primary_if_needed(old_cluster)
    return relation


@transaction.atomic
def move_asset_to_new_cluster(asset):
    old_relation = ClusterAsset.objects.select_related("cluster").filter(asset=asset).first()
    old_cluster = None
    if old_relation is not None:
        old_cluster = Cluster.objects.select_for_update().get(id=old_relation.cluster_id)
        old_relation.delete()
    new_cluster = Cluster.create_for_asset(batch=asset.batch, asset=asset)
    if old_cluster is not None:
        _promote_primary_if_needed(old_cluster)
    return new_cluster


def latest_attempt(cluster, slot):
    return (
        cluster.generations.filter(output_slot=slot)
        .aggregate(value=Max("attempt"))["value"]
        or 0
    )
