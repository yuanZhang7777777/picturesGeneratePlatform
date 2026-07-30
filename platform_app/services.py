import copy
import hashlib
import ipaddress
import json
import re
import uuid
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
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
from .storage import StorageError, get_object_storage, validate_storage_path
from .template_policy import (
    apply_standard_product_hero_policy,
    is_source_product_photo_slot,
    is_standard_product_hero_slot,
    standard_product_hero_slot,
)


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


class UploadError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class RateLimited(ProviderError):
    pass


class CatalogError(Exception):
    pass


class CatalogAuthExpired(CatalogError):
    pass


class ErpAuthError(Exception):
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


def _extract_token(data):
    token = data.get("accessToken") or data.get("token")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("missing token")
    return token.strip()


class ErpAuthClient:
    def __init__(self, session=None, timeout=None):
        self.session = session or requests.Session()
        self.timeout = timeout or settings.CATALOG_TIMEOUT_SECONDS

    def login(self, username, password):
        if not settings.ERP_LOGIN_URL:
            raise ErpAuthError("ERP login is not configured")
        try:
            response = self.session.post(
                settings.ERP_LOGIN_URL,
                json={"username": username, "password": password},
                timeout=self.timeout,
            )
            data = _catalog_response_data(response, dict)
            return _extract_token(data)
        except (CatalogError, ValueError, requests.RequestException) as exc:
            raise ErpAuthError("ERP login failed") from exc


def authenticate_erp_user(username, password, *, client=None):
    username = str(username or "").strip()
    password = str(password or "")
    if not username or not password:
        raise ErpAuthError("ERP login failed")
    token = (client or ErpAuthClient()).login(username, password)
    admin_names = {name.strip().lower() for name in settings.PLATFORM_ADMIN_ERP_USERS if name.strip()}
    role = get_user_model().Role.ADMIN if username.lower() in admin_names else get_user_model().Role.OPERATOR
    user, _ = get_user_model().objects.get_or_create(username=username, defaults={"role": role})
    changed = []
    if user.role != role:
        user.role = role
        changed.append("role")
    if user.must_change_password:
        user.must_change_password = False
        changed.append("must_change_password")
    is_staff = role == get_user_model().Role.ADMIN or user.is_superuser
    if user.is_staff != is_staff:
        user.is_staff = is_staff
        changed.append("is_staff")
    if user.has_usable_password():
        user.set_unusable_password()
        changed.append("password")
    if changed:
        user.save(update_fields=changed)
    return user, token


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
        self.backend = get_object_storage(root)

    @contextmanager
    def reference_paths(self, storage_paths):
        contexts = []
        paths = []
        try:
            for storage_path in storage_paths:
                context = self.backend.local_path(storage_path)
                path = context.__enter__()
                contexts.append(context)
                paths.append(str(path))
            yield paths
        finally:
            for context in reversed(contexts):
                context.__exit__(None, None, None)

    def read(self, storage_path):
        return self.backend.read(storage_path)

    def size(self, storage_path):
        return self.backend.size(storage_path)

    def save(self, storage_path, data):
        self.backend.save(storage_path, data)

    def delete(self, storage_path):
        self.backend.delete(storage_path)

    def archive_result(self, generation, source_url, data):
        image_format, width, height = _inspect_image(data)
        suffix = ".jpg" if image_format == "JPEG" else ".png"
        storage_path = (
            f"results/{generation.batch_id}/{generation.cluster_id}/"
            f"{generation.output_slot_id}/{generation.attempt}/{uuid.uuid4().hex}{suffix}"
        )
        self.save(storage_path, data)
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
    def upload_image(self, path):
        return f"fake://upload/{Path(path).name}"

    def submit_generation(self, prompt, image_paths, size, resolution):
        return f"fake-{uuid.uuid4().hex}"

    def get_task(self, task_id):
        return {"status": "completed", "image_urls": [f"fake://{task_id}/result.png"]}

    def download_image(self, url):
        buffer = BytesIO()
        Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
        return buffer.getvalue()

    def observe_images(self, instruction, image_paths):
        return {
            "output_text": json.dumps(
                {
                    "product_name": "Demo product",
                    "confidence": 0.9,
                    "product_facts": ["visible product reference"],
                    "identity_lock": "Preserve the visible product identity.",
                    "target_consumer": "adult",
                }
            ),
            "raw": {},
        }

    def optimize_prompt(self, payload):
        text = payload.get("text", "")
        try:
            node_input = json.loads(text.splitlines()[-1])
        except (IndexError, TypeError, ValueError, json.JSONDecodeError):
            node_input = {}
        if "NODE N2" in text:
            observations = node_input.get("observations", [])
            primary = observations[0].get("asset_id") if observations else None
            observed = observations[0] if observations else {}
            observed_facts = _string_list(observed.get("product_facts") or observed.get("facts"))
            output = {
                "decision": "continue",
                "confidence": 90,
                "product_name": node_input.get("product_name") or "Demo product",
                "product_profile": {
                    "category": "product",
                    "primary_appearance": "; ".join(observed_facts) or "visible reference",
                },
                "identity_lock": {
                    "must_not_change": [
                        str(observed.get("identity_lock") or "visible product identity")
                    ]
                },
                "primary_asset_id": primary,
                "supporting_asset_ids": [],
            }
        elif "NODE N3" in text:
            output = {
                "ledger_version": "2.0.0",
                "facts": [
                    {
                        "fact_id": "fact.name.001",
                        "statement": node_input.get("product_name") or "Demo product",
                        "fact_class": "confirmed",
                        "confidence": 1,
                        "evidence_refs": ["product_name"],
                        "risk_level": "low",
                        "allowed_uses": ["identity", "visual_prompt", "consumer_copy"],
                    }
                ],
                "blocked_claim_topics": ["price", "certification", "medical_efficacy"],
                "unresolved_questions": [],
            }
        elif "NODE N4" in text:
            output = {
                "main_scene": "pure white commercial studio",
                "main_action": "none",
                "visible_text_lines": [],
                "prompt": "Show the complete accurate product on pure white.",
            }
        elif "NODE N5" in text:
            output = {
                "plans": [
                    {
                        "slot_order": slot["slot_order"],
                        "scene_family": f"scene-{slot['slot_order']}",
                        "conversion_goal": slot.get("purpose", ""),
                        "main_scene": f"distinct scene {slot['slot_order']}",
                        "main_action": "none",
                        "visible_text_lines": [],
                    }
                    for slot in node_input.get("slots", [])
                ]
            }
        elif "NODE N6" in text:
            slot_order = node_input.get("slot_order")
            output = {
                "slot_order": slot_order,
                "main_scene": node_input.get("slot_plan", {}).get("main_scene", "clean ecommerce scene"),
                "main_action": node_input.get("slot_plan", {}).get("main_action", "none"),
                "visible_text_lines": node_input.get("slot_plan", {}).get("visible_text_lines", []),
                "prompt": f"Create demo ecommerce product image slot {slot_order}.",
            }
        else:
            output = {"suggested_prompt": text}
        return {"output_text": json.dumps(output), "raw": {}}


