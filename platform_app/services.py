import base64
import copy
import hashlib
import ipaddress
import json
import mimetypes
import re
import uuid
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import F, Max, Prefetch
from django.urls import reverse
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
import requests

from .models import (
    Asset,
    AuditEvent,
    Batch,
    Cluster,
    ClusterAsset,
    CompetitorInsight,
    DailyGenerationUsage,
    Generation,
    OutputSlot,
    OutputTemplate,
    PromptNodeTemplate,
    PromptVersion,
    ResultAsset,
    ReviewAnnotation,
    ReviewFeedback,
    RuleProfile,
    SkuImportItem,
)
from .template_policy import apply_standard_product_hero_policy


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TXT_BYTES = 256 * 1024
BATCH_GENERATION_LIMIT = 300
STYLE_DNA_VALUES = {
    "composition": {"centered", "top-left", "top-right", "bottom-left", "bottom-right", "rule-of-thirds", "symmetrical", "negative-space"},
    "lighting": {"soft daylight", "natural daylight", "diffused studio", "softbox", "high key", "low key"},
    "color": {"warm neutral", "cool neutral", "muted", "pastel", "monochrome", "black and white"},
    "scene_density": {"minimal", "sparse", "balanced", "full"},
    "camera": {"eye level", "top-down", "45-degree", "macro", "wide", "close-up"},
}
STYLE_DNA_FIELDS = set(STYLE_DNA_VALUES)
_UNSET = object()
INFANT_KEYWORDS = ("baby", "infant", "newborn", "toddler", "婴", "幼儿", "宝宝")
ADULT_KEYWORDS = ("adult", "clothing", "apparel", "beauty", "fashion", "成人", "服装", "美容")


class SubmitUnknown(Exception):
    pass


class ProviderError(Exception):
    pass


class RateLimited(ProviderError):
    pass


class CatalogError(Exception):
    pass


def _catalog_response_data(response, expected_type):
    try:
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise CatalogError("Catalog service is unavailable") from exc
    status = payload.get("status") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("success") is not True
        or ("code" in payload and payload["code"] not in (200, "200"))
        or (
            status is not None
            and status not in (True, 200, "200", "ok", "success")
        )
        or not isinstance(payload.get("data"), expected_type)
    ):
        raise CatalogError("Catalog service returned an invalid response")
    return payload["data"]


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


