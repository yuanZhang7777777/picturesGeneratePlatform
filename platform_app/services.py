import base64
import hashlib
import mimetypes
import re
import uuid
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
import requests

from .models import (
    Asset,
    Batch,
    Cluster,
    ClusterAsset,
    Generation,
    OutputSlot,
    OutputTemplate,
    ResultAsset,
)


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TXT_BYTES = 256 * 1024
BATCH_GENERATION_LIMIT = 300


class SubmitUnknown(Exception):
    pass


class ProviderError(Exception):
    pass


class RateLimited(ProviderError):
    pass


def _sanitize_provider_text(text):
    text = str(text)
    if settings.APIMART_API_KEY:
        text = text.replace(settings.APIMART_API_KEY, "[redacted]")
    return re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [redacted]", text)


def _raise_provider_error(response):
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    message = (
        payload.get("error", {}).get("message")
        or payload.get("message")
        or f"provider returned HTTP {response.status_code}"
    )
    sanitized = _sanitize_provider_text(message)
    if response.status_code == 429:
        raise RateLimited(sanitized)
    raise ProviderError(sanitized)


class LocalStorage:
    def __init__(self, root=None):
        self.root = Path(root or settings.MEDIA_ROOT)

    def path(self, storage_path):
        return self.root / storage_path

    def archive_result(self, generation, source_url, data):
        image_format, width, height = _inspect_image(data)
        suffix = ".jpg" if image_format == "JPEG" else ".png"
        storage_path = (
            f"results/{generation.batch_id}/{generation.cluster_id}/"
            f"{generation.output_slot_id}/{generation.attempt}/{uuid.uuid4().hex}{suffix}"
        )
        target = self.path(storage_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return ResultAsset.objects.create(
            generation=generation,
            storage_path=storage_path,
            source_url="" if source_url.startswith("fake://") else source_url,
            sha256=hashlib.sha256(data).hexdigest(),
            file_size=len(data),
            width=width,
            height=height,
        )


class FakeAPIMartClient:
    def submit_generation(self, prompt, image_paths, size, resolution):
        return f"fake-{uuid.uuid4().hex}"

    def get_task(self, task_id):
        return {"status": "completed", "image_urls": [f"fake://{task_id}/result.png"]}

    def download_image(self, url):
        buffer = BytesIO()
        Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
        return buffer.getvalue()


class APIMartClient:
    def __init__(self, session=None, timeout=60):
        self.session = session or requests.Session()
        self.timeout = timeout

    @property
    def headers(self):
        if not settings.APIMART_API_KEY:
            raise ProviderError("APIMart API key is not configured")
        return {
            "Authorization": f"Bearer {settings.APIMART_API_KEY}",
            "Content-Type": "application/json",
        }

    def _data_uri(self, path):
        raw = Path(path).read_bytes()
        mime = mimetypes.guess_type(path)[0] or "image/png"
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _json(self, response):
        if response.status_code >= 400:
            _raise_provider_error(response)
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("provider returned invalid JSON") from exc

    def submit_generation(self, prompt, image_paths, size, resolution):
        payload = {
            "model": "gpt-image-2",
            "prompt": prompt,
            "n": 1,
            "size": size,
            "resolution": resolution,
            "official_fallback": False,
        }
        if image_paths:
            payload["image_urls"] = [self._data_uri(path) for path in image_paths]

        try:
            response = self.session.post(
                f"{settings.APIMART_BASE_URL}/v1/images/generations",
                json=payload,
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise SubmitUnknown("generation submit timed out") from exc
        except requests.RequestException as exc:
            raise SubmitUnknown(_sanitize_provider_text(str(exc))) from exc

        data = self._json(response).get("data")
        first = data[0] if isinstance(data, list) and data else data
        task_id = first.get("task_id") if isinstance(first, dict) else None
        if not task_id:
            raise ProviderError("provider response did not include task_id")
        return task_id

    def get_task(self, task_id):
        response = self.session.get(
            f"{settings.APIMART_BASE_URL}/v1/tasks/{task_id}",
            headers=self.headers,
            params={"language": "zh"},
            timeout=self.timeout,
        )
        payload = self._json(response)
        return payload.get("data", payload)

    def download_image(self, url):
        response = self.session.get(url, timeout=self.timeout)
        if response.status_code >= 400:
            _raise_provider_error(response)
        return response.content

    def optimize_prompt(self, payload):
        text = payload.get("text", "")
        response = self.session.post(
            f"{settings.APIMART_BASE_URL}/v1/responses",
            json={
                "model": settings.APIMART_PROMPT_MODEL,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": text,
                            }
                        ],
                    }
                ],
            },
            headers=self.headers,
            timeout=self.timeout,
        )
        return self._json(response)


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