class APIMartClient:
    def __init__(self, session=None, timeout=60):
        self.session = session or requests.Session()
        self.timeout = timeout

    @property
    def auth_headers(self):
        if not settings.APIMART_API_KEY:
            raise ProviderError("APIMart API key is not configured")
        return {"Authorization": f"Bearer {settings.APIMART_API_KEY}"}

    @property
    def headers(self):
        return {
            **self.auth_headers,
            "Content-Type": "application/json",
        }

    def _json(self, response):
        if response.status_code >= 400:
            _raise_provider_error(response)
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("provider returned invalid JSON") from exc

    def _url(self, path):
        base = settings.APIMART_BASE_URL.rstrip("/")
        if base.endswith("/v1") and path.startswith("/v1/"):
            path = path[3:]
        return f"{base}{path}"

    def _api_url(self, path):
        base = settings.APIMART_BASE_URL.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return f"{base}{path}"

    def upload_image(self, path):
        try:
            with Path(path).open("rb") as handle:
                response = self.session.post(
                    self._url("/v1/uploads/images"),
                    headers=self.auth_headers,
                    files={"file": handle},
                    timeout=self.timeout,
                )
        except requests.RequestException as exc:
            raise ProviderError(_sanitize_provider_text(str(exc))) from exc
        url = self._json(response).get("url")
        if not isinstance(url, str) or not url:
            raise ProviderError("provider upload response did not include url")
        return url

    def _uploaded_image_urls(self, image_paths):
        return [self.upload_image(path) for path in image_paths]

    def submit_generation(self, prompt, image_paths, size, resolution):
        payload = {
            "model": settings.APIMART_IMAGE_MODEL,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "resolution": resolution,
            "official_fallback": False,
        }
        if image_paths:
            payload["image_urls"] = self._uploaded_image_urls(image_paths)

        try:
            response = self.session.post(
                self._url("/v1/images/generations"),
                json=payload,
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise SubmitUnknown("generation submit timed out") from exc
        except requests.RequestException as exc:
            raise SubmitUnknown(_sanitize_provider_text(str(exc))) from exc

        payload = self._json(response)
        data = payload.get("data")
        first = data[0] if isinstance(data, list) and data else data
        task_id = (
            first.get("task_id") or first.get("id")
            if isinstance(first, dict)
            else payload.get("task_id") or payload.get("id")
        )
        if not task_id:
            raise ProviderError("provider response did not include task_id")
        return task_id

    def get_task(self, task_id):
        response = self.session.get(
            self._url(f"/v1/tasks/{task_id}"),
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

    def complete_chat(self, messages, *, model=None):
        temperature = settings.APIMART_PROMPT_TEMPERATURE
        if temperature < 0 or temperature > 2:
            raise ProviderError("APIMart prompt temperature must be between 0 and 2")
        response = self.session.post(
            self._api_url("/api/v1/chat/completions"),
            json={
                "model": model or settings.APIMART_PROMPT_MODEL,
                "stream": False,
                "temperature": temperature,
                "messages": messages,
            },
            headers=self.headers,
            timeout=self.timeout,
        )
        payload = self._json(response)
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("provider chat response did not include choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ProviderError("provider chat response did not include message content")
        return {"output_text": content, "raw": payload}

    def _responses_output_text(self, payload):
        if isinstance(payload.get("output_text"), str) and payload["output_text"]:
            return payload["output_text"]
        parts = []
        for item in payload.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    parts.append(content["text"])
        return "\n".join(parts)

    def observe_images(self, instruction, image_paths):
        content = [{"type": "input_text", "text": instruction}]
        content.extend(
            {"type": "input_image", "image_url": url}
            for url in self._uploaded_image_urls(image_paths)
        )
        response = self.session.post(
            self._url("/v1/responses"),
            json={
                "model": settings.APIMART_VISION_MODEL,
                "input": [{"role": "user", "content": content}],
            },
            headers=self.headers,
            timeout=self.timeout,
        )
        payload = self._json(response)
        return {"output_text": self._responses_output_text(payload), "raw": payload}

    def optimize_prompt(self, payload):
        text = payload.get("text", "")
        return self.complete_chat(
            [
                {
                    "role": "system",
                    "content": payload.get("system")
                    or "Return only valid JSON for ecommerce image prompt planning.",
                },
                {"role": "user", "content": text},
            ]
        )


class CatalogClient:
    """The catalog boundary; imported fields stay limited to SKU, name, and image URL."""

    def __init__(self, token=None, session=None, timeout=None):
        self.session = session or requests.Session()
        self.timeout = timeout or settings.CATALOG_TIMEOUT_SECONDS
        self._token = str(token or "").strip()

    def fetch_products(self, skus):
        if not self._token:
            raise CatalogAuthExpired("ERP login expired")
        if not settings.CATALOG_QUERY_URL:
            raise CatalogError("Catalog service is not configured")
        try:
            response = self.session.post(
                settings.CATALOG_QUERY_URL,
                json={"skuList": skus},
                headers={"Authorization": self._token},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CatalogError("Catalog service is unavailable") from exc
        if response.status_code in {401, 403}:
            raise CatalogAuthExpired("ERP login expired")
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


def _default_output_template(platform, market, seller_tier):
    if platform == "shopee" and market == "VN" and seller_tier == Batch.SellerTier.GENERAL:
        template = OutputTemplate.objects.filter(
            seed_key="shopee-vn-general-nine-slot-v2-template",
            status=OutputTemplate.Status.PUBLISHED,
        ).first()
        if template is not None:
            return template
    return _global_fallback_template()


def _default_rule_profile(platform, market):
    regional = RuleProfile.objects.filter(
        platform=platform,
        site=market,
        status=RuleProfile.Status.PUBLISHED,
    ).order_by("-checked_at", "-version", "-id").first()
    if regional is not None:
        return regional
    platform_baseline = RuleProfile.objects.filter(
        platform=platform,
        site="",
        status=RuleProfile.Status.PUBLISHED,
    ).order_by("-checked_at", "-version", "-id").first()
    if platform_baseline is not None:
        return platform_baseline
    return (
        RuleProfile.objects.filter(
            seed_key="global-marketplace-prompt-os-v2-rule",
            status=RuleProfile.Status.PUBLISHED,
        ).first()
        or RuleProfile.objects.filter(
            platform="global",
            site="",
            status=RuleProfile.Status.PUBLISHED,
        ).order_by("-version", "-id").first()
    )


def create_project(
    owner,
    *,
    name,
    platform="shopee",
    market="SG",
    seller_tier="general",
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
    market = str(market or "SG").strip().upper()
    seller_tier = str(seller_tier or Batch.SellerTier.GENERAL).strip().lower()
    if platform != "shopee":
        seller_tier = Batch.SellerTier.GENERAL
    if seller_tier not in Batch.SellerTier.values:
        raise ValueError("seller_tier must be general or mall")
    output_template = _published_configuration(
        OutputTemplate,
        template,
        platform=platform,
        site=market,
    )
    if output_template is None:
        output_template = _default_output_template(platform, market, seller_tier)
    rules = _published_configuration(
        RuleProfile,
        rule_profile,
        platform=platform,
        site=market,
    )
    if rules is None:
        rules = _default_rule_profile(platform, market)
    return Batch.objects.create(
        owner=owner,
        name=name,
        platform=platform,
        site=market,
        market=market,
        seller_tier=seller_tier,
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
    try:
        LocalStorage().save(object_name, data)
    except (OSError, StorageError):
        LocalStorage().delete(object_name)
        raise
    return object_name


def _decode_txt(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UploadError("invalid_encoding", "TXT 必须使用 UTF-8 编码") from exc


def _normalize_image(data, suffix):
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise UploadError("unsupported_format", "仅支持 JPEG、PNG、WebP 图片和 UTF-8 TXT")
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise UploadError("unsupported_format", "仅支持 JPEG、PNG、WebP 图片和 UTF-8 TXT")
            if image.format == "WEBP":
                if getattr(image, "is_animated", False):
                    raise UploadError("unsupported_format", "暂不支持动态 WebP")
                normalized = BytesIO()
                image.convert("RGBA" if "A" in image.getbands() else "RGB").save(normalized, "PNG")
                return normalized.getvalue(), "PNG", image.width, image.height
            return data, image.format, image.width, image.height
    except UploadError:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise UploadError("invalid_image", "图片文件损坏或内容与扩展名不一致") from exc


def _inspect_image(data):
    normalized, image_format, width, height = _normalize_image(data, ".png")
    return image_format, width, height


def _refresh_batch_seed_prompt(batch):
    prompt = "\n\n".join(
        text.strip()
        for text in batch.assets.filter(kind=Asset.Kind.TXT)
        .order_by("original_filename", "id")
        .values_list("text_content", flat=True)
        if text.strip()
    )
    if batch.global_prompt != prompt:
        batch.global_prompt = prompt
        batch.save(update_fields=["global_prompt", "updated_at"])


def _normalize_import_mode(mode):
    mode = str(mode or Batch.ImportMode.ORGANIZE).strip()
    if mode not in Batch.ImportMode.values:
        raise ValueError("mode must be auto or organize")
    return mode


@transaction.atomic
def request_cluster_preparation(cluster, *, auto_generate):
    locked = Cluster.objects.select_for_update().get(id=cluster.id)
    locked.auto_generate = bool(auto_generate)
    locked.preparation_status = Cluster.PreparationStatus.PENDING
    locked.preparation_error = ""
    locked.save(
        update_fields=[
            "auto_generate",
            "preparation_status",
            "preparation_error",
            "updated_at",
        ]
    )
    return locked


@transaction.atomic
def register_uploaded_asset(batch, filename, content, content_type, *, mode=None):
    mode = _normalize_import_mode(mode or batch.last_import_mode)
    data = _bytes(content)
    suffix = Path(filename).suffix.lower()

    if suffix == ".txt":
        if len(data) > MAX_TXT_BYTES:
            raise UploadError("file_too_large", "TXT 不能超过 256 KiB")
        text = _decode_txt(data)
        storage_path = _store(batch, filename, data)
        asset = Asset.objects.create(
            batch=batch,
            kind=Asset.Kind.TXT,
            original_filename=filename,
            storage_path=storage_path,
            sha256=hashlib.sha256(data).hexdigest(),
            file_size=len(data),
            content_type="text/plain",
            text_content=text,
        )
        _refresh_batch_seed_prompt(batch)
        return asset

    if len(data) > MAX_IMAGE_BYTES:
        raise UploadError("file_too_large", "图片不能超过 20 MiB")

    data, image_format, width, height = _normalize_image(data, suffix)
    content_type = "image/jpeg" if image_format == "JPEG" else "image/png"
    storage_filename = str(PurePosixPath(filename).with_suffix(".png")) if image_format == "PNG" else filename
    storage_path = _store(batch, storage_filename, data)
    asset = Asset.objects.create(
        batch=batch,
        kind=Asset.Kind.IMAGE,
        original_filename=filename,
        storage_path=storage_path,
        sha256=hashlib.sha256(data).hexdigest(),
        file_size=len(data),
        content_type=content_type,
        width=width,
        height=height,
    )
    cluster = Cluster.create_for_asset(batch=batch, asset=asset)
    request_cluster_preparation(cluster, auto_generate=mode == Batch.ImportMode.AUTO)
    if batch.status == Batch.Status.DRAFT or batch.last_import_mode != mode:
        batch.status = Batch.Status.ORGANIZING
        batch.last_import_mode = mode
        batch.save(update_fields=["status", "last_import_mode", "updated_at"])
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
        LocalStorage().delete(storage_path)
    except (OSError, StorageError):
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
    return False


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


def import_skus(batch, skus, *, erp_token=None, catalog_client=None, image_downloader=None, mode=None):
    mode = _normalize_import_mode(mode or batch.last_import_mode)
    if not isinstance(skus, list):
        raise ValueError("skus must be an array")
    if len(skus) > settings.CATALOG_MAX_SKUS_PER_REQUEST:
        raise ValueError(f"at most {settings.CATALOG_MAX_SKUS_PER_REQUEST} SKUs are allowed")
    clean_skus = list(dict.fromkeys(str(sku).strip() for sku in skus if str(sku).strip()))
    if not clean_skus:
        raise ValueError("at least one SKU is required")
    if any(len(sku) > 120 for sku in clean_skus):
        raise ValueError("SKU is too long")

    catalog_client = catalog_client or CatalogClient(token=erp_token)
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
    except CatalogAuthExpired:
        raise
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
                            request_cluster_preparation(cluster, auto_generate=mode == Batch.ImportMode.AUTO)
                            if locked_batch.status == Batch.Status.DRAFT:
                                locked_batch.status = Batch.Status.ORGANIZING
                        else:
                            request_cluster_preparation(cluster, auto_generate=mode == Batch.ImportMode.AUTO)
                        if locked_batch.status == Batch.Status.DRAFT or locked_batch.last_import_mode != mode:
                            locked_batch.status = Batch.Status.ORGANIZING
                            locked_batch.last_import_mode = mode
                            locked_batch.save(update_fields=["status", "last_import_mode", "updated_at"])
                        if product_name and cluster.product_name != product_name:
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
        except (OSError, DatabaseError, StorageError):
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
        "global-marketplace-nine-slot-template",
        "global-marketplace-baseline-template",
    ):
        template = OutputTemplate.objects.filter(
            platform="global",
            site="",
            status=OutputTemplate.Status.PUBLISHED,
            seed_key=seed_key,
            slots__order=9,
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


def _slot_scope(slot):
    if is_source_product_photo_slot(slot):
        return "source"
    return "cover" if is_standard_product_hero_slot(slot) else "gallery"


def _applicable_rules(batch, slot):
    profiles = getattr(batch, "_prompt_rule_profiles", None)
    if profiles is None:
        profiles = []
        internal = RuleProfile.objects.filter(
            seed_key="global-marketplace-prompt-os-v2-rule",
            status=RuleProfile.Status.PUBLISHED,
        ).first()
        platform_baseline = RuleProfile.objects.filter(
            platform=batch.platform,
            site="",
            status=RuleProfile.Status.PUBLISHED,
        ).order_by("-checked_at", "-version", "-id").first()
        for profile in (internal, platform_baseline, batch.rule_profile):
            if profile is not None and profile.id not in {item.id for item in profiles}:
                profiles.append(profile)
        batch._prompt_rule_profiles = profiles
    market = (batch.market or batch.site or "").upper()
    scope = _slot_scope(slot)
    allowed_tiers = {"general", batch.seller_tier}
    rules = []
    seen = set()
    for profile in profiles:
        if not isinstance(profile.rules, list):
            continue
        for item in profile.rules:
            if not isinstance(item, dict):
                continue
            if str(item.get("verification_status", "")).lower() not in {"verified", ""}:
                continue
            rule_market = str(item.get("market", "*")).upper()
            if rule_market not in {"", "*", market}:
                continue
            if str(item.get("seller_tier", "general")).lower() not in allowed_tiers:
                continue
            scopes = item.get("slot_scope", ["cover", "gallery"])
            if isinstance(scopes, str):
                scopes = [scopes]
            if scope not in scopes:
                continue
            rule_id = str(item.get("rule_id") or "")
            if rule_id and rule_id in seen:
                continue
            seen.add(rule_id)
            rules.append(copy.deepcopy(item))
    return rules


def _identity_text(value):
    if isinstance(value, dict):
        parts = []
        for key in (
            "family_invariants",
            "primary_variant_attributes",
            "exact_component_constraints",
            "verified_hidden_or_internal_structure",
            "use_relationship_constraints",
            "must_not_change",
        ):
            parts.extend(_string_list(value.get(key)))
        return "; ".join(dict.fromkeys(parts))
    return str(value or "").strip()


def evaluate_prompt_rule_gate(batch, slot, prompt, *, visible_text_lines=None, references=None):
    visible_text_lines = [str(line).strip() for line in (visible_text_lines or []) if str(line).strip()]
    rules = _applicable_rules(batch, slot)
    hard_blocks = []
    if len(prompt) > 3500:
        hard_blocks.append("prompt.max_3500_characters")
    if len(visible_text_lines) > 3:
        hard_blocks.append("prompt.visible_text_max_three_lines")
    if is_standard_product_hero_slot(slot) and visible_text_lines:
        hard_blocks.append("hero.no_added_text")
    for rule in rules:
        rule_id = str(rule.get("rule_id") or "")
        severity = str(rule.get("severity") or "")
        if severity not in {"HARD_PLATFORM", "HARD_MALL"}:
            continue
        if rule_id.endswith(".no_added_text") and visible_text_lines:
            hard_blocks.append(rule_id)
        if rule_id.endswith(".no_digital_rendering") and not is_source_product_photo_slot(slot):
            hard_blocks.append(rule_id)
    return {
        "decision": "block" if hard_blocks else "pass",
        "hard_blocks": list(dict.fromkeys(hard_blocks)),
        "semantic_risks": [],
        "warnings": [],
        "resolved_rule_refs": [str(rule.get("rule_id")) for rule in rules if rule.get("rule_id")],
        "prompt_checks": {
            "character_count": len(prompt),
            "text_line_count": len(visible_text_lines),
            "main_scene_count": 1,
            "main_action_count": 1,
            "reference_assets_valid": all(isinstance(path, str) for path in (references or [])),
        },
        "review_required": True,
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
    slot_directive=None,
    visible_text_lines=None,
    main_scene=None,
    main_action=None,
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
    applicable_rules = _applicable_rules(batch, slot)
    rule_snapshot["resolved_rules"] = applicable_rules
    references = _reference_snapshot(cluster)
    resolved_node_name, node_version, node_instruction = _prompt_node(node_name, node_template)
    product_name = cluster.product_name or "not provided"
    product_facts = cluster.product_facts or "not provided"
    identity_lock = _identity_text(cluster.identity_lock) or "Preserve visible product identity; do not change unprovided attributes."
    global_requirements = batch.global_prompt or "not provided"
    creative_requirements = cluster.prompt_override or "not provided"
    scene = main_scene or sanitized_style_dna.get("scene_density") or slot.purpose or "not specified"
    composition = sanitized_style_dna.get("composition") or slot.purpose or "not specified"
    lighting = sanitized_style_dna.get("lighting") or "not specified"
    material = "Use only material explicitly stated in Product facts; otherwise not specified."
    visible_text_lines = [str(line).strip() for line in (visible_text_lines or []) if str(line).strip()]
    prompt_lines = [
        "Create one ecommerce product image using the supplied product references.",
        f"Product name: {product_name}",
        f"Product facts: {product_facts}",
        f"Global requirements: {global_requirements}",
        f"Identity lock: {identity_lock}",
        f"Creative requirements: {creative_requirements}",
        f"Market: {market or 'not provided'}",
        f"Model persona: {consumer}",
        f"Slot purpose: {slot.purpose or 'not provided'}",
        *[
            f"Rule {rule['rule_id']}: {rule.get('prompt_directive', '')}"
            for rule in applicable_rules
            if rule.get("rule_id") and rule.get("prompt_directive")
        ],
        *(
            [f"Legacy rules: {json.dumps(rule_snapshot.get('rules', {}), ensure_ascii=False, sort_keys=True)}"]
            if isinstance(rule_snapshot.get("rules"), dict)
            else []
        ),
        f"Scene: {scene}",
        f"Main action: {main_action or 'none'}",
        "Grounding: keep confirmed, observed, and disclosed inferred facts traceable; never add price, discount, certification, medical claims, external contact, logos, or parts not listed.",
        f"Composition: {composition}",
        f"Lighting: {lighting}",
        f"Material: {material}",
        f"Style DNA: {json.dumps(sanitized_style_dna, ensure_ascii=False, sort_keys=True)}",
        f"Size: {size}",
        f"Resolution: {resolution}",
    ]
    if slot_directive:
        prompt_lines.append(f"Creative direction: {str(slot_directive).strip()}")
    if visible_text_lines:
        prompt_lines.append(f"Visible text (exactly these lines): {json.dumps(visible_text_lines, ensure_ascii=False)}")
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
        "visible_text_lines": visible_text_lines,
        "main_scene": scene,
        "main_action": main_action or "none",
    }
    prompt, input_snapshot = apply_standard_product_hero_policy(slot, "\n".join(prompt_lines), input_snapshot)
    gate = evaluate_prompt_rule_gate(
        batch,
        slot,
        prompt,
        visible_text_lines=visible_text_lines,
        references=references,
    )
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
        "evaluation": {"fact_policy": "traceable-inference", "rule_gate": gate},
        "size": size,
        "resolution": resolution,
    }


def _json_object(text):
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("provider returned non-object JSON")
    return payload


def _provider_json(response, repair):
    text = response.get("output_text", "")
    try:
        return _json_object(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        fixed = repair(text)
        return _json_object(fixed.get("output_text", ""))


def _json_repair_prompt(text, schema):
    return "\n".join(
        [
            "Rewrite the previous model response as exactly one valid JSON object.",
            "Return no markdown, no prose, no comments, and no code fences.",
            schema,
            "Previous response:",
            str(text)[:6000],
        ]
    )


def _repair_observation_json(client, text):
    return client.optimize_prompt(
        {
            "text": _json_repair_prompt(
                text,
                (
                    'Required schema: {"product_name":"string","confidence":0.0,'
                    '"product_facts":["string"],"identity_lock":"string","target_consumer":"string"}.'
                ),
            )
        }
    )


def _repair_slot_prompt_json(client, text, slots):
    orders = ", ".join(str(slot.order) for slot in slots)
    return client.optimize_prompt(
        {
            "text": _json_repair_prompt(
                text,
                (
                    'Required schema: {"slots":[{"order":1,"prompt":"string"}]}. '
                    f"Include exactly these slot orders once each: {orders}."
                ),
            )
        }
    )


def _string_list(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _slot_prompt_map(payload):
    slots = payload.get("slots")
    if not isinstance(slots, list):
        raise ValueError("prompt JSON must include slots")
    prompts = {}
    for item in slots:
        if not isinstance(item, dict):
            continue
        try:
            order = int(item.get("order"))
        except (TypeError, ValueError):
            continue
        prompt = str(item.get("prompt") or "").strip()
        if order and prompt:
            prompts[order] = prompt
    return prompts


def _snapshot_hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _node_snapshot(node_id, model_id, input_snapshot, output_snapshot, *, slot_id=None):
    node_template = _published_prompt_node(node_id)
    prompt_version = (
        node_template.version
        if node_template is not None and model_id != "deterministic-rule-engine"
        else "deterministic-v2.0.0"
        if model_id == "deterministic-rule-engine"
        else "builtin-v2.0.0"
    )
    return {
        "snapshot_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "node_id": node_id,
        "node_version": prompt_version,
        "schema_version": "2.0.0",
        "prompt_template_version": prompt_version,
        "model_id": model_id,
        "slot_id": str(slot_id) if slot_id else None,
        "input_hash": _snapshot_hash(input_snapshot),
        "output_hash": _snapshot_hash(output_snapshot),
        "input_snapshot": input_snapshot,
        "output_snapshot": output_snapshot,
        "status": "succeeded",
        "created_at": timezone.now().isoformat(),
    }


def _prompt_node_json(client, node_id, instruction, payload):
    node_template = _published_prompt_node(node_id)
    system_instruction = node_template.instruction if node_template is not None else ""
    response = client.optimize_prompt(
        {
            "system": system_instruction
            or "Return only valid JSON for ecommerce image prompt planning.",
            "text": "\n".join(
                [
                    f"NODE {node_id}",
                    instruction,
                    "Return exactly one JSON object without Markdown or commentary.",
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ]
            )
        }
    )
    return _provider_json(
        response,
        lambda text: client.optimize_prompt(
            {
                "text": _json_repair_prompt(
                    text,
                    f"Return the valid JSON object required by NODE {node_id}.",
                )
            }
        ),
    )


def _fact_ledger(payload):
    facts = []
    for index, item in enumerate(payload.get("facts", []), start=1):
        if not isinstance(item, dict) or not str(item.get("statement") or "").strip():
            continue
        fact_class = str(item.get("fact_class") or "inferred").lower()
        if fact_class not in {"confirmed", "observed", "inferred"}:
            fact_class = "inferred"
        try:
            confidence = min(max(float(item.get("confidence", 0)), 0), 1)
        except (TypeError, ValueError):
            confidence = 0
        risk = str(item.get("risk_level") or "medium").lower()
        allowed = _string_list(item.get("allowed_uses"))
        if fact_class == "inferred" and risk == "high":
            allowed = ["blocked"]
        facts.append(
            {
                "fact_id": str(item.get("fact_id") or f"fact.{index:03d}"),
                "statement": str(item["statement"]).strip(),
                "fact_class": fact_class,
                "confidence": confidence,
                "evidence_refs": _string_list(item.get("evidence_refs")),
                "risk_level": risk,
                "allowed_uses": allowed,
                "review_note": str(item.get("review_note") or ""),
            }
        )
    counts = {kind: sum(item["fact_class"] == kind for item in facts) for kind in ("confirmed", "observed", "inferred")}
    return {
        "ledger_version": "2.0.0",
        "facts": facts,
        "blocked_claim_topics": _string_list(payload.get("blocked_claim_topics")),
        "unresolved_questions": _string_list(payload.get("unresolved_questions")),
        "review_summary": {
            "confirmed_count": counts["confirmed"],
            "observed_count": counts["observed"],
            "inferred_count": counts["inferred"],
            "high_risk_count": sum(item["risk_level"] == "high" for item in facts),
        },
    }


def _identity_facts(identity):
    profile = identity.get("product_profile") if isinstance(identity.get("product_profile"), dict) else {}
    values = []
    for value in profile.values():
        if isinstance(value, list):
            values.extend(_string_list(value))
        elif value:
            values.append(str(value).strip())
    return "; ".join(dict.fromkeys(item for item in values if item))


def process_prompt_once(client=None, storage=None):
    client = client or (FakeAPIMartClient() if settings.APIMART_FAKE_MODE else APIMartClient())
    storage = storage or LocalStorage()
    cluster = (
        Cluster.objects.select_related("batch", "batch__owner", "batch__output_template")
        .filter(preparation_status=Cluster.PreparationStatus.PENDING)
        .order_by("updated_at", "created_at", "id")
        .first()
    )
    if cluster is None:
        return 0
    Cluster.objects.filter(id=cluster.id).update(
        preparation_status=Cluster.PreparationStatus.PREPARING,
        preparation_error="",
        updated_at=timezone.now(),
    )
    try:
        node_snapshots = []
        cluster_assets = list(cluster.cluster_assets.select_related("asset").order_by("order", "id"))
        references = [item.asset.storage_path for item in cluster_assets if item.asset.kind == Asset.Kind.IMAGE]
        observations = []
        observation_template = _published_prompt_node("N1")
        observation_system = (
            observation_template.instruction
            if observation_template is not None
            else "Observe only visible product evidence. Return one strict JSON object."
        )
        with storage.reference_paths(references) as image_paths:
            for relation, image_path in zip(cluster_assets, image_paths):
                observation_input = {
                    "asset_id": str(relation.asset_id),
                    "asset_kind": "owned_product",
                    "product_name": cluster.product_name,
                    "confirmed_points": _string_list(cluster.product_facts),
                }
                observation = _provider_json(
                    client.observe_images(
                        "\n".join(
                            [
                                "NODE N1",
                                observation_system,
                                "Observe only visible product evidence in this single owned-product image.",
                                "Return strict JSON with image_role, product visibility, observed_identity, and recommended_use.",
                            ]
                        ),
                        [image_path],
                    ),
                    lambda text: _repair_observation_json(client, text),
                )
                observation.setdefault("asset_id", str(relation.asset_id))
                observations.append(observation)
                node_snapshots.append(
                    _node_snapshot("N1", settings.APIMART_VISION_MODEL, observation_input, observation)
                )

        observed_name = next(
            (
                str(item.get("product_name") or item.get("name") or "").strip()
                for item in observations
                if str(item.get("product_name") or item.get("name") or "").strip()
            ),
            "",
        )
        observed_facts = [
            fact
            for item in observations
            for fact in _string_list(item.get("product_facts") or item.get("facts"))
        ]
        identity_input = {
            "product_name": cluster.product_name or observed_name,
            "confirmed_points": _string_list(cluster.product_facts) or observed_facts,
            "relation_type": cluster.relation_type,
            "observations": observations,
            "max_supporting_images": 3,
        }
        identity = _prompt_node_json(
            client,
            "N2",
            "Merge owned observations into one product identity. Select one primary asset, at most three supporting assets, and an identity lock.",
            identity_input,
        )
        node_snapshots.append(
            _node_snapshot("N2", settings.APIMART_PROMPT_MODEL, identity_input, identity)
        )
        product_name = str(identity.get("product_name") or cluster.product_name or "").strip()
        confidence = float(identity.get("confidence", 0)) / (100 if float(identity.get("confidence", 0)) > 1 else 1)
        if identity.get("decision") != "continue" or confidence < 0.5 or not product_name:
            analysis = {"observations": observations, "identity": identity, "prompt_os": node_snapshots}
            Cluster.objects.filter(id=cluster.id).update(
                product_name=product_name or "名称待确认",
                analysis_snapshot=analysis,
                preparation_status=Cluster.PreparationStatus.BLOCKED,
                preparation_error="product identity needs confirmation",
                updated_at=timezone.now(),
            )
            return 1

        identity_lock = identity.get("identity_lock") or {}
        identity_facts = _identity_facts(identity)
        Cluster.objects.filter(id=cluster.id).update(
            product_name=product_name,
            name=product_name,
            product_facts="; ".join(identity_input["confirmed_points"]) or identity_facts or cluster.product_facts,
            identity_lock=_identity_text(identity_lock),
            updated_at=timezone.now(),
        )
        cluster.refresh_from_db()
        ledger_input = {
            "product_name": cluster.product_name,
            "confirmed_points": _string_list(cluster.product_facts),
            "product_profile": identity.get("product_profile", {}),
            "identity_lock": identity_lock,
            "owned_observations": observations,
            "market_context": {
                "platform": cluster.batch.platform,
                "market": cluster.batch.market or cluster.batch.site,
            },
        }
        ledger = _fact_ledger(
            _prompt_node_json(
                client,
                "N3",
                "Classify every fact as confirmed, observed, or inferred with confidence, risk, evidence, and allowed uses.",
                ledger_input,
            )
        )
        node_snapshots.append(
            _node_snapshot("N3", settings.APIMART_PROMPT_MODEL, ledger_input, ledger)
        )

        template = cluster.batch.output_template or _global_fallback_template()
        slots = list(template.slots.order_by("order", "id"))
        hero_slot = standard_product_hero_slot(template)
        if hero_slot is None:
            raise ValueError("output template requires a standard product hero")
        generated_slots = [slot for slot in slots if not is_source_product_photo_slot(slot)]
        marketing_slots = [slot for slot in generated_slots if slot.id != hero_slot.id]
        compiled_by_slot = {}

        hero_input = {
            "slot_order": hero_slot.order,
            "product_name": cluster.product_name,
            "identity_lock": identity_lock,
            "fact_ledger": ledger,
            "resolved_rule_directives": [
                rule.get("prompt_directive") for rule in _applicable_rules(cluster.batch, hero_slot)
            ],
            "prompt_limits": {"max_characters": 3500, "max_text_lines": 0},
        }
        hero_plan = _prompt_node_json(
            client,
            "N4",
            "Compile the standard white-background product hero. One scene, no action, no new visible text.",
            hero_input,
        )
        node_snapshots.append(
            _node_snapshot("N4", settings.APIMART_PROMPT_MODEL, hero_input, hero_plan, slot_id=hero_slot.id)
        )
        compiled_by_slot[hero_slot.id] = compile_slot_prompt(
            cluster,
            hero_slot,
            batch=cluster.batch,
            template=template,
            slot_directive=hero_plan.get("prompt"),
            visible_text_lines=hero_plan.get("visible_text_lines"),
            main_scene=hero_plan.get("main_scene"),
            main_action=hero_plan.get("main_action"),
            node_name="N4",
            node_template=None,
        )

        marketing_input = {
            "product_name": cluster.product_name,
            "identity_lock": identity_lock,
            "fact_ledger": ledger,
            "slots": [
                {"slot_order": slot.order, "name": slot.name, "purpose": slot.purpose}
                for slot in marketing_slots
            ],
            "seed_style": cluster.prompt_override or cluster.batch.global_prompt,
        }
        marketing_plan = _prompt_node_json(
            client,
            "N5",
            "Plan one distinct purchase-decision scene for every supplied marketing slot. Do not repeat scene families.",
            marketing_input,
        )
        plans = {
            int(item.get("slot_order")): item
            for item in marketing_plan.get("plans", [])
            if isinstance(item, dict) and str(item.get("slot_order", "")).isdigit()
        }
        if set(plans) != {slot.order for slot in marketing_slots}:
            raise ValueError("marketing plan missing slot plans")
        scene_families = [str(plans[slot.order].get("scene_family") or "") for slot in marketing_slots]
        if len(scene_families) != len(set(scene_families)):
            raise ValueError("marketing plan repeats scene families")
        node_snapshots.append(
            _node_snapshot("N5", settings.APIMART_PROMPT_MODEL, marketing_input, marketing_plan)
        )
        for slot in marketing_slots:
            slot_input = {
                "slot_order": slot.order,
                "slot_plan": plans[slot.order],
                "product_name": cluster.product_name,
                "identity_lock": identity_lock,
                "fact_ledger": ledger,
                "resolved_rule_directives": [
                    rule.get("prompt_directive") for rule in _applicable_rules(cluster.batch, slot)
                ],
                "prompt_limits": {"max_characters": 3500, "max_text_lines": 3},
            }
            slot_plan = _prompt_node_json(
                client,
                "N6",
                f"SLOT_ORDER={slot.order}\nCompile one localized image instruction for this slot with one scene, one main action, and at most three visible text lines.",
                slot_input,
            )
            node_snapshots.append(
                _node_snapshot("N6", settings.APIMART_PROMPT_MODEL, slot_input, slot_plan, slot_id=slot.id)
            )
            compiled_by_slot[slot.id] = compile_slot_prompt(
                cluster,
                slot,
                batch=cluster.batch,
                template=template,
                slot_directive=slot_plan.get("prompt"),
                visible_text_lines=slot_plan.get("visible_text_lines"),
                main_scene=slot_plan.get("main_scene"),
                main_action=slot_plan.get("main_action"),
                node_name="N6",
                node_template=None,
            )

        gate_blocks = []
        for slot in slots:
            if is_source_product_photo_slot(slot):
                source_relation = cluster_assets[0]
                prompt_text = "Preserve the original seller product photo without AI modification."
                gate = evaluate_prompt_rule_gate(cluster.batch, slot, prompt_text)
                compiled = {
                    "node_name": "source_passthrough",
                    "template_version": "builtin-v2.0.0",
                    "provider_model": "none",
                    "prompt": prompt_text,
                    "reference_snapshot": [source_relation.asset.storage_path],
                    "template_snapshot": _template_snapshot(template, slot),
                    "rule_snapshot": _rule_snapshot(cluster.batch.rule_profile),
                    "input_snapshot": {"source_asset_id": str(source_relation.asset_id)},
                    "evaluation": {"fact_policy": "source-passthrough", "rule_gate": gate},
                    "size": cluster.batch.size or template.default_size,
                    "resolution": cluster.batch.resolution or template.default_resolution,
                }
            else:
                compiled = compiled_by_slot[slot.id]
                prompt_text = compiled["prompt"]
                gate = compiled["evaluation"]["rule_gate"]
            gate_blocks.extend(gate["hard_blocks"])
            gate_input = {
                "slot_order": slot.order,
                "prompt": prompt_text,
                "rule_snapshot": compiled["rule_snapshot"],
            }
            node_snapshots.append(
                _node_snapshot("N7", "deterministic-rule-engine", gate_input, gate, slot_id=slot.id)
            )
            values = {
                "cluster": cluster,
                "output_slot": slot,
                "created_by": cluster.batch.owner,
                "node_name": compiled["node_name"],
                "template_version": compiled["template_version"],
                "provider_model": compiled["provider_model"],
                "prompt_text": prompt_text,
                "input_snapshot": compiled["input_snapshot"],
                "structured_output": compiled,
                "evaluation": compiled["evaluation"],
                "source_snapshot": compiled["input_snapshot"],
            }
            PromptVersion.objects.create(**values)
        analysis = {
            "observations": observations,
            "identity": identity,
            "fact_ledger": ledger,
            "marketing_plan": marketing_plan,
            "rule_gate": {
                "decision": "block" if gate_blocks else "pass",
                "hard_blocks": list(dict.fromkeys(gate_blocks)),
                "semantic_risks": [],
                "warnings": [],
            },
            "prompt_os": node_snapshots,
        }
        Cluster.objects.filter(id=cluster.id).update(
            analysis_snapshot=analysis,
            preparation_status=(
                Cluster.PreparationStatus.BLOCKED if gate_blocks else Cluster.PreparationStatus.READY
            ),
            preparation_error=", ".join(dict.fromkeys(gate_blocks)),
            updated_at=timezone.now(),
        )
        cluster.refresh_from_db()
        if cluster.auto_generate and not gate_blocks:
            ensure_cluster_generations(cluster, cluster.batch.owner)
        return 1
    except Exception as exc:
        Cluster.objects.filter(id=cluster.id).update(
            preparation_status=Cluster.PreparationStatus.FAILED,
            preparation_error=_sanitize_provider_text(str(exc)),
            updated_at=timezone.now(),
        )
        return 1


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
    if not settings.GENERATION_QUOTAS_ENABLED:
        return
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
    generated_slots = [
        slot for slot in template.slots.order_by("order", "id") if not is_source_product_photo_slot(slot)
    ]
    cluster_count = batch.clusters.count()
    generation_count = cluster_count * len(generated_slots)
    org_used = _used_generations_today()
    user_used = _used_generations_today(user)
    user_limit = user.daily_generation_limit or settings.USER_DAILY_GENERATION_LIMIT
    org_remaining = max(settings.ORG_DAILY_GENERATION_LIMIT - org_used, 0)
    user_remaining = max(user_limit - user_used, 0)
    blocking_errors = []

    if cluster_count == 0:
        blocking_errors.append("batch has no image clusters")
    hero_slot = standard_product_hero_slot(template)
    if hero_slot is None or not is_standard_product_hero_slot(hero_slot):
        blocking_errors.append("output template requires a standard product hero")
    for slot in generated_slots:
        for rule in _applicable_rules(batch, slot):
            rule_id = str(rule.get("rule_id") or "")
            if (
                str(rule.get("severity") or "") in {"HARD_PLATFORM", "HARD_MALL"}
                and rule_id.endswith(".no_digital_rendering")
            ):
                blocking_errors.append(rule_id)
    if generation_count > BATCH_GENERATION_LIMIT:
        blocking_errors.append("batch generation limit exceeded")
    if settings.GENERATION_QUOTAS_ENABLED:
        if generation_count > org_remaining:
            blocking_errors.append("organization daily quota exceeded")
        if generation_count > user_remaining:
            blocking_errors.append("user daily quota exceeded")
    blocking_errors = list(dict.fromkeys(blocking_errors))

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


def _latest_completed_hero(cluster, template):
    hero_slot = standard_product_hero_slot(template)
    if hero_slot is None:
        return None
    return (
        cluster.generations.filter(
            output_slot__template=template,
            output_slot=hero_slot,
            status=Generation.Status.COMPLETED,
            result_assets__isnull=False,
        )
        .prefetch_related("result_assets")
        .order_by("-attempt", "-created_at", "-id")
        .first()
    )


def _ensure_source_passthrough_generation(cluster, batch, template, slot, user):
    existing = (
        cluster.generations.filter(output_slot=slot)
        .exclude(status=Generation.Status.CANCELED)
        .order_by("-attempt", "-created_at", "-id")
        .first()
    )
    if existing is not None:
        return existing
    relation = (
        cluster.cluster_assets.select_related("asset")
        .filter(asset__kind=Asset.Kind.IMAGE)
        .order_by("order", "id")
        .first()
    )
    if relation is None:
        raise ValueError("source-photo slot requires an uploaded or ERP product image")
    prompt = "Preserve the original seller product photo without AI modification."
    prompt_version = _prompt_for_slot(cluster, slot) or PromptVersion.objects.create(
        cluster=cluster,
        output_slot=slot,
        created_by=user,
        node_name="source_passthrough",
        template_version="builtin-v2.0.0",
        provider_model="none",
        prompt_text=prompt,
        input_snapshot={"source_asset_id": str(relation.asset_id)},
        structured_output={"source_asset_id": str(relation.asset_id), "prompt": prompt},
        evaluation={"fact_policy": "source-passthrough", "rule_gate": {"decision": "pass", "hard_blocks": []}},
        source_snapshot={"source_asset_id": str(relation.asset_id)},
    )
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        prompt_version=prompt_version,
        created_by=user,
        attempt=1,
        status=Generation.Status.COMPLETED,
        prompt_text=prompt,
        size=batch.size or template.default_size,
        resolution=batch.resolution or template.default_resolution,
        reference_snapshot=[relation.asset.storage_path],
        template_snapshot=_template_snapshot(template, slot),
        rule_snapshot=_rule_snapshot(batch.rule_profile),
        completed_at=timezone.now(),
    )
    source_data = LocalStorage().read(relation.asset.storage_path)
    suffix = Path(relation.asset.storage_path).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    storage_path = (
        f"results/{batch.id}/{cluster.id}/{slot.id}/1/"
        f"{uuid.uuid4().hex}{suffix}"
    )
    LocalStorage().save(storage_path, source_data)
    ResultAsset.objects.create(
        generation=generation,
        storage_path=storage_path,
        sha256=relation.asset.sha256,
        file_size=len(source_data),
        width=relation.asset.width,
        height=relation.asset.height,
    )
    return generation


def _prompt_for_slot(cluster, slot):
    return (
        cluster.prompt_versions.filter(output_slot=slot)
        .order_by("-created_at", "-id")
        .first()
    )


@transaction.atomic
def ensure_cluster_generations(cluster, user, *, slot_orders=None, force_new=False):
    locked = Cluster.objects.select_for_update().get(id=cluster.id)
    batch = Batch.objects.select_for_update().get(id=locked.batch_id)
    template = batch.output_template or _global_fallback_template()
    if template.status != OutputTemplate.Status.PUBLISHED:
        raise ValueError("output template must be published before generation")
    if batch.rule_profile_id and batch.rule_profile.status != RuleProfile.Status.PUBLISHED:
        raise ValueError("rule profile must be published before generation")
    if batch.output_template_id != template.id:
        batch.output_template = template
    if not batch.market:
        batch.market = batch.site
    if not batch.size:
        batch.size = template.default_size
    if not batch.resolution:
        batch.resolution = template.default_resolution
    batch.save(update_fields=["output_template", "market", "size", "resolution", "updated_at"])

    requested = {int(order) for order in slot_orders} if slot_orders else None
    slots = list(template.slots.order_by("order", "id"))
    hero_slot = standard_product_hero_slot(template)
    if hero_slot is None or not is_standard_product_hero_slot(hero_slot):
        raise ValueError("output template requires a standard product hero")
    source_slots = [slot for slot in slots if is_source_product_photo_slot(slot)]
    if requested:
        required = {*requested, hero_slot.order, *(slot.order for slot in source_slots)}
        slots = [slot for slot in slots if slot.order in required]

    for source_slot in source_slots:
        if source_slot in slots:
            _ensure_source_passthrough_generation(locked, batch, template, source_slot, user)
    hero = _latest_completed_hero(locked, template)
    creatable = [
        slot
        for slot in slots
        if not is_source_product_photo_slot(slot) and (slot.id == hero_slot.id or hero is not None)
    ]
    if not force_new:
        existing = {
            generation.output_slot_id: generation
            for generation in locked.generations.filter(
                output_slot__in=creatable,
                status__in=[
                    Generation.Status.QUEUED,
                    Generation.Status.PREPARING,
                    Generation.Status.SUBMITTING,
                    Generation.Status.SUBMITTED,
                    Generation.Status.PROCESSING,
                    Generation.Status.ARCHIVING,
                    Generation.Status.COMPLETED,
                ],
            ).order_by("output_slot__order", "-attempt", "-id")
        }
    else:
        existing = {}

    to_create = [slot for slot in creatable if force_new or slot.id not in existing]
    reserve_generation_usage(user, len(to_create))
    hero_refs = []
    if hero is not None:
        hero_refs = [result.storage_path for result in hero.result_assets.all()]
    created = []
    node_template = _published_prompt_node("slot_prompt")
    for slot in to_create:
        prompt_version = _prompt_for_slot(locked, slot)
        if prompt_version is None:
            compiled = compile_slot_prompt(locked, slot, batch=batch, template=template, node_template=node_template)
            if compiled["evaluation"]["rule_gate"]["decision"] != "pass":
                raise ValueError(", ".join(compiled["evaluation"]["rule_gate"]["hard_blocks"]))
            prompt_version = PromptVersion.objects.create(
                cluster=locked,
                output_slot=slot,
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
        elif prompt_version.evaluation.get("rule_gate", {}).get("decision") == "block":
            raise ValueError(
                ", ".join(prompt_version.evaluation["rule_gate"].get("hard_blocks", []))
                or "prompt rule gate blocked generation"
            )
        references = _prompt_version_references(prompt_version) or _reference_snapshot(locked)
        if slot.id != hero_slot.id:
            references = list(dict.fromkeys([*references, *hero_refs]))
        created.append(
            Generation.objects.create(
                batch=batch,
                cluster=locked,
                output_slot=slot,
                prompt_version=prompt_version,
                created_by=user,
                attempt=latest_attempt(locked, slot) + 1,
                status=Generation.Status.QUEUED,
                prompt_text=prompt_version.prompt_text,
                size=batch.size or template.default_size,
                resolution=batch.resolution or template.default_resolution,
                reference_snapshot=references,
                template_snapshot=_template_snapshot(template, slot),
                rule_snapshot=_rule_snapshot(batch.rule_profile),
            )
        )
    Batch.objects.filter(id=batch.id).update(status=Batch.Status.QUEUED, updated_at=timezone.now())
    return list(
        locked.generations.select_related("output_slot")
        .filter(output_slot__in=slots)
        .order_by("output_slot__order", "attempt", "id")
    )


def regenerate_generation(source, user, prompt_version=None):
    overrides = {}
    if prompt_version is not None:
        overrides["prompt_version"] = prompt_version
        overrides["prompt_text"] = prompt_version.prompt_text
    return _create_followup_attempt(source, user, **overrides)


@transaction.atomic
def confirm_generation(batch, user, template=None):
    locked_batch = Batch.objects.select_for_update().get(id=batch.id)
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
    hero_slot = standard_product_hero_slot(template)
    source_slots = [slot for slot in slots if is_source_product_photo_slot(slot)]
    for cluster in clusters:
        for slot in slots:
            if is_source_product_photo_slot(slot):
                _ensure_source_passthrough_generation(cluster, locked_batch, template, slot, user)
                continue
            if source_slots and slot.id != hero_slot.id:
                continue
            compiled = compile_slot_prompt(
                cluster,
                slot,
                batch=locked_batch,
                template=template,
                node_template=node_template,
            )
            if compiled["evaluation"]["rule_gate"]["decision"] != "pass":
                raise ValueError(", ".join(compiled["evaluation"]["rule_gate"]["hard_blocks"]))
            prompt_version = PromptVersion.objects.create(
                cluster=cluster,
                output_slot=slot,
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
    if status in {"pending", "processing", "in_progress", "submitted", "queued"}:
        return Generation.Status.PROCESSING
    if status in {"completed", "succeeded", "success"}:
        return Generation.Status.COMPLETED
    if status in {"failed", "error", "canceled", "cancelled"}:
        return Generation.Status.FAILED
    return Generation.Status.PROCESSING


def _image_urls(payload):
    if "image_urls" in payload:
        urls = []
        for item in payload["image_urls"]:
            if isinstance(item, dict):
                item = item.get("url")
            if item:
                urls.append(item)
        return urls
    urls = []
    for image in payload.get("result", {}).get("images", []):
        value = image.get("url")
        if isinstance(value, list):
            urls.extend(value)
        elif value:
            urls.append(value)
    return urls


def _simplifiable_failure(payload):
    text = " ".join(
        str(payload.get(key) or "") for key in ("code", "error_code", "error", "message")
    ).lower()
    if any(token in text for token in ("prompt too complex", "prompt_complexity", "too many instructions")):
        return "prompt_complexity"
    if any(token in text for token in ("content safety", "safety rejection", "content_policy")):
        return "content_safety_rejection"
    return ""


def _create_simplified_failure_retry(generation, client):
    previous = generation.prompt_version
    if previous and previous.structured_output.get("failure_simplifier"):
        return None
    failure_class = _simplifiable_failure(generation.provider_payload)
    if not failure_class:
        return None
    simplifier_input = {
        "failure_class": failure_class,
        "provider_message_sanitized": generation.failure_reason,
        "original_prompt": generation.prompt_text,
        "identity_lock": generation.cluster.identity_lock,
        "fact_ledger": generation.cluster.analysis_snapshot.get("fact_ledger", {}),
        "rule_snapshot": generation.rule_snapshot,
        "max_simplification_attempts": 1,
    }
    output = _prompt_node_json(
        client,
        "N9",
        "Return a clearly shorter prompt that preserves product identity and hard rules. Do not handle network, rate-limit, or unknown-submit failures.",
        simplifier_input,
    )
    if output.get("decision") != "retry_with_simplified_prompt":
        return None
    prompt = str(output.get("simplified_prompt") or "").strip()
    if not prompt or len(prompt) >= len(generation.prompt_text):
        return None
    prompt, input_snapshot = apply_standard_product_hero_policy(
        generation.output_slot,
        prompt,
        copy.deepcopy(previous.input_snapshot if previous else {}),
    )
    if len(prompt) >= len(generation.prompt_text):
        return None
    gate = evaluate_prompt_rule_gate(
        generation.batch,
        generation.output_slot,
        prompt,
        visible_text_lines=output.get("visible_text_lines"),
        references=generation.reference_snapshot,
    )
    if gate["decision"] != "pass":
        return None
    structured_output = copy.deepcopy(previous.structured_output if previous else {})
    structured_output["failure_simplifier"] = output
    structured_output["node_snapshot"] = _node_snapshot(
        "N9",
        settings.APIMART_PROMPT_MODEL,
        simplifier_input,
        output,
        slot_id=generation.output_slot_id,
    )
    prompt_version = PromptVersion.objects.create(
        cluster=generation.cluster,
        output_slot=generation.output_slot,
        created_by=generation.created_by or generation.batch.owner,
        node_name="N9",
        template_version=_prompt_node("N9")[1],
        provider_model="gpt-image-2",
        prompt_text=prompt,
        input_snapshot=input_snapshot,
        structured_output=structured_output,
        evaluation={"fact_policy": "traceable-inference", "rule_gate": gate},
        source_snapshot=copy.deepcopy(previous.source_snapshot if previous else input_snapshot),
    )
    return _create_followup_attempt(
        generation,
        generation.created_by or generation.batch.owner,
        prompt_version=prompt_version,
        prompt_text=prompt,
    )


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
        hero_slot = standard_product_hero_slot(candidate.output_slot.template)
        if hero_slot is not None and candidate.output_slot_id == hero_slot.id:
            queued = candidate
            break
        hero = (
            Generation.objects.filter(
                batch_id=candidate.batch_id,
                cluster_id=candidate.cluster_id,
                output_slot=hero_slot,
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
        try:
            with storage.reference_paths(queued.reference_snapshot) as image_paths:
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
        active.provider_payload = {
            key: payload.get(key)
            for key in ("status", "code", "error_code", "error", "message")
            if payload.get(key) is not None
        }
        active.save(update_fields=["status", "failure_reason", "provider_payload", "updated_at"])
        try:
            _create_simplified_failure_retry(active, client)
        except Exception:
            pass
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
    hero_slot = standard_product_hero_slot(active.output_slot.template)
    if hero_slot is not None and active.output_slot_id == hero_slot.id:
        ensure_cluster_generations(active.cluster, active.created_by)
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


@transaction.atomic
def update_cluster_content(cluster, user, payload):
    locked = Cluster.objects.select_for_update().select_related("batch", "batch__output_template").get(id=cluster.id)
    if payload.get("expected_version") != locked.version:
        raise ValueError("Cluster changed; refresh before saving")
    prompts = payload.get("prompts", [])
    if not isinstance(prompts, list):
        raise TypeError("prompts must be an array")
    template = locked.batch.output_template or _global_fallback_template()
    slots = {slot.order: slot for slot in template.slots.order_by("order", "id")}
    prepared_prompts = []
    for item in prompts:
        if not isinstance(item, dict):
            raise TypeError("each prompt must be an object")
        try:
            order = int(item.get("slot_order"))
        except (TypeError, ValueError):
            raise ValueError("slot_order must be an integer") from None
        slot = slots.get(order)
        if slot is None:
            raise ValueError(f"unknown slot_order: {order}")
        if is_source_product_photo_slot(slot):
            raise ValueError("the original source-photo slot cannot be replaced by a generated prompt")
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"prompt for slot {order} cannot be empty")
        input_snapshot = {
            "manual_edit": True,
            "cluster_version": locked.version,
            "reference_snapshot": _reference_snapshot(locked),
            "analysis_snapshot_hash": _snapshot_hash(locked.analysis_snapshot),
        }
        prompt, input_snapshot = apply_standard_product_hero_policy(slot, prompt, input_snapshot)
        gate = evaluate_prompt_rule_gate(
            locked.batch,
            slot,
            prompt,
            references=input_snapshot["reference_snapshot"],
        )
        if gate["decision"] != "pass":
            raise ValueError(", ".join(gate["hard_blocks"]))
        prepared_prompts.append((slot, prompt, input_snapshot, gate))

    for field in ("product_name", "product_facts", "identity_lock", "prompt_override"):
        if field in payload:
            setattr(locked, field, str(payload[field] or ""))
    for slot, prompt, input_snapshot, gate in prepared_prompts:
        PromptVersion.objects.create(
            cluster=locked,
            output_slot=slot,
            created_by=user,
            node_name="manual_edit",
            template_version="manual-v1",
            provider_model="gpt-image-2",
            prompt_text=prompt,
            input_snapshot=input_snapshot,
            structured_output={
                "manual_edit": True,
                "prompt": prompt,
                "slot_order": slot.order,
                "rule_gate": gate,
            },
            evaluation={"fact_policy": "human-reviewed", "rule_gate": gate},
            source_snapshot=input_snapshot,
        )
    locked.version += 1
    locked.save(
        update_fields=[
            "product_name",
            "product_facts",
            "identity_lock",
            "prompt_override",
            "version",
            "updated_at",
        ]
    )
    return locked


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
        output_slot=generation.output_slot,
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
    locked = Generation.objects.select_for_update().get(id=generation.id)
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
    locked = Generation.objects.select_for_update().get(id=generation.id)
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
        director_input = {
            "source_generation_id": str(locked.id),
            "current_prompt": prior_prompt,
            "identity_lock": locked.cluster.identity_lock,
            "fact_ledger": locked.cluster.analysis_snapshot.get("fact_ledger", {}),
            "rule_snapshot": locked.rule_snapshot,
            "review": {**revision_delta, "annotations": normalized_annotations},
        }
        if settings.APIMART_FAKE_MODE:
            director_output = {
                "operation": "edit_region" if normalized_annotations else "edit_image",
                "change_intent": description or ", ".join(normalized_tags),
                "preserve_outside_region": True,
                "visible_text_lines": [],
                "delta_prompt": (
                    f"Change only the reviewed issue: {description or ', '.join(normalized_tags)}. "
                    "Preserve all other product identity, composition, text, and lighting."
                ),
                "review_required": True,
            }
        else:
            director_output = _prompt_node_json(
                APIMartClient(),
                "N8",
                "Compile the review tags, normalized annotations, and description into the smallest safe edit instruction. Preserve everything outside the requested change.",
                director_input,
            )
        if director_output.get("operation") == "blocked_change":
            raise ValueError("requested revision conflicts with the product identity or platform rules")
        delta_lines = [
            prior_prompt,
            "Revision delta:",
            str(director_output.get("delta_prompt") or description or ", ".join(normalized_tags)),
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
        gate = evaluate_prompt_rule_gate(
            locked.batch,
            locked.output_slot,
            prompt_text,
            visible_text_lines=director_output.get("visible_text_lines"),
            references=references,
        )
        if gate["decision"] != "pass":
            raise ValueError(", ".join(gate["hard_blocks"]))
        structured_output["modification_director"] = director_output
        structured_output["node_snapshot"] = _node_snapshot(
            "N8",
            settings.APIMART_PROMPT_MODEL,
            director_input,
            director_output,
            slot_id=locked.output_slot_id,
        )
        structured_output["prompt"] = prompt_text
        prompt_version = PromptVersion.objects.create(
            cluster=locked.cluster,
            output_slot=locked.output_slot,
            created_by=reviewer,
            node_name="N8",
            template_version=_prompt_node("N8")[1],
            provider_model=previous.provider_model if previous else "gpt-image-2",
            prompt_text=prompt_text,
            input_snapshot=input_snapshot,
            structured_output=structured_output,
            evaluation={"fact_policy": "traceable-inference", "rule_gate": gate},
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


def request_generation_revision(generation, user, *, issue_tags, description, annotations):
    return review_generation(
        generation,
        user,
        decision=ReviewFeedback.Decision.CHANGES_REQUESTED,
        issue_tags=issue_tags,
        description=description,
        annotations=annotations,
    )[1]


def safe_storage_path(storage_path, expected_prefix):
    storage_path = validate_storage_path(storage_path, expected_prefix)
    if str(settings.STORAGE_BACKEND).lower() != "oss":
        root = Path(settings.MEDIA_ROOT).resolve()
        prefix = PurePosixPath(expected_prefix)
        prefix_path = root / Path(*prefix.parts)
        if prefix_path.exists() and prefix_path.resolve() != prefix_path:
            raise ValueError("Invalid storage path")
        target = (root / Path(*PurePosixPath(storage_path).parts)).resolve()
        if not target.is_relative_to(prefix_path) or not target.is_file():
            raise ValueError("Stored file is unavailable")
    LocalStorage().size(storage_path)
    return storage_path


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
        latest_prompts = {}
        for prompt in cluster.prompt_versions.select_related("output_slot").order_by(
            "output_slot__order", "-created_at", "-id"
        ):
            if prompt.output_slot_id:
                latest_prompts.setdefault(prompt.output_slot_id, prompt)
        outputs = []
        for generation in cluster.generations.select_related("output_slot", "prompt_version").prefetch_related(
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
                "prompt": generation.prompt_text,
                "promptVersionId": str(generation.prompt_version_id) if generation.prompt_version_id else None,
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
                "version": cluster.version,
                "relationType": cluster.relation_type,
                "preparationStatus": cluster.preparation_status,
                "importStatus": (
                    latest_imports[cluster.sku].status if cluster.sku in latest_imports else "manual"
                ),
                "assetIds": [str(item.asset_id) for item in cluster_assets],
                "assets": sku_assets,
                "facts": cluster.product_facts,
                "identityLock": cluster.identity_lock,
                "brief": cluster.prompt_override,
                "analysisSnapshot": cluster.analysis_snapshot,
                "prompts": [
                    {
                        "slotOrder": slot.order,
                        "slot": slot.name,
                        "text": latest_prompts[slot.id].prompt_text if slot.id in latest_prompts else "",
                        "promptVersionId": (
                            str(latest_prompts[slot.id].id) if slot.id in latest_prompts else None
                        ),
                        "readOnly": is_source_product_photo_slot(slot),
                    }
                    for slot in template.slots.order_by("order", "id")
                ],
                "outputs": outputs,
            }
        )
    preflight = preflight_batch(batch, batch.owner, template)
    return {
        "id": str(batch.id),
        "name": batch.name,
        "platform": batch.platform,
        "market": batch.market or batch.site,
        "sellerTier": batch.seller_tier,
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