class CatalogClient:
    """The catalog boundary; imported fields stay limited to SKU, name, and image URL."""

    def __init__(self, session=None, timeout=None):
        self.session = session or requests.Session()
        self.timeout = timeout or settings.CATALOG_TIMEOUT_SECONDS
        self._token = None

    def _login(self):
        if not all((settings.CATALOG_LOGIN_URL, settings.CATALOG_USERNAME, settings.CATALOG_PASSWORD)):
            raise CatalogError("Catalog service is not configured")
        try:
            response = self.session.post(
                settings.CATALOG_LOGIN_URL,
                json={"username": settings.CATALOG_USERNAME, "password": settings.CATALOG_PASSWORD},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CatalogError("Catalog service is unavailable") from exc
        data = _catalog_response_data(response, dict)
        self._token = data.get("accessToken") or data.get("token")
        if not isinstance(self._token, str) or not self._token.strip():
            raise CatalogError("Catalog service rejected authentication")
        self._token = self._token.strip()

    def fetch_products(self, skus):
        if not settings.CATALOG_QUERY_URL:
            raise CatalogError("Catalog service is not configured")
        if self._token is None:
            self._login()
        try:
            response = self.session.post(
                settings.CATALOG_QUERY_URL,
                json={"skuList": skus},
                headers={"Authorization": self._token},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CatalogError("Catalog service is unavailable") from exc
        data = _catalog_response_data(response, list)
        requested_skus = set(skus)
        products = {}
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("sku"), str):
                raise CatalogError("Catalog service returned an invalid response")
            sku = item["sku"].strip()
            if not sku:
                raise CatalogError("Catalog service returned an invalid response")
            if sku not in requested_skus:
                continue
            products[sku] = {
                "sku": sku,
                "productName": str(item.get("productName") or ""),
                "pic": str(item.get("pic") or ""),
            }
        return products


def create_batch(owner, name, platform="shopee", site="SG"):
    return Batch.objects.create(owner=owner, name=name, platform=platform, site=site)


def _published_configuration(model, value, *, platform, site):
    if not value:
        return None
    queryset = model.objects.filter(platform=platform, site=site)
    try:
        item = queryset.filter(id=uuid.UUID(str(value))).first()
    except (ValueError, AttributeError):
        named = queryset.filter(name=str(value))
        item = (
            named.filter(status=model.Status.PUBLISHED)
            .order_by("-version", "-id")
            .first()
            or named.order_by("-version", "-id").first()
        )
    if item is None:
        raise ValueError(f"{model._meta.verbose_name} not found")
    if item.status != model.Status.PUBLISHED:
        raise ValueError(f"{model._meta.verbose_name} must be published")
    return item


def create_project(
    owner,
    *,
    name,
    platform="shopee",
    market="SG",
    template=None,
    rule_profile=None,
    size="",
    resolution="",
    global_prompt="",
):
    name = str(name or "").strip()
    if not name:
        raise ValueError("name is required")
    platform = str(platform or "shopee").strip()
    market = str(market or "SG").strip()
    output_template = _published_configuration(
        OutputTemplate,
        template,
        platform=platform,
        site=market,
    )
    if output_template is None:
        output_template = _global_fallback_template()
    rules = _published_configuration(
        RuleProfile,
        rule_profile,
        platform=platform,
        site=market,
    )
    return Batch.objects.create(
        owner=owner,
        name=name,
        platform=platform,
        site=market,
        market=market,
        output_template=output_template,
        rule_profile=rules,
        size=str(size or output_template.default_size),
        resolution=str(resolution or output_template.default_resolution),
        global_prompt=str(global_prompt or ""),
    )


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
    try:
        target.write_bytes(data)
    except OSError:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise
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


def _catalog_image_url(value):
    parsed = urlparse(str(value or ""))
    host = (parsed.hostname or "").lower()
    allowed = {str(address).strip().lower() for address in settings.CATALOG_ALLOWED_IMAGE_HOSTS}
    if parsed.scheme not in {"http", "https"} or not host or host not in allowed or parsed.username or parsed.password:
        raise CatalogError("Catalog image is not allowed")
    try:
        address = ipaddress.ip_address(host)
        if (
            not address.is_global
            or address.is_multicast
            or address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_unspecified
        ):
            raise CatalogError("Catalog image is not allowed")
    except ValueError as exc:
        raise CatalogError("Catalog image is not allowed") from exc
    return parsed.geturl()


def download_catalog_image(url, session=None):
    current = _catalog_image_url(url)
    session = session or requests.Session()
    for _ in range(settings.CATALOG_MAX_REDIRECTS + 1):
        try:
            response = session.get(current, timeout=settings.CATALOG_TIMEOUT_SECONDS, stream=True, allow_redirects=False)
        except requests.RequestException as exc:
            raise CatalogError("Catalog image could not be downloaded") from exc
        try:
            if response.is_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise CatalogError("Catalog image could not be downloaded")
                current = _catalog_image_url(urljoin(current, location))
                continue
            if response.status_code != 200:
                raise CatalogError("Catalog image could not be downloaded")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if content_type not in {"image/jpeg", "image/png"}:
                raise CatalogError("Catalog image is not supported")
            try:
                content_length = int(response.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise CatalogError("Catalog image could not be downloaded") from exc
            if content_length > settings.CATALOG_MAX_IMAGE_BYTES:
                raise CatalogError("Catalog image is too large")
            chunks = []
            total = 0
            for chunk in response.iter_content(64 * 1024):
                total += len(chunk)
                if total > settings.CATALOG_MAX_IMAGE_BYTES:
                    raise CatalogError("Catalog image is too large")
                chunks.append(chunk)
            return b"".join(chunks), content_type
        finally:
            response.close()
    raise CatalogError("Catalog image redirects too many times")


def _validate_catalog_image(data, content_type):
    if len(data) > settings.CATALOG_MAX_IMAGE_BYTES:
        raise CatalogError("Catalog image is too large")
    content_type = str(content_type or "").split(";", 1)[0].lower()
    if content_type not in {"image/jpeg", "image/png"}:
        raise CatalogError("Catalog image is not supported")
    try:
        image_format, width, height = _inspect_image(data)
    except (ValueError, Image.DecompressionBombError) as exc:
        raise CatalogError("Catalog image could not be imported") from exc
    if width * height > settings.CATALOG_MAX_IMAGE_PIXELS:
        raise CatalogError("Catalog image dimensions are too large")
    expected_content_type = "image/jpeg" if image_format == "JPEG" else "image/png"
    if content_type != expected_content_type:
        raise CatalogError("Catalog image is not supported")
    return data, expected_content_type, width, height


def _archive_catalog_image(batch, sku, image):
    data, content_type, width, height = image
    suffix = ".jpg" if content_type == "image/jpeg" else ".png"
    storage_path = _store(batch, f"{sku}{suffix}", data)
    try:
        return Asset.objects.create(
            batch=batch,
            kind=Asset.Kind.IMAGE,
            original_filename=f"{sku}{suffix}",
            storage_path=storage_path,
            sha256=hashlib.sha256(data).hexdigest(),
            file_size=len(data),
            content_type=content_type,
            width=width,
            height=height,
        )
    except Exception:
        _remove_catalog_archive(storage_path)
        raise


def _remove_catalog_archive(storage_path):
    try:
        (Path(settings.MEDIA_ROOT) / storage_path).unlink(missing_ok=True)
    except OSError:
        pass


def _sku_import_error_code(item):
    if item.status != SkuImportItem.Status.FAILED:
        return None
    if item.error_message == "SKU could not be imported":
        return "sku_not_found"
    if item.error_message == "Catalog service is unavailable":
        return "catalog_unavailable"
    if item.error_message == "Project is locked for generation":
        return "project_locked"
    if item.error_message == "Catalog image could not be archived":
        return "archive_failed"
    if item.error_message == "Catalog image could not be imported":
        return "catalog_image_invalid"
    return "import_failed"


def _serialize_sku_import_item(item):
    return {
        "sku": item.sku,
        "productName": item.product_name,
        "status": item.status,
        "clusterId": str(item.cluster_id) if item.cluster_id else None,
        "errorCode": _sku_import_error_code(item),
    }


def _project_is_locked(batch):
    return bool(batch.confirmed_generation_key) or batch.generations.exists()


def _create_sku_import_item(batch, sku, product_name, status, *, cluster=None, error_message=""):
    attempt = (
        SkuImportItem.objects.filter(batch=batch, sku=sku)
        .aggregate(value=Max("attempt"))["value"]
        or 0
    ) + 1
    return SkuImportItem.objects.create(
        batch=batch,
        cluster=cluster,
        sku=sku,
        attempt=attempt,
        product_name=product_name,
        status=status,
        error_message=error_message,
    )


def import_skus(batch, skus, *, catalog_client=None, image_downloader=None):
    if not isinstance(skus, list):
        raise ValueError("skus must be an array")
    if len(skus) > settings.CATALOG_MAX_SKUS_PER_REQUEST:
        raise ValueError(f"at most {settings.CATALOG_MAX_SKUS_PER_REQUEST} SKUs are allowed")
    clean_skus = list(dict.fromkeys(str(sku).strip() for sku in skus if str(sku).strip()))
    if not clean_skus:
        raise ValueError("at least one SKU is required")
    if any(len(sku) > 120 for sku in clean_skus):
        raise ValueError("SKU is too long")

    catalog_client = catalog_client or CatalogClient()
    image_downloader = image_downloader or download_catalog_image

    with transaction.atomic():
        locked_batch = Batch.objects.select_for_update().get(id=batch.id)
        if _project_is_locked(locked_batch):
            items = [
                _serialize_sku_import_item(
                    _create_sku_import_item(
                        locked_batch,
                        sku,
                        "",
                        SkuImportItem.Status.FAILED,
                        error_message="Project is locked for generation",
                    )
                )
                for sku in clean_skus
            ]
            return {"imported": 0, "failed": len(items), "items": items}

    try:
        products = catalog_client.fetch_products(clean_skus)
    except CatalogError:
        products = None

    imported = failed = 0
    items = []
    for sku in clean_skus:
        product = products.get(sku) if products is not None else None
        product_name = str((product or {}).get("productName") or "")[:200]
        image = None
        if products is None:
            error = "Catalog service is unavailable"
        elif not product:
            error = "SKU could not be imported"
        else:
            error = ""
            if not Cluster.objects.filter(batch=batch, sku=sku).exists():
                try:
                    image_data, content_type = image_downloader(_catalog_image_url(product.get("pic")))
                    image = _validate_catalog_image(image_data, content_type)
                except (CatalogError, ValueError, TypeError, Image.DecompressionBombError):
                    error = "Catalog image could not be imported"

        storage_path = None
        try:
            with transaction.atomic():
                locked_batch = Batch.objects.select_for_update().get(id=batch.id)
                if _project_is_locked(locked_batch):
                    item = _create_sku_import_item(
                        locked_batch,
                        sku,
                        product_name,
                        SkuImportItem.Status.FAILED,
                        error_message="Project is locked for generation",
                    )
                else:
                    cluster = Cluster.objects.select_for_update().filter(batch=locked_batch, sku=sku).first()
                    if error:
                        item = _create_sku_import_item(
                            locked_batch,
                            sku,
                            product_name,
                            SkuImportItem.Status.FAILED,
                            error_message=error,
                        )
                    elif cluster is None and image is None:
                        item = _create_sku_import_item(
                            locked_batch,
                            sku,
                            product_name,
                            SkuImportItem.Status.FAILED,
                            error_message="Catalog image could not be imported",
                        )
                    else:
                        if cluster is None:
                            asset = _archive_catalog_image(locked_batch, sku, image)
                            storage_path = asset.storage_path
                            cluster = Cluster.objects.create(
                                batch=locked_batch,
                                sku=sku,
                                name=product_name or sku,
                                product_name=product_name,
                            )
                            ClusterAsset.objects.create(
                                cluster=cluster,
                                asset=asset,
                                role=ClusterAsset.Role.PRIMARY,
                                order=1,
                            )
                            if locked_batch.status == Batch.Status.DRAFT:
                                locked_batch.status = Batch.Status.ORGANIZING
                                locked_batch.save(update_fields=["status", "updated_at"])
                        elif product_name and cluster.product_name != product_name:
                            cluster.product_name = product_name
                            cluster.name = product_name
                            cluster.version += 1
                            cluster.save(update_fields=["product_name", "name", "version", "updated_at"])
                        item = _create_sku_import_item(
                            locked_batch,
                            sku,
                            product_name or cluster.product_name,
                            SkuImportItem.Status.IMPORTED,
                            cluster=cluster,
                        )
        except (OSError, DatabaseError):
            if storage_path:
                _remove_catalog_archive(storage_path)
            with transaction.atomic():
                locked_batch = Batch.objects.select_for_update().get(id=batch.id)
                item = _create_sku_import_item(
                    locked_batch,
                    sku,
                    product_name,
                    SkuImportItem.Status.FAILED,
                    error_message="Catalog image could not be archived",
                )
        except Exception:
            if storage_path:
                _remove_catalog_archive(storage_path)
            raise

        items.append(_serialize_sku_import_item(item))
        if item.status == SkuImportItem.Status.IMPORTED:
            imported += 1
        else:
            failed += 1
    return {"imported": imported, "failed": failed, "items": items}


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


def publish_prompt_node_template(node_template):
    with transaction.atomic():
        target = PromptNodeTemplate.objects.select_for_update().get(id=node_template.id)
        PromptNodeTemplate.objects.select_for_update().filter(node_name=target.node_name).exclude(
            id=target.id
        ).update(status=PromptNodeTemplate.Status.RETIRED)
        target.status = PromptNodeTemplate.Status.PUBLISHED
        target.save(update_fields=["status", "updated_at"])
        return target


def rollback_prompt_node_template(node_name, version):
    target = PromptNodeTemplate.objects.get(node_name=node_name, version=version)
    return publish_prompt_node_template(target)


def _global_fallback_template():
    for seed_key in (
        "global-marketplace-eight-slot-template",
        "global-marketplace-baseline-template",
    ):
        template = OutputTemplate.objects.filter(
            platform="global",
            site="",
            status=OutputTemplate.Status.PUBLISHED,
            seed_key=seed_key,
            slots__order=8,
        ).first()
        if template is not None:
            return template
    template = OutputTemplate.objects.filter(
        platform="global",
        site="",
        status=OutputTemplate.Status.PUBLISHED,
    ).order_by("-version", "-id").first()
    if template is None:
        raise ValueError("published global baseline template is required")
    return template


def _sanitize_style_dna(style_dna):
    if not isinstance(style_dna, dict):
        return {}
    sanitized = {}
    for key, value in style_dna.items():
        if key not in STYLE_DNA_FIELDS or not isinstance(value, str):
            continue
        value = " ".join(value.lower().split())
        if value in STYLE_DNA_VALUES[key]:
            sanitized[key] = value
    return sanitized


def _cluster_style_dna(cluster, supplied_style_dna=None):
    if supplied_style_dna is not None:
        return _sanitize_style_dna(supplied_style_dna)
    style_dna = {}
    insights = getattr(cluster, "_prefetched_objects_cache", {}).get("competitor_insights")
    if insights is None:
        insights = cluster.competitor_insights.order_by("created_at", "id")
    for insight in insights:
        style_dna.update(_sanitize_style_dna(insight.style_dna))
    return style_dna


def _reference_snapshot(cluster):
    cluster_assets = getattr(cluster, "_prefetched_objects_cache", {}).get("cluster_assets")
    if cluster_assets is None:
        cluster_assets = cluster.cluster_assets.select_related("asset").order_by("order", "id")
    return [
        item.asset.storage_path
        for item in cluster_assets
        if item.asset.kind == Asset.Kind.IMAGE
    ]


def target_consumer_for_cluster(cluster):
    if cluster.target_consumer.strip():
        return cluster.target_consumer.strip().lower()
    product_text = f"{cluster.product_name} {cluster.product_facts}".lower()
    if any(keyword in product_text for keyword in INFANT_KEYWORDS):
        return "baby"
    if any(keyword in product_text for keyword in ADULT_KEYWORDS):
        return "adult"
    return "adult"


def _template_snapshot(template, slot):
    return {
        "id": str(template.id),
        "name": template.name,
        "version": template.version,
        "platform": template.platform,
        "site": template.site,
        "slot": {"id": str(slot.id), "name": slot.name, "order": slot.order, "purpose": slot.purpose},
    }


def _rule_snapshot(rule_profile):
    if rule_profile is None:
        return {}
    return {
        "id": str(rule_profile.id),
        "name": rule_profile.name,
        "version": rule_profile.version,
        "platform": rule_profile.platform,
        "site": rule_profile.site,
        "rules": rule_profile.rules,
    }


def _published_prompt_node(node_name):
    return (
        PromptNodeTemplate.objects.filter(
            node_name=node_name,
            status=PromptNodeTemplate.Status.PUBLISHED,
        )
        .order_by("-updated_at", "-created_at", "-id")
        .first()
    )


def _prompt_node(node_name, node_template=_UNSET):
    if node_template is _UNSET:
        node_template = _published_prompt_node(node_name)
    if node_template is None:
        return node_name, "builtin-v1", ""
    return node_template.node_name, node_template.version, node_template.instruction


def compile_slot_prompt(
    cluster,
    slot,
    *,
    batch=None,
    template=None,
    provider_model="gpt-image-2",
    style_dna=None,
    node_name="slot_prompt",
    node_template=_UNSET,
):
    batch = batch or cluster.batch
    template = template or slot.template
    market = batch.market or batch.site
    size = batch.size or template.default_size
    resolution = batch.resolution or template.default_resolution
    consumer = target_consumer_for_cluster(cluster)
    sanitized_style_dna = _cluster_style_dna(cluster, style_dna)
    template_snapshot = _template_snapshot(template, slot)
    rule_snapshot = _rule_snapshot(batch.rule_profile)
    references = _reference_snapshot(cluster)
    resolved_node_name, node_version, node_instruction = _prompt_node(node_name, node_template)
    product_name = cluster.product_name or "not provided"
    product_facts = cluster.product_facts or "not provided"
    identity_lock = cluster.identity_lock or "Preserve visible product identity; do not change unprovided attributes."
    global_requirements = batch.global_prompt or "not provided"
    creative_requirements = cluster.prompt_override or "not provided"
    scene = sanitized_style_dna.get("scene_density") or slot.purpose or "not specified"
    composition = sanitized_style_dna.get("composition") or slot.purpose or "not specified"
    lighting = sanitized_style_dna.get("lighting") or "not specified"
    material = "Use only material explicitly stated in Product facts; otherwise not specified."
    prompt_lines = [
        "Create one ecommerce product image using only the supplied product information and references.",
        f"Product name: {product_name}",
        f"Product facts: {product_facts}",
        f"Global requirements: {global_requirements}",
        f"Identity lock: {identity_lock}",
        f"Creative requirements: {creative_requirements}",
        f"Market: {market or 'not provided'}",
        f"Model persona: {consumer}",
        f"Slot purpose: {slot.purpose or 'not provided'}",
        f"Rules: {json.dumps(rule_snapshot.get('rules', {}), ensure_ascii=False, sort_keys=True)}",
        f"Scene: {scene}",
        "Grounding: Do not invent product facts, claims, dimensions, certification, material, logos, text, or parts not provided above.",
        f"Composition: {composition}",
        f"Lighting: {lighting}",
        f"Material: {material}",
        f"Style DNA: {json.dumps(sanitized_style_dna, ensure_ascii=False, sort_keys=True)}",
        f"Size: {size}",
        f"Resolution: {resolution}",
    ]
    if node_instruction:
        prompt_lines.append(f"Node instruction: {node_instruction}")
    input_snapshot = {
        "market": market,
        "product_name": cluster.product_name,
        "product_facts": cluster.product_facts,
        "identity_lock": cluster.identity_lock,
        "global_requirements": batch.global_prompt,
        "creative_requirements": cluster.prompt_override,
        "slot_purpose": slot.purpose,
        "target_consumer": consumer,
        "style_dna": sanitized_style_dna,
        "rule_snapshot": rule_snapshot,
        "template_snapshot": template_snapshot,
        "reference_snapshot": references,
        "size": size,
        "resolution": resolution,
    }
    prompt, input_snapshot = apply_standard_product_hero_policy(slot, "\n".join(prompt_lines), input_snapshot)
    return {
        "node_name": resolved_node_name,
        "template_version": node_version,
        "provider_model": provider_model,
        "target_consumer": consumer,
        "model_persona": consumer,
        "prompt": prompt,
        "reference_snapshot": references,
        "style_dna": sanitized_style_dna,
        "template_snapshot": template_snapshot,
        "rule_snapshot": rule_snapshot,
        "input_snapshot": input_snapshot,
        "evaluation": {"fact_policy": "user-provided-only"},
        "size": size,
        "resolution": resolution,
    }


def _used_generations_today(user=None):
    today = timezone.localdate()
    queryset = Generation.objects.exclude(status=Generation.Status.CANCELED).filter(created_at__date=today)
    if user is not None:
        queryset = queryset.filter(created_by=user)
    return queryset.count()


def _locked_daily_usage(scope, user=None):
    today = timezone.localdate()
    lookup = {"scope": scope, "date": today, "user": user}
    seed = _used_generations_today(user if scope == DailyGenerationUsage.Scope.USER else None)
    usage, _ = DailyGenerationUsage.objects.get_or_create(
        **lookup,
        defaults={"used": seed},
    )
    return DailyGenerationUsage.objects.select_for_update().get(id=usage.id)


@transaction.atomic
def reserve_generation_usage(user, count):
    if count <= 0:
        return
    organization = _locked_daily_usage(DailyGenerationUsage.Scope.ORGANIZATION)
    personal = _locked_daily_usage(DailyGenerationUsage.Scope.USER, user)
    user_limit = user.daily_generation_limit or settings.USER_DAILY_GENERATION_LIMIT
    if organization.used + count > settings.ORG_DAILY_GENERATION_LIMIT:
        raise ValueError("organization daily quota exceeded")
    if personal.used + count > user_limit:
        raise ValueError("user daily quota exceeded")
    DailyGenerationUsage.objects.filter(id=organization.id).update(used=F("used") + count)
    DailyGenerationUsage.objects.filter(id=personal.id).update(used=F("used") + count)


def preflight_batch(batch, user, template=None):
    template = template or batch.output_template or _global_fallback_template()
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
    if not template.slots.filter(order=1).exists():
        blocking_errors.append("output template requires a standard product hero at order 1")
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
        "template": {
            "id": str(template.id),
            "name": template.name,
            "version": template.version,
        },
        "rule_profile": (
            {
                "id": str(batch.rule_profile_id),
                "name": batch.rule_profile.name,
                "version": batch.rule_profile.version,
            }
            if batch.rule_profile_id
            else None
        ),
    }


@transaction.atomic
def confirm_generation(batch, user, template=None):
    locked_batch = Batch.objects.select_for_update().select_related("output_template", "rule_profile").get(id=batch.id)
    existing = list(locked_batch.generations.order_by("created_at", "id"))
    if locked_batch.confirmed_generation_key and existing:
        return existing

    template = template or locked_batch.output_template or _global_fallback_template()
    if template.status != OutputTemplate.Status.PUBLISHED:
        raise ValueError("output template must be published before generation")
    if locked_batch.rule_profile_id and locked_batch.rule_profile.status != RuleProfile.Status.PUBLISHED:
        raise ValueError("rule profile must be published before generation")
    preflight = preflight_batch(locked_batch, user, template)
    if preflight["blocking_errors"]:
        raise ValueError(", ".join(preflight["blocking_errors"]))
    reserve_generation_usage(user, preflight["generation_count"])

    if locked_batch.output_template_id != template.id:
        locked_batch.output_template = template
    if not locked_batch.market:
        locked_batch.market = locked_batch.site
    if not locked_batch.size:
        locked_batch.size = template.default_size
    if not locked_batch.resolution:
        locked_batch.resolution = template.default_resolution

    slots = list(template.slots.order_by("order", "id"))
    clusters = list(
        locked_batch.clusters.prefetch_related(
            Prefetch(
                "cluster_assets",
                queryset=ClusterAsset.objects.select_related("asset").order_by("order", "id"),
            ),
            Prefetch(
                "competitor_insights",
                queryset=CompetitorInsight.objects.order_by("created_at", "id"),
            ),
        ).order_by("created_at", "id")
    )
    node_template = _published_prompt_node("slot_prompt")
    for cluster in clusters:
        for slot in slots:
            compiled = compile_slot_prompt(
                cluster,
                slot,
                batch=locked_batch,
                template=template,
                node_template=node_template,
            )
            prompt_version = PromptVersion.objects.create(
                cluster=cluster,
                created_by=user,
                node_name=compiled["node_name"],
                template_version=compiled["template_version"],
                provider_model=compiled["provider_model"],
                prompt_text=compiled["prompt"],
                input_snapshot=compiled["input_snapshot"],
                structured_output=compiled,
                evaluation=compiled["evaluation"],
                source_snapshot=compiled["input_snapshot"],
            )
            Generation.objects.create(
                batch=locked_batch,
                cluster=cluster,
                output_slot=slot,
                prompt_version=prompt_version,
                created_by=user,
                prompt_text=compiled["prompt"],
                size=compiled["size"],
                resolution=compiled["resolution"],
                reference_snapshot=compiled["reference_snapshot"],
                template_snapshot=compiled["template_snapshot"],
                rule_snapshot=compiled["rule_snapshot"],
            )

    locked_batch.confirmed_generation_key = uuid.uuid4()
    locked_batch.status = Batch.Status.QUEUED
    locked_batch.save(
        update_fields=[
            "output_template",
            "market",
            "size",
            "resolution",
            "confirmed_generation_key",
            "status",
            "updated_at",
        ]
    )
    return list(locked_batch.generations.order_by("created_at", "id"))


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
    queued = None
    rejected_queued = False
    queued_candidates = (
        Generation.objects.select_related("batch", "batch__owner", "cluster", "output_slot__template", "prompt_version")
        .filter(status=Generation.Status.QUEUED)
        .order_by("created_at", "id")
    )
    for candidate in queued_candidates:
        if candidate.output_slot.order == 1:
            queued = candidate
            break
        hero = (
            Generation.objects.filter(
                batch_id=candidate.batch_id,
                cluster_id=candidate.cluster_id,
                output_slot__template_id=candidate.output_slot.template_id,
                output_slot__order=1,
            )
            .order_by("-attempt", "-created_at", "-id")
            .first()
        )
        if hero is None:
            candidate.status = Generation.Status.FAILED
            candidate.failure_reason = "A completed standard product hero is required before detail outputs can be generated"
            candidate.save(update_fields=["status", "failure_reason", "updated_at"])
            candidate.batch.recompute_status()
            rejected_queued = True
            continue
        if hero.status != Generation.Status.COMPLETED:
            continue
        queued = candidate
        break

    if queued is not None:
        prompt_version, prompt_text = _ensure_generation_prompt_policy(queued, queued.created_by)
        if prompt_version_id := getattr(prompt_version, "id", None):
            if queued.prompt_version_id != prompt_version_id or queued.prompt_text != prompt_text:
                queued._replace_prompt_version_for_policy(prompt_version, prompt_text)
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
        return 1 if rejected_queued else 0

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


ACTIVE_GENERATION_STATUSES = {
    Generation.Status.QUEUED,
    Generation.Status.PREPARING,
    Generation.Status.SUBMITTING,
    Generation.Status.SUBMITTED,
    Generation.Status.PROCESSING,
    Generation.Status.ARCHIVING,
}


def _ensure_generation_prompt_policy(generation, user):
    """Keep a paid generation and its immutable PromptVersion on the same enforced prompt."""
    previous = generation.prompt_version
    if previous is None:
        input_snapshot = {
            "product_facts": generation.cluster.product_facts,
            "identity_lock": generation.cluster.identity_lock,
            "reference_snapshot": copy.deepcopy(generation.reference_snapshot),
        }
        source_snapshot = copy.deepcopy(input_snapshot)
    else:
        input_snapshot = copy.deepcopy(previous.input_snapshot)
        source_snapshot = copy.deepcopy(previous.source_snapshot)

    prompt_text, input_snapshot = apply_standard_product_hero_policy(
        generation.output_slot,
        previous.prompt_text if previous else generation.prompt_text,
        input_snapshot,
    )
    if previous is None and not input_snapshot.get("standard_product_hero"):
        return None, prompt_text
    if previous is not None and prompt_text == previous.prompt_text and input_snapshot == previous.input_snapshot:
        return previous, prompt_text

    _, source_snapshot = apply_standard_product_hero_policy(
        generation.output_slot,
        prompt_text,
        source_snapshot,
    )
    structured_output = copy.deepcopy(previous.structured_output if previous else {})
    structured_output["prompt"] = prompt_text
    prompt_version = PromptVersion.objects.create(
        cluster=generation.cluster,
        created_by=user or generation.created_by or generation.batch.owner,
        node_name=previous.node_name if previous else "slot_prompt",
        template_version=previous.template_version if previous else "builtin-v1",
        provider_model=previous.provider_model if previous else "gpt-image-2",
        prompt_text=prompt_text,
        input_snapshot=input_snapshot,
        structured_output=structured_output,
        evaluation=copy.deepcopy(previous.evaluation if previous else {}),
        source_snapshot=source_snapshot,
    )
    return prompt_version, prompt_text


def _create_followup_attempt(source, user, **overrides):
    siblings = Generation.objects.filter(
        cluster_id=source.cluster_id,
        output_slot_id=source.output_slot_id,
    ).exclude(id=source.id)
    if siblings.filter(attempt__gt=source.attempt).exists() or siblings.filter(
        status__in=ACTIVE_GENERATION_STATUSES
    ).exists():
        raise ValueError("A newer generation attempt already exists")
    next_attempt = (
        Generation.objects.filter(
            cluster_id=source.cluster_id,
            output_slot_id=source.output_slot_id,
        ).aggregate(value=Max("attempt"))["value"]
        or 0
    ) + 1
    reserve_generation_usage(user, 1)
    values = {
        "batch": source.batch,
        "cluster": source.cluster,
        "output_slot": source.output_slot,
        "prompt_version": source.prompt_version,
        "created_by": user,
        "attempt": next_attempt,
        "status": Generation.Status.QUEUED,
        "prompt_text": source.prompt_text,
        "size": source.size,
        "resolution": source.resolution,
        "reference_snapshot": copy.deepcopy(source.reference_snapshot),
        "template_snapshot": copy.deepcopy(source.template_snapshot),
        "rule_snapshot": copy.deepcopy(source.rule_snapshot),
    }
    values.update(overrides)
    followup = Generation.objects.create(**values)
    prompt_version, prompt_text = _ensure_generation_prompt_policy(followup, user)
    if prompt_version_id := getattr(prompt_version, "id", None):
        if followup.prompt_version_id != prompt_version_id or followup.prompt_text != prompt_text:
            followup._replace_prompt_version_for_policy(prompt_version, prompt_text)
    return followup


@transaction.atomic
def retry_failed_generation(generation, user):
    Batch.objects.select_for_update().get(id=generation.batch_id)
    Cluster.objects.select_for_update().get(id=generation.cluster_id)
    locked = (
        Generation.objects.select_for_update()
        .select_related("batch", "cluster", "output_slot", "prompt_version")
        .get(id=generation.id)
    )
    if locked.status not in {Generation.Status.FAILED, Generation.Status.CANCELED}:
        raise ValueError("Only failed or canceled generations can be retried")
    retry = _create_followup_attempt(locked, user)
    Batch.objects.filter(id=locked.batch_id).update(
        status=Batch.Status.QUEUED,
        updated_at=timezone.now(),
    )
    return retry


def _coordinate(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError("Annotation coordinates must be numbers from 0 to 1")
    return float(value)


def _normalize_annotations(annotations):
    if not isinstance(annotations, list):
        raise ValueError("annotations must be a list")
    normalized = []
    for annotation in annotations:
        if not isinstance(annotation, dict):
            raise ValueError("annotation must be an object")
        kind = annotation.get("kind")
        if kind not in ReviewAnnotation.Kind.values:
            raise ValueError("annotation kind must be stroke or circle")
        points = []
        rect = []
        if kind == ReviewAnnotation.Kind.STROKE:
            raw_points = annotation.get("points")
            if not isinstance(raw_points, list) or len(raw_points) < 2:
                raise ValueError("stroke annotation requires at least two points")
            for point in raw_points:
                if isinstance(point, dict):
                    point = [point.get("x"), point.get("y")]
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise ValueError("annotation point must contain x and y")
                points.append([_coordinate(point[0]), _coordinate(point[1])])
        else:
            raw_rect = annotation.get("rect")
            if isinstance(raw_rect, dict):
                raw_rect = [
                    raw_rect.get("x"),
                    raw_rect.get("y"),
                    raw_rect.get("width"),
                    raw_rect.get("height"),
                ]
            if not isinstance(raw_rect, (list, tuple)) or len(raw_rect) != 4:
                raise ValueError("circle annotation requires x, y, width, and height")
            rect = [_coordinate(value) for value in raw_rect]
            if rect[2] <= 0 or rect[3] <= 0 or rect[0] + rect[2] > 1 or rect[1] + rect[3] > 1:
                raise ValueError("circle annotation must fit within the image")
        color = annotation.get("color", "#ff0000")
        if not isinstance(color, str) or not color.strip() or len(color) > 32:
            raise ValueError("annotation color is invalid")
        width = annotation.get("width", 2)
        if isinstance(width, bool) or not isinstance(width, (int, float)) or not 0 < width <= 64:
            raise ValueError("annotation width must be between 0 and 64")
        normalized.append(
            {
                "kind": kind,
                "points": points,
                "rect": rect,
                "color": color.strip(),
                "width": float(width),
            }
        )
    return normalized


def _prompt_version_references(prompt_version):
    for snapshot in (prompt_version.input_snapshot, prompt_version.source_snapshot):
        for key in ("reference_snapshot", "self_product_references", "product_references"):
            references = snapshot.get(key)
            if isinstance(references, list) and all(isinstance(item, str) for item in references):
                return copy.deepcopy(references)
    return []


@transaction.atomic
def review_generation(generation, reviewer, *, decision, issue_tags=None, description="", annotations=None):
    Batch.objects.select_for_update().get(id=generation.batch_id)
    Cluster.objects.select_for_update().get(id=generation.cluster_id)
    locked = (
        Generation.objects.select_for_update()
        .select_related("prompt_version", "cluster", "output_slot", "batch")
        .get(id=generation.id)
    )
    if locked.status != Generation.Status.COMPLETED:
        raise ValueError("Only completed generations can be reviewed")
    if decision not in ReviewFeedback.Decision.values:
        raise ValueError("decision must be accept or changes_requested")
    if hasattr(locked, "review_feedback"):
        raise ValueError("Generation has already been reviewed")
    if not isinstance(issue_tags, list):
        raise ValueError("issue_tags must be a list")
    if not isinstance(description, str):
        raise ValueError("description must be a string")
    normalized_tags = sorted(
        {
            tag.strip().lower()
            for tag in issue_tags
            if isinstance(tag, str) and tag.strip()
        }
    )
    if len(normalized_tags) != len(
        {tag.strip().lower() for tag in issue_tags if isinstance(tag, str) and tag.strip()}
    ) or any(not isinstance(tag, str) for tag in issue_tags):
        raise ValueError("issue_tags must contain strings")
    normalized_annotations = _normalize_annotations(annotations or [])
    description = description.strip()
    if (
        decision == ReviewFeedback.Decision.CHANGES_REQUESTED
        and not description
        and not normalized_tags
    ):
        raise ValueError("changes_requested requires a description or issue tag")

    feedback = ReviewFeedback.objects.create(
        generation=locked,
        reviewer=reviewer,
        decision=decision,
        issue_tags=normalized_tags,
        description=description,
    )
    ReviewAnnotation.objects.bulk_create(
        [ReviewAnnotation(feedback=feedback, **annotation) for annotation in normalized_annotations]
    )

    revision = None
    if decision == ReviewFeedback.Decision.ACCEPT:
        locked.review_status = Generation.ReviewStatus.ACCEPTED
        audit_action = "generation.accept"
    else:
        locked.review_status = Generation.ReviewStatus.CHANGES_REQUESTED
        audit_action = "generation.changes_requested"
        previous = locked.prompt_version
        revision_delta = {
            "issue_tags": normalized_tags,
            "description": description,
        }
        if previous:
            input_snapshot = copy.deepcopy(previous.input_snapshot)
            source_snapshot = copy.deepcopy(previous.source_snapshot)
            references = _prompt_version_references(previous)
            prior_prompt = previous.prompt_text
        else:
            references = copy.deepcopy(locked.reference_snapshot)
            input_snapshot = {
                "product_facts": locked.cluster.product_facts,
                "identity_lock": locked.cluster.identity_lock,
                "reference_snapshot": references,
            }
            source_snapshot = copy.deepcopy(input_snapshot)
            prior_prompt = locked.prompt_text
        input_snapshot["revision_delta"] = revision_delta
        source_snapshot["revision_delta"] = revision_delta
        structured_output = copy.deepcopy(previous.structured_output if previous else {})
        structured_output["revision_delta"] = {
            **revision_delta,
            "prior_prompt_version_id": str(previous.id) if previous else None,
        }
        delta_lines = [
            prior_prompt,
            "Revision request:",
            f"Issue tags: {', '.join(normalized_tags) or 'not provided'}",
            f"Description: {description or 'not provided'}",
        ]
        prompt_text, input_snapshot = apply_standard_product_hero_policy(
            locked.output_slot,
            "\n".join(delta_lines),
            input_snapshot,
        )
        _, source_snapshot = apply_standard_product_hero_policy(
            locked.output_slot,
            prompt_text,
            source_snapshot,
        )
        structured_output["prompt"] = prompt_text
        prompt_version = PromptVersion.objects.create(
            cluster=locked.cluster,
            created_by=reviewer,
            node_name=previous.node_name if previous else "slot_prompt",
            template_version=previous.template_version if previous else "builtin-v1",
            provider_model=previous.provider_model if previous else "gpt-image-2",
            prompt_text=prompt_text,
            input_snapshot=input_snapshot,
            structured_output=structured_output,
            evaluation=copy.deepcopy(previous.evaluation if previous else {}),
            source_snapshot=source_snapshot,
        )
        revision = _create_followup_attempt(
            locked,
            reviewer,
            prompt_version=prompt_version,
            prompt_text=prompt_version.prompt_text,
            reference_snapshot=references,
        )
        Batch.objects.filter(id=locked.batch_id).update(
            status=Batch.Status.QUEUED,
            updated_at=timezone.now(),
        )
    locked.save(update_fields=["review_status", "updated_at"])
    AuditEvent.objects.create(
        actor=reviewer,
        action=audit_action,
        object_type="generation",
        object_id=str(locked.id),
        metadata={"decision": decision, "revision_generation_id": str(revision.id) if revision else ""},
    )
    return feedback, revision


def safe_storage_path(storage_path, expected_prefix):
    if (
        not isinstance(storage_path, str)
        or not storage_path
        or "\\" in storage_path
        or "\x00" in storage_path
        or Path(storage_path).is_absolute()
    ):
        raise ValueError("Invalid storage path")
    relative = PurePosixPath(storage_path)
    prefix = PurePosixPath(expected_prefix)
    if ".." in relative.parts or relative.parts[: len(prefix.parts)] != prefix.parts:
        raise ValueError("Invalid storage path")
    root = Path(settings.MEDIA_ROOT).resolve()
    prefix_path = root / Path(*prefix.parts)
    if prefix_path.resolve() != prefix_path:
        raise ValueError("Invalid storage path")
    target = (root / Path(*relative.parts)).resolve()
    if not target.is_relative_to(prefix_path) or not target.is_file():
        raise ValueError("Stored file is unavailable")
    return target


def _generation_status(status):
    if status == Generation.Status.COMPLETED:
        return "completed"
    if status in {
        Generation.Status.FAILED,
        Generation.Status.SUBMIT_UNKNOWN,
        Generation.Status.CANCELED,
    }:
        return "failed"
    if status == Generation.Status.QUEUED:
        return "queued"
    return "running"


def generation_failure_message(generation):
    if generation.status == Generation.Status.SUBMIT_UNKNOWN:
        return "Generation status is uncertain. Contact an administrator before retrying."
    if generation.status in {Generation.Status.FAILED, Generation.Status.CANCELED}:
        return "Generation failed. Retry this item or contact an administrator."
    return ""


def _project_status(status):
    if status in {Batch.Status.COMPLETED, Batch.Status.ARCHIVED}:
        return "completed"
    if status in {Batch.Status.FAILED, Batch.Status.PARTIAL}:
        return "failed"
    if status == Batch.Status.QUEUED:
        return "queued"
    if status == Batch.Status.RUNNING:
        return "running"
    return "draft"


def serialize_project(batch):
    assets = list(batch.assets.order_by("created_at", "id"))
    serialized_assets = {
        asset.id: {
            "id": str(asset.id),
            "name": asset.original_filename,
            "kind": asset.kind,
            **(
                {"imageUrl": reverse("api_asset_media", args=[asset.id])}
                if asset.kind == Asset.Kind.IMAGE
                else {}
            ),
        }
        for asset in assets
    }
    template = batch.output_template or _global_fallback_template()
    template_slots = [
        {"order": slot.order, "name": slot.name, "purpose": slot.purpose}
        for slot in template.slots.order_by("order", "id")
    ]
    latest_imports = {}
    for item in batch.sku_import_items.order_by("sku", "-attempt"):
        latest_imports.setdefault(item.sku, item)
    sku_imports = [
        _serialize_sku_import_item(latest_imports[sku])
        for sku in sorted(latest_imports)
    ]
    skus = []
    for cluster in batch.clusters.order_by("created_at", "id"):
        cluster_assets = list(
            cluster.cluster_assets.select_related("asset").order_by("order", "id")
        )
        outputs = []
        for generation in cluster.generations.select_related("output_slot").prefetch_related(
            "result_assets"
        ).order_by("output_slot__order", "attempt", "id"):
            result = next(iter(generation.result_assets.all()), None)
            review_status = (
                Generation.ReviewStatus.CHANGES_REQUESTED
                if generation.review_status == Generation.ReviewStatus.REJECTED
                else generation.review_status
            )
            output = {
                "id": str(generation.id),
                "name": generation.output_slot.name,
                "slot": generation.output_slot.name,
                "slotId": str(generation.output_slot_id),
                "slotOrder": generation.output_slot.order,
                "attempt": generation.attempt,
                "version": generation.attempt,
                "status": _generation_status(generation.status),
                "reviewStatus": review_status,
            }
            failure_message = generation_failure_message(generation)
            if failure_message:
                output["failureReason"] = failure_message
            if result is not None:
                output["imageUrl"] = reverse("api_result_media", args=[result.id])
            outputs.append(output)
        sku_assets = [serialized_assets[item.asset_id] for item in cluster_assets]
        skus.append(
            {
                "id": str(cluster.id),
                "sku": cluster.sku or "",
                "name": cluster.product_name or cluster.name,
                "productName": cluster.product_name or cluster.name,
                "importStatus": (
                    latest_imports[cluster.sku].status if cluster.sku in latest_imports else "manual"
                ),
                "assetIds": [str(item.asset_id) for item in cluster_assets],
                "assets": sku_assets,
                "facts": cluster.product_facts,
                "identityLock": cluster.identity_lock,
                "brief": cluster.prompt_override,
                "outputs": outputs,
            }
        )
    preflight = preflight_batch(batch, batch.owner, template)
    return {
        "id": str(batch.id),
        "name": batch.name,
        "platform": batch.platform,
        "market": batch.market or batch.site,
        "template": template.name,
        "size": batch.size,
        "status": _project_status(batch.status),
        "updatedAt": batch.updated_at.isoformat(),
        "assets": list(serialized_assets.values()),
        "skus": skus,
        "skuImports": sku_imports,
        "templateSlots": template_slots,
        "preflight": {
            key: preflight[key]
            for key in (
                "cluster_count",
                "slot_count",
                "generation_count",
                "blocking_errors",
                "template",
                "rule_profile",
            )
        },
    }