def ensure_default_template(platform="shopee", site="SG"):
    template, _ = OutputTemplate.objects.get_or_create(
        platform=platform,
        site=site,
        name="Default one image set",
        defaults={"default_size": "1:1", "default_resolution": "1k"},
    )
    OutputSlot.objects.get_or_create(
        template=template,
        order=1,
        defaults={"name": "main", "purpose": "Main ecommerce product image"},
    )
    return template


def _used_generations_today(user=None):
    today = timezone.localdate()
    queryset = Generation.objects.exclude(status=Generation.Status.CANCELED).filter(created_at__date=today)
    if user is not None:
        queryset = queryset.filter(created_by=user)
    return queryset.count()


def preflight_batch(batch, user, template=None):
    template = template or ensure_default_template(batch.platform, batch.site)
    slot_count = template.slots.count()
    cluster_count = batch.clusters.count()
    generation_count = cluster_count * slot_count
    org_used = _used_generations_today()
    user_used = _used_generations_today(user)
    user_limit = user.daily_generation_limit or settings.USER_DAILY_GENERATION_LIMIT
    org_remaining = max(settings.ORG_DAILY_GENERATION_LIMIT - org_used, 0)
    user_remaining = max(user_limit - user_used, 0)
    blocking_errors = []

    if cluster_count == 0:
        blocking_errors.append("batch has no image clusters")
    if generation_count > BATCH_GENERATION_LIMIT:
        blocking_errors.append("batch generation limit exceeded")
    if generation_count > org_remaining:
        blocking_errors.append("organization daily quota exceeded")
    if generation_count > user_remaining:
        blocking_errors.append("user daily quota exceeded")

    return {
        "cluster_count": cluster_count,
        "slot_count": slot_count,
        "generation_count": generation_count,
        "org_remaining": org_remaining,
        "user_remaining": user_remaining,
        "blocking_errors": blocking_errors,
    }


@transaction.atomic
def confirm_generation(batch, user, template=None):
    locked_batch = Batch.objects.select_for_update().get(id=batch.id)
    existing = list(locked_batch.generations.order_by("created_at", "id"))
    if locked_batch.confirmed_generation_key and existing:
        return existing

    template = template or ensure_default_template(locked_batch.platform, locked_batch.site)
    preflight = preflight_batch(locked_batch, user, template)
    if preflight["blocking_errors"]:
        raise ValueError(", ".join(preflight["blocking_errors"]))

    generations = []
    for cluster in locked_batch.clusters.prefetch_related("cluster_assets__asset").order_by("created_at", "id"):
        references = [
            item.asset.storage_path
            for item in cluster.cluster_assets.select_related("asset").order_by("order", "id")
        ]
        prompt = cluster.prompt_override or locked_batch.global_prompt or "Create a clean ecommerce product image."
        for slot in template.slots.order_by("order", "id"):
            generations.append(
                Generation.objects.create(
                    batch=locked_batch,
                    cluster=cluster,
                    output_slot=slot,
                    created_by=user,
                    prompt_text=prompt,
                    size=template.default_size,
                    resolution=template.default_resolution,
                    reference_snapshot=references,
                )
            )

    locked_batch.confirmed_generation_key = uuid.uuid4()
    locked_batch.status = Batch.Status.QUEUED
    locked_batch.save(update_fields=["confirmed_generation_key", "status", "updated_at"])
    return generations


def _normalize_provider_status(payload):
    status = payload.get("status", "").lower()
    if status in {"processing", "in_progress", "submitted", "queued"}:
        return Generation.Status.PROCESSING
    if status in {"completed", "succeeded", "success"}:
        return Generation.Status.COMPLETED
    if status in {"failed", "error", "canceled"}:
        return Generation.Status.FAILED
    return Generation.Status.PROCESSING


def _image_urls(payload):
    if "image_urls" in payload:
        return list(payload["image_urls"])
    urls = []
    for image in payload.get("result", {}).get("images", []):
        value = image.get("url")
        if isinstance(value, list):
            urls.extend(value)
        elif value:
            urls.append(value)
    return urls


