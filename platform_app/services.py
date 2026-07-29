import base64
import copy
import hashlib
import json
import mimetypes
import re
import uuid
from io import BytesIO
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import transaction
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
        output_template = ensure_default_template(platform, market)
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


def ensure_default_template(platform="shopee", site="SG"):
    template = OutputTemplate.objects.filter(
        platform=platform,
        site=site,
        name="Default one image set",
        status=OutputTemplate.Status.PUBLISHED,
    ).first()
    if template is None:
        template = OutputTemplate.objects.create(
            platform=platform,
            site=site,
            name="Default one image set",
            default_size="1:1",
            default_resolution="1k",
        )
    OutputSlot.objects.get_or_create(
        template=template,
        order=1,
        defaults={"name": "main", "purpose": "Main ecommerce product image"},
    )
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
    prompt = "\n".join(prompt_lines)
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
    template = template or batch.output_template or ensure_default_template(batch.platform, batch.site)
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
    locked_batch = Batch.objects.select_for_update().select_related("output_template", "rule_profile").get(id=batch.id)
    existing = list(locked_batch.generations.order_by("created_at", "id"))
    if locked_batch.confirmed_generation_key and existing:
        return existing

    template = template or locked_batch.output_template or ensure_default_template(locked_batch.platform, locked_batch.site)
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


ACTIVE_GENERATION_STATUSES = {
    Generation.Status.QUEUED,
    Generation.Status.PREPARING,
    Generation.Status.SUBMITTING,
    Generation.Status.SUBMITTED,
    Generation.Status.PROCESSING,
    Generation.Status.ARCHIVING,
}


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
    return Generation.objects.create(**values)


@transaction.atomic
def retry_failed_generation(generation, user):
    Batch.objects.select_for_update().get(id=generation.batch_id)
    Cluster.objects.select_for_update().get(id=generation.cluster_id)
    locked = (
        Generation.objects.select_for_update()
        .select_related("batch", "cluster", "output_slot", "prompt_version")
        .get(id=generation.id)
    )
    if locked.status != Generation.Status.FAILED:
        raise ValueError("Only failed generations can be retried")
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
        prompt_version = PromptVersion.objects.create(
            cluster=locked.cluster,
            created_by=reviewer,
            node_name=previous.node_name if previous else "slot_prompt",
            template_version=previous.template_version if previous else "builtin-v1",
            provider_model=previous.provider_model if previous else "gpt-image-2",
            prompt_text="\n".join(delta_lines),
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
                "name": cluster.product_name or cluster.name,
                "assetIds": [str(item.asset_id) for item in cluster_assets],
                "assets": sku_assets,
                "facts": cluster.product_facts,
                "identityLock": cluster.identity_lock,
                "brief": cluster.prompt_override,
                "outputs": outputs,
            }
        )
    return {
        "id": str(batch.id),
        "name": batch.name,
        "platform": batch.platform,
        "market": batch.market or batch.site,
        "template": batch.output_template.name if batch.output_template_id else "",
        "size": batch.size,
        "status": _project_status(batch.status),
        "updatedAt": batch.updated_at.isoformat(),
        "assets": list(serialized_assets.values()),
        "skus": skus,
    }