def process_generation_once(client=None, storage=None):
    client = client or (FakeAPIMartClient() if settings.APIMART_FAKE_MODE else APIMartClient())
    storage = storage or LocalStorage()
    queued = (
        Generation.objects.select_related("batch", "cluster", "output_slot")
        .filter(status=Generation.Status.QUEUED)
        .order_by("created_at", "id")
        .first()
    )
    if queued is not None:
        queued.status = Generation.Status.SUBMITTING
        queued.save(update_fields=["status", "updated_at"])
        image_paths = [str(storage.path(path)) for path in queued.reference_snapshot]
        try:
            task_id = client.submit_generation(
                queued.prompt_text,
                image_paths,
                queued.size,
                queued.resolution,
            )
        except SubmitUnknown as exc:
            queued.status = Generation.Status.SUBMIT_UNKNOWN
            queued.failure_reason = str(exc)
            queued.save(update_fields=["status", "failure_reason", "updated_at"])
            queued.batch.recompute_status()
            return 1
        except Exception as exc:
            queued.status = Generation.Status.FAILED
            queued.failure_reason = str(exc)
            queued.save(update_fields=["status", "failure_reason", "updated_at"])
            queued.batch.recompute_status()
            return 1
        queued.provider_task_id = task_id
        queued.status = Generation.Status.SUBMITTED
        queued.submitted_at = timezone.now()
        queued.save(update_fields=["provider_task_id", "status", "submitted_at", "updated_at"])
        queued.batch.recompute_status()
        return 1

    active = (
        Generation.objects.select_related("batch", "cluster", "output_slot")
        .filter(status__in=[Generation.Status.SUBMITTED, Generation.Status.PROCESSING])
        .order_by("submitted_at", "created_at", "id")
        .first()
    )
    if active is None:
        return 0

    payload = client.get_task(active.provider_task_id)
    provider_status = _normalize_provider_status(payload)
    if provider_status == Generation.Status.PROCESSING:
        active.status = Generation.Status.PROCESSING
        active.provider_payload = {"status": payload.get("status")}
        active.save(update_fields=["status", "provider_payload", "updated_at"])
        return 1
    if provider_status == Generation.Status.FAILED:
        active.status = Generation.Status.FAILED
        active.failure_reason = payload.get("error") or payload.get("message") or "provider failed"
        active.provider_payload = {"status": payload.get("status")}
        active.save(update_fields=["status", "failure_reason", "provider_payload", "updated_at"])
        active.batch.recompute_status()
        return 1

    active.status = Generation.Status.ARCHIVING
    active.provider_payload = {"status": payload.get("status")}
    active.save(update_fields=["status", "provider_payload", "updated_at"])
    urls = _image_urls(payload)
    if not urls:
        active.status = Generation.Status.FAILED
        active.failure_reason = "provider completed without image URL"
        active.save(update_fields=["status", "failure_reason", "updated_at"])
        active.batch.recompute_status()
        return 1
    for url in urls:
        storage.archive_result(active, url, client.download_image(url))
    active.status = Generation.Status.COMPLETED
    active.completed_at = timezone.now()
    active.save(update_fields=["status", "completed_at", "updated_at"])
    active.batch.recompute_status()
    return 1


def optimize_cluster_prompt(cluster, client=None):
    source_text = "\n".join(
        part
        for part in [
            f"Product name: {cluster.product_name}",
            f"Global requirements: {cluster.batch.global_prompt}",
            f"Product facts: {cluster.product_facts}",
            f"Identity lock: {cluster.identity_lock}",
            f"Cluster requirements: {cluster.prompt_override}",
        ]
        if part and not part.endswith(": ")
    )
    if settings.APIMART_FAKE_MODE:
        product = cluster.product_name or cluster.name
        prompt = (
            f"Create a 1:1 ecommerce product image for {product}. "
            f"Keep the product identity unchanged. {cluster.batch.global_prompt}".strip()
        )
        return {"suggested_prompt": prompt, "missing_fields": []}

    client = client or APIMartClient()
    response = client.optimize_prompt(
        {
            "text": (
                "Return a concise JSON object with suggested_prompt and missing_fields for an ecommerce image.\n"
                f"{source_text}"
            )
        }
    )
    return {"suggested_prompt": response.get("output_text", ""), "raw": response}
