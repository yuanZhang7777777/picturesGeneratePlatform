import copy
import hashlib
import ipaddress
import json
import re
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
from pathlib import Path, PurePosixPath
from threading import Lock
from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, transaction
from django.db.models import F, Max, Prefetch, Q
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
from .prompt_templates_v3 import MARKETING_STRATEGY_MODES, NODE_TEMPERATURES
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
SUPPORTED_PLATFORMS = {"generic", "shopee", "tiktok", "tiktok_shop"}
SUPPORTED_SIZES = {"1:1", "3:4"}
SUPPORTED_RESOLUTIONS = {"1k", "2k"}
PROMPT_GENERATION_FAILED_MESSAGE = "提示词生成失败，请重试预备生成"
STYLE_DNA_VALUES = {
    "composition": {"centered", "top-left", "top-right", "bottom-left", "bottom-right", "rule-of-thirds", "symmetrical", "negative-space"},
    "lighting": {"soft daylight", "natural daylight", "diffused studio", "softbox", "high key", "low key"},
    "color": {"warm neutral", "cool neutral", "muted", "pastel", "monochrome", "black and white"},
    "scene_density": {"minimal", "sparse", "balanced", "full"},
    "camera": {"eye level", "top-down", "45-degree", "macro", "wide", "close-up"},
}
STYLE_DNA_FIELDS = set(STYLE_DNA_VALUES)
_UNSET = object()
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]+")
INFANT_KEYWORDS = ("baby", "infant", "newborn", "toddler", "婴", "幼儿", "宝宝")
ADULT_KEYWORDS = ("adult", "clothing", "apparel", "beauty", "fashion", "成人", "服装", "美容")
TEST_NODE_INSTRUCTIONS = {
    "N1": "TEST N1: observe visible product identity and return the complete N1 JSON contract.",
    "N2": "TEST N2: merge owned observations and return the complete N2 identity JSON contract.",
    "N3": "TEST N3: classify traceable facts and return the complete N3 ledger JSON contract.",
    "N4": "TEST N4: compile the standard white-background hero JSON contract.",
    "N5": "TEST N5: plan a diverse marketing set and return the complete N5 JSON contract.",
    "N6": "TEST N6: compile one localized marketing-slot JSON contract.",
    "N7": "TEST N7: evaluate the final prompt and references against deterministic rules.",
    "N8": "TEST N8: compile one bounded review revision JSON contract.",
    "N9": "TEST N9: simplify one failed prompt without changing identity or hard rules.",
}
MARKETING_DIVERSITY_MAX_SHARE = {
    "scene_family": 0.5,
    "environment": 0.5,
    "camera": 0.5,
    "main_action": 0.5,
    "composition": 0.5,
}
_MARKETING_STRATEGY_MODE_SET = set(MARKETING_STRATEGY_MODES)
_APIMART_UPLOAD_URL_CACHE = {}
_APIMART_UPLOAD_URL_CACHE_LOCK = Lock()


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


def _validate_prompt_temperature(temperature):
    try:
        value = float(temperature)
    except (TypeError, ValueError) as exc:
        raise ProviderError("APIMart prompt temperature must be between 0 and 2") from exc
    if value < 0 or value > 2:
        raise ProviderError("APIMart prompt temperature must be between 0 and 2")
    return value


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
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [redacted]", text)
    lower = text.lower()
    if "api key" in lower or "authentication" in lower or "unauthorized" in lower:
        return "模型服务鉴权失败，请联系管理员"
    if "read timed out" in lower or "read timeout" in lower or "timed out" in lower or "timeout" in lower:
        return "模型服务响应超时，请重试预备生成"
    if "rate limit" in lower or "too many requests" in lower or "429" in lower:
        return "模型服务限流，请稍后重试"
    if "provider chat response did not include" in lower or "message content" in lower or "output_text" in lower:
        return "模型服务返回内容为空或格式异常，请重试预备生成"
    if "provider returned invalid json" in lower:
        return "模型服务返回无法解析，请重试预备生成"
    internal_tokens = (
        "expecting value",
        "line 1 column 1",
        "char 0",
        "image_role",
        "visible product identity",
        "evidence_refs",
        "json",
        "schema",
        "n1 output",
        "n2 output",
        "n3 output",
        "n2 may",
    )
    if any(token in text.lower() for token in internal_tokens):
        return "系统识别异常，请重试预备生成"
    return text


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
    if response.status_code == 429:
        raise RateLimited("模型服务限流，请稍后重试")
    if response.status_code in {401, 403}:
        raise ProviderError("模型服务鉴权失败，请联系管理员")
    if response.status_code >= 500:
        raise ProviderError("模型服务临时异常，请重试预备生成")
    raise ProviderError(_sanitize_provider_text(message))


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
        match = re.search(r"^ASSET_ID=(.+)$", instruction, re.MULTILINE)
        return {
            "output_text": json.dumps(
                {
                    "asset_id": match.group(1).strip() if match else "",
                    "asset_kind": "owned_product",
                    "image_role": "clean_product",
                    "contains_target_product": True,
                    "target_is_physical_product": True,
                    "target_visibility": 90,
                    "target_complete": True,
                    "background_complexity": "low",
                    "observed_identity": {
                        "category_candidates": ["product"],
                        "dominant_colors": [],
                        "overall_shape": "visible product silhouette",
                        "visible_material_cues": [],
                        "logos_or_markings": [],
                        "controls_ports_connectors": [],
                        "distinctive_parts": [],
                        "count_observations": [],
                    },
                    "observed_use_relationships": [],
                    "non_target_objects": [],
                    "package_or_text_clues": [],
                    "conflicts_with_confirmed_points": [],
                    "reference_quality": 90,
                    "recommended_use": "reuse",
                    "style_dna": None,
                    "reason": "",
                    "candidate_product_name": "Demo product",
                    "candidate_product_name_confidence": 0.9,
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
            product_observations = [
                item
                for item in observations
                if item.get("contains_target_product")
                and item.get("target_is_physical_product")
                and item.get("asset_id")
            ]
            primary = product_observations[0].get("asset_id") if product_observations else None
            observed = product_observations[0] if product_observations else {}
            observed_facts = _string_list(observed.get("product_facts") or observed.get("facts"))
            output = {
                "decision": "continue",
                "confidence": 90,
                "needs_input_reason": "",
                "product_name": node_input.get("product_name") or "Demo product",
                "conflict_state": "unknown",
                "product_profile": {
                    "category": "product",
                    "primary_appearance": "; ".join(observed_facts) or "visible reference",
                    "shared_structure": [],
                    "visible_fixed_counts": [],
                    "verified_use_relationships": [],
                    "included_items": [],
                    "other_variants": [],
                    "known_conflicts": [],
                },
                "identity_lock": {
                    "family_invariants": [],
                    "primary_variant_attributes": [],
                    "exact_component_constraints": [],
                    "verified_hidden_or_internal_structure": [],
                    "use_relationship_constraints": [],
                    "must_not_change": [
                        str(observed.get("identity_lock") or "visible product identity")
                    ]
                },
                "primary_asset_id": primary,
                "supporting_asset_ids": [
                    item.get("asset_id") for item in product_observations[1:4]
                ],
                "target_appearances": [
                    {
                        "appearance_id": f"appearance.{index + 1:03d}",
                        "label": "; ".join(_string_list(item.get("product_facts") or item.get("facts"))),
                        "variant_attributes": [],
                        "asset_ids": [item.get("asset_id")],
                        "primary_asset_id": item.get("asset_id"),
                    }
                    for index, item in enumerate(product_observations)
                ],
                "standardization_mode": "reuse",
                "standardization_reason": "",
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
                        "review_note": "",
                    }
                ],
                "blocked_claim_topics": ["price", "certification", "medical_efficacy"],
                "unresolved_questions": [],
                "review_summary": {
                    "confirmed_count": 1,
                    "observed_count": 0,
                    "inferred_count": 0,
                    "high_risk_count": 0,
                },
            }
        elif "NODE N4" in text:
            fact_ids = [
                item.get("fact_id")
                for item in node_input.get("fact_ledger", {}).get("facts", [])
                if item.get("fact_id")
            ]
            output = {
                "slot_id": str(node_input.get("slot_order")),
                "main_scene": "pure white commercial studio",
                "main_action": "none",
                "visible_text_lines": [],
                "prompt": "Show the complete accurate product on pure white.",
                "character_count": 49,
                "reference_plan": {
                    "primary_asset_id": node_input.get("primary_asset_id"),
                    "supporting_asset_ids": node_input.get("supporting_asset_ids", []),
                    "include_completed_white_image": False,
                },
                "fact_trace": fact_ids,
                "inference_trace": [],
                "rule_refs": node_input.get("rule_refs", []),
                "generation_parameters": {
                    "model": "gpt-image-2",
                    "n": 1,
                    "size": node_input.get("size", "1:1"),
                    "resolution": node_input.get("resolution", "1k"),
                },
                "review_required": True,
            }
        elif "NODE N5" in text:
            fact_ids = [
                item.get("fact_id")
                for item in node_input.get("fact_ledger", {}).get("facts", [])
                if item.get("fact_id")
            ]
            modes = [
                "fab_value",
                "scene_ownership",
                "emotion",
                "personification",
                "identity_signal",
                "fab_value",
                "scene_ownership",
                "emotion",
            ]
            output = {
                "plans": [
                    {
                        "slot_order": slot["slot_order"],
                        "role": slot.get("purpose") or slot.get("name") or f"slot-{slot['slot_order']}",
                        "appearance_ids": [
                            item.get("appearance_id")
                            for item in node_input.get("target_appearances", [])
                            if item.get("appearance_id")
                        ],
                        "scene_family": f"scene-{slot['slot_order']}",
                        "environment": f"environment-{slot['slot_order']}",
                        "camera": f"camera-{slot['slot_order']}",
                        "decision_task": slot.get("purpose") or f"decision-{slot['slot_order']}",
                        "conversion_goal": slot.get("purpose") or f"decision-{slot['slot_order']}",
                        "fact_refs": [],
                        "inference_refs": [],
                        "main_scene": f"distinct scene {slot['slot_order']}",
                        "main_action": f"action-{slot['slot_order']}",
                        "subject_relationship": "product is the clear subject",
                        "composition": f"composition-{slot['slot_order']}",
                        "copy_intent": "",
                        "text_mode": "up_to_3_lines",
                        "localization_notes": [],
                        "must_show": [],
                        "must_avoid": [],
                        "visible_text_lines": [],
                        "creative_strategy": {
                            "mode": modes[index % len(modes)],
                            "source_fact_refs": fact_ids[:1],
                            "user_job": slot.get("purpose") or f"decision-{slot['slot_order']}",
                            "consumer_tension": "The shopper needs a concrete reason to choose this product.",
                            "feature": "visible product reference",
                            "advantage": "the product is easy to understand visually",
                            "consumer_benefit": "the shopper can picture using it with confidence",
                            "mental_simulation": f"imagine the product in scene {slot['slot_order']}",
                            "emotional_shift": "from uncertainty to confidence",
                            "product_voice": "",
                            "identity_signal": "",
                            "selection_reason": "fake mode keeps the marketing plan schema complete",
                        },
                    }
                    for index, slot in enumerate(node_input.get("slots", []))
                ]
            }
        elif "NODE N6" in text:
            slot_order = node_input.get("slot_order")
            fact_ids = [
                item.get("fact_id")
                for item in node_input.get("fact_ledger", {}).get("facts", [])
                if item.get("fact_id")
            ]
            prompt = f"Create demo ecommerce product image slot {slot_order}."
            display_prompt = f"生成一张 1:1 商品营销图，槽位 {slot_order} 用清楚的商品主体、具体使用画面、自然光线、材质细节和简洁目标语言标题表达当前卖点。"
            output = {
                "slot_id": str(slot_order),
                "main_scene": node_input.get("slot_plan", {}).get("main_scene", "clean ecommerce scene"),
                "main_action": node_input.get("slot_plan", {}).get("main_action", "none"),
                "display_prompt": display_prompt,
                "visible_text_lines": node_input.get("slot_plan", {}).get("visible_text_lines", []),
                "localized_copy": {
                    "language": node_input.get("market_context", {}).get("language", "en"),
                    "lines": node_input.get("slot_plan", {}).get("visible_text_lines", []),
                    "back_translation": "",
                    "strategy_mode": node_input.get("slot_plan", {}).get("creative_strategy", {}).get("mode", "fab_value"),
                    "source_fact_refs": [],
                    "source_inference_refs": [],
                    "quality": {
                        "relevance": 90,
                        "specificity": 90,
                        "imagery": 90,
                        "naturalness": 90,
                        "truthfulness": 100,
                        "mobile_readability": 90,
                        "generic_phrase_hits": [],
                    },
                },
                "prompt": prompt,
                "character_count": len(prompt),
                "reference_plan": {
                    "primary_asset_id": node_input.get("primary_asset_id"),
                    "supporting_asset_ids": node_input.get("supporting_asset_ids", []),
                    "completed_white_result_id": None,
                },
                "fact_trace": fact_ids,
                "inference_trace": [],
                "rule_refs": node_input.get("rule_refs", []),
                "generation_parameters": {
                    "model": "gpt-image-2",
                    "n": 1,
                    "size": node_input.get("size", "1:1"),
                    "resolution": node_input.get("resolution", "1k"),
                },
                "review_required": True,
            }
        elif "NODE N7" in text:
            deterministic = node_input.get("deterministic_gate", {})
            hard_blocks = list(deterministic.get("hard_blocks", []))
            output = {
                "decision": "block" if hard_blocks else "pass",
                "hard_blocks": hard_blocks,
                "semantic_risks": [],
                "warnings": [],
                "copy_checks": {
                    "lines_match_visible_text": True,
                    "each_line_present_once": True,
                    "language_match": True,
                    "fact_refs_valid": True,
                    "generic_phrase_hits": [],
                },
                "prompt_checks": {
                    "character_count": len(str(node_input.get("prompt") or "")),
                    "text_line_count": 0,
                    "main_scene_count": 1,
                    "main_action_count": 1,
                    "reference_assets_valid": True,
                },
                "resolved_rule_refs": list(deterministic.get("resolved_rule_refs", [])),
                "review_required": True,
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

    @property
    def deepseek_auth_headers(self):
        if not settings.DEEPSEEK_API_KEY:
            raise ProviderError("DeepSeek 官方接口密钥未配置，请配置 DEEPSEEK_API_KEY 后重试")
        return {"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"}

    @property
    def deepseek_headers(self):
        return {
            **self.deepseek_auth_headers,
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

    def _deepseek_url(self, path):
        base = settings.DEEPSEEK_BASE_URL.rstrip("/")
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

    def submit_generation(self, prompt, image_paths, size, resolution, image_urls=None):
        payload = {
            "model": settings.APIMART_IMAGE_MODEL,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "resolution": resolution,
            "official_fallback": False,
        }
        if image_urls:
            payload["image_urls"] = list(image_urls)
        elif image_paths:
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

    def complete_chat(self, messages, *, model=None, temperature=None):
        payload = {
            "model": model or settings.DEEPSEEK_PROMPT_MODEL,
            "stream": False,
            "messages": messages,
        }
        if temperature is not None:
            payload["temperature"] = _validate_prompt_temperature(temperature)
        reasoning_effort = str(getattr(settings, "DEEPSEEK_REASONING_EFFORT", "") or "").strip()
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        if getattr(settings, "DEEPSEEK_THINKING_ENABLED", True):
            payload["thinking"] = {"type": "enabled"}
        last_timeout = None
        last_payload = {}
        for _attempt in range(2):
            try:
                response = self.session.post(
                    self._deepseek_url("/chat/completions"),
                    json=payload,
                    headers=self.deepseek_headers,
                    timeout=settings.APIMART_PROMPT_TIMEOUT_SECONDS,
                )
            except requests.Timeout as exc:
                last_timeout = exc
                continue
            except requests.RequestException as exc:
                raise ProviderError(_sanitize_provider_text(str(exc))) from exc
            last_payload = self._json(response)
            content = self._chat_output_text(last_payload)
            if content:
                return {"output_text": content, "raw": last_payload}
        if last_timeout is not None:
            raise ProviderError("模型服务响应超时，请重试预备生成") from last_timeout
        raise ProviderError("模型服务暂时没有返回正文，请重试预备生成")

    def _content_text(self, content):
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, dict):
            return self._content_text(
                content.get("text")
                or content.get("content")
                or content.get("output_text")
                or content.get("reasoning_content")
                or content.get("reasoning")
            )
        if not isinstance(content, list):
            return ""
        parts = []
        for item in content:
            text = self._content_text(item)
            if text:
                parts.append(text)
        return "\n".join(part.strip() for part in parts if part and part.strip())

    def _chat_output_text(self, payload):
        if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
            return payload["output_text"].strip()
        choices = payload.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
                for value in (
                    message.get("content"),
                    message.get("reasoning_content"),
                    message.get("reasoning"),
                    message.get("text"),
                    message.get("output_text"),
                    choice.get("text"),
                    choice.get("content"),
                    choice.get("output_text"),
                ):
                    text = self._content_text(value)
                    if text:
                        return text
        return self._responses_output_text(payload).strip()

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
            ],
            temperature=payload.get("temperature"),
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
    market="SEA",
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
    platform = str(platform or "").strip()
    market = str(market or "").strip().upper()
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


def _default_config(batch):
    return {
        "platform": batch.platform,
        "market": batch.market or batch.site,
        "sellerTier": batch.seller_tier or Batch.SellerTier.GENERAL,
        "size": batch.size or "1:1",
        "resolution": batch.resolution or "1k",
        "globalPrompt": batch.global_prompt or "",
    }


def _effective_config(batch, cluster):
    defaults = _default_config(batch)
    platform = cluster.platform_override if cluster.platform_override is not None else (defaults["platform"] or "global")
    seller_tier = (
        cluster.seller_tier_override
        if cluster.seller_tier_override is not None
        else defaults["sellerTier"]
    )
    return {
        "platform": platform,
        "market": cluster.market_override if cluster.market_override is not None else defaults["market"],
        "sellerTier": seller_tier if platform == "shopee" else Batch.SellerTier.GENERAL,
        "size": defaults["size"],
        "resolution": defaults["resolution"],
        "globalPrompt": cluster.prompt_override.strip() or defaults["globalPrompt"],
    }


def _effective_cluster_resources(batch, cluster):
    config = _effective_config(batch, cluster)
    has_resource_override = any(
        value is not None
        for value in (
            cluster.platform_override,
            cluster.market_override,
            cluster.seller_tier_override,
        )
    )
    template = (
        _default_output_template(
            config["platform"],
            config["market"],
            config["sellerTier"],
        )
        if has_resource_override
        else batch.output_template
        or _default_output_template(
            config["platform"],
            config["market"],
            config["sellerTier"],
        )
    )
    rule_profile = (
        _default_rule_profile(config["platform"], config["market"])
        if has_resource_override
        else batch.rule_profile
        or _default_rule_profile(config["platform"], config["market"])
    )
    return template, rule_profile, config


def _required_config_value(payload, field, *, uppercase=False):
    value = payload.get(field)
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value.upper() if uppercase else value


def _optional_config_value(payload, field, current, fallback):
    value = payload.get(field, current)
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    value = value.strip()
    if field == "resolution":
        value = value.lower()
    if not value:
        return current or fallback
    allowed = {"size": SUPPORTED_SIZES, "resolution": SUPPORTED_RESOLUTIONS}[field]
    if value not in allowed:
        raise ValueError(f"unsupported {field}")
    return value


def _preparation_revision(snapshot):
    try:
        return int((snapshot or {}).get("_preparation_revision", 0))
    except (AttributeError, TypeError, ValueError):
        return 0


def _active_sealed_submission_exists(cluster_id=None):
    queryset = Generation.objects.filter(status=Generation.Status.SUBMITTING)
    if cluster_id is not None:
        queryset = queryset.filter(cluster_id=cluster_id)
    return any(
        isinstance(payload, dict) and isinstance(payload.get("submission"), dict)
        for payload in queryset.values_list("provider_payload", flat=True)
    )


def _invalidate_preparation(cluster):
    if cluster.pk and _active_sealed_submission_exists(cluster.id):
        raise ValueError("Product has an active submission")
    snapshot = copy.deepcopy(cluster.analysis_snapshot) if isinstance(cluster.analysis_snapshot, dict) else {}
    snapshot["_preparation_revision"] = _preparation_revision(snapshot) + 1
    cluster.analysis_snapshot = snapshot
    cluster.preparation_status = Cluster.PreparationStatus.DRAFT
    cluster.preparation_error = ""
    cluster.preparation_stage = "draft"
    cluster.preparation_current = 0
    cluster.preparation_total = 7
    cluster.auto_generate = False


def _preparation_is_current(cluster_id, revision):
    current = Cluster.objects.filter(id=cluster_id).values("preparation_status", "analysis_snapshot").first()
    return bool(
        current
        and current["preparation_status"] == Cluster.PreparationStatus.PREPARING
        and _preparation_revision(current["analysis_snapshot"]) == revision
    )


def _persist_prompt_terminal(
    cluster_id,
    claimed_revision,
    prompt_values,
    analysis,
    status,
    error,
    user,
    *,
    cluster_updates=None,
):
    with transaction.atomic():
        locked = Cluster.objects.select_for_update().get(id=cluster_id)
        if locked.preparation_status != Cluster.PreparationStatus.PREPARING or _preparation_revision(locked.analysis_snapshot) != claimed_revision:
            return False
        for values in prompt_values:
            PromptVersion.objects.create(cluster=locked, created_by=user, **values)
        previous = locked.analysis_snapshot if isinstance(locked.analysis_snapshot, dict) else {}
        history = copy.deepcopy(previous.get("_preparation_history", []))
        if previous.get("identity") or previous.get("prompt_os"):
            history.append(
                {
                    "preparation_revision": _preparation_revision(previous),
                    "observations": copy.deepcopy(previous.get("observations", [])),
                    "identity": copy.deepcopy(previous.get("identity", {})),
                    "fact_ledger": copy.deepcopy(previous.get("fact_ledger", {})),
                    "marketing_plan": copy.deepcopy(previous.get("marketing_plan", {})),
                    "prompt_os": copy.deepcopy(previous.get("prompt_os", [])),
                }
            )
        if history:
            analysis["_preparation_history"] = history
        update_fields = [
            "analysis_snapshot",
            "preparation_status",
            "preparation_error",
            "preparation_stage",
            "preparation_current",
            "preparation_total",
            "updated_at",
        ]
        for field, value in (cluster_updates or {}).items():
            setattr(locked, field, value)
            update_fields.append(field)
        locked.analysis_snapshot = analysis
        locked.preparation_status = status
        locked.preparation_error = error
        if status == Cluster.PreparationStatus.READY:
            locked.preparation_stage = "ready"
            locked.preparation_current = locked.preparation_total
        elif status == Cluster.PreparationStatus.BLOCKED:
            locked.preparation_stage = "blocked"
        locked.save(update_fields=list(dict.fromkeys(update_fields)))
        return True


def _set_preparation_progress(cluster_id, revision, stage, current):
    snapshot = Cluster.objects.filter(
        id=cluster_id,
        preparation_status=Cluster.PreparationStatus.PREPARING,
    ).values_list("analysis_snapshot", flat=True).first()
    if _preparation_revision(snapshot) != revision:
        return
    Cluster.objects.filter(id=cluster_id).update(
        preparation_stage=stage,
        preparation_current=current,
        preparation_total=7,
        updated_at=timezone.now(),
    )


def _configuration_signature(batch, cluster):
    template, rule_profile, config = _effective_cluster_resources(batch, cluster)
    has_resource_override = any(
        value is not None
        for value in (
            cluster.platform_override,
            cluster.market_override,
            cluster.seller_tier_override,
        )
    )
    return (
        config,
        template.id if has_resource_override and template is not None else batch.output_template_id,
        (
            rule_profile.id
            if has_resource_override and rule_profile is not None
            else batch.rule_profile_id
        ),
    )


@transaction.atomic
def update_project_settings(batch, payload):
    if not isinstance(payload, dict):
        raise TypeError("request body must be an object")
    locked = Batch.objects.select_for_update().get(id=batch.id)
    platform = _required_config_value(payload, "platform")
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError("unsupported platform")
    market = _required_config_value(payload, "market", uppercase=True)
    seller_tier = str(payload.get("seller_tier", locked.seller_tier or Batch.SellerTier.GENERAL)).strip().lower()
    if seller_tier not in Batch.SellerTier.values:
        raise ValueError("seller_tier must be general or mall")
    if platform != "shopee":
        seller_tier = Batch.SellerTier.GENERAL
    output_template = _default_output_template(platform, market, seller_tier)
    rule_profile = _default_rule_profile(platform, market)
    size = _optional_config_value(payload, "size", locked.size, "1:1")
    resolution = _optional_config_value(payload, "resolution", locked.resolution, "1k")
    global_prompt = payload.get("global_prompt", locked.global_prompt)
    if not isinstance(global_prompt, str):
        raise TypeError("global_prompt must be a string")

    clusters = list(locked.clusters.select_for_update().filter(archived_at__isnull=True))
    before = {cluster.id: _configuration_signature(locked, cluster) for cluster in clusters}
    locked.platform = platform
    locked.site = market
    locked.market = market
    locked.seller_tier = seller_tier
    locked.output_template = output_template
    locked.rule_profile = rule_profile
    locked.size = size
    locked.resolution = resolution
    locked.global_prompt = global_prompt
    locked.save(
        update_fields=[
            "platform",
            "site",
            "market",
            "seller_tier",
            "output_template",
            "rule_profile",
            "size",
            "resolution",
            "global_prompt",
            "updated_at",
        ]
    )
    for cluster in clusters:
        if before[cluster.id] == _configuration_signature(locked, cluster):
            continue
        _invalidate_preparation(cluster)
        cluster.save(
            update_fields=[
                "preparation_status",
                "preparation_error",
                "preparation_stage",
                "preparation_current",
                "preparation_total",
                "analysis_snapshot",
                "auto_generate",
                "updated_at",
            ]
        )
    return locked


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


def _ensure_cluster_mutable(cluster):
    if cluster.archived_at is not None:
        raise ValueError("Product is archived")
    if cluster.preparation_status == Cluster.PreparationStatus.PREPARING:
        raise ValueError("Product is being prepared")


@transaction.atomic
def request_cluster_preparation(cluster, *, auto_generate):
    locked = Cluster.objects.select_for_update().get(id=cluster.id)
    _ensure_cluster_mutable(locked)
    locked.auto_generate = bool(auto_generate)
    locked.preparation_status = Cluster.PreparationStatus.PENDING
    locked.preparation_error = ""
    locked.preparation_stage = "queued"
    locked.preparation_current = 0
    locked.preparation_total = 7
    locked.save(
        update_fields=[
            "auto_generate",
            "preparation_status",
            "preparation_error",
            "preparation_stage",
            "preparation_current",
            "preparation_total",
            "updated_at",
        ]
    )
    return locked


@transaction.atomic
def record_cluster_auto_generate(cluster):
    batch = Batch.objects.select_for_update().get(id=cluster.batch_id)
    locked = Cluster.objects.select_for_update().get(id=cluster.id, batch_id=batch.id)
    if locked.archived_at is not None:
        raise ValueError("Product is archived")
    if locked.preparation_status not in {
        Cluster.PreparationStatus.PENDING,
        Cluster.PreparationStatus.PREPARING,
    }:
        raise ValueError("Product preparation is not waiting")
    if not locked.auto_generate:
        locked.auto_generate = True
        locked.save(update_fields=["auto_generate", "updated_at"])
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
    if mode == Batch.ImportMode.AUTO:
        request_cluster_preparation(cluster, auto_generate=True)
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
            existing_cluster = Cluster.objects.filter(batch=batch, sku=sku).only("archived_at").first()
            if existing_cluster is not None and existing_cluster.archived_at is not None:
                error = "Product is archived"
            elif existing_cluster is None:
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
                    if cluster is not None and cluster.archived_at is not None:
                        item = _create_sku_import_item(
                            locked_batch,
                            sku,
                            product_name,
                            SkuImportItem.Status.FAILED,
                            error_message="Product is archived",
                        )
                    elif error:
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
                                analysis_snapshot={"product_name_source": "erp"},
                            )
                            ClusterAsset.objects.create(
                                cluster=cluster,
                                asset=asset,
                                role=ClusterAsset.Role.PRIMARY,
                                order=1,
                            )
                            if mode == Batch.ImportMode.AUTO:
                                request_cluster_preparation(cluster, auto_generate=True)
                            if locked_batch.status == Batch.Status.DRAFT:
                                locked_batch.status = Batch.Status.ORGANIZING
                        else:
                            if mode == Batch.ImportMode.AUTO:
                                request_cluster_preparation(cluster, auto_generate=True)
                        if locked_batch.status == Batch.Status.DRAFT or locked_batch.last_import_mode != mode:
                            locked_batch.status = Batch.Status.ORGANIZING
                            locked_batch.last_import_mode = mode
                            locked_batch.save(update_fields=["status", "last_import_mode", "updated_at"])
                        if (
                            product_name
                            and cluster.product_name in {"", "名称待确认"}
                            and cluster.product_name != product_name
                        ):
                            cluster.product_name = product_name
                            cluster.name = product_name
                            analysis = copy.deepcopy(cluster.analysis_snapshot)
                            analysis["product_name_source"] = "erp"
                            cluster.analysis_snapshot = analysis
                            cluster.version += 1
                            cluster.save(
                                update_fields=[
                                    "product_name",
                                    "name",
                                    "analysis_snapshot",
                                    "version",
                                    "updated_at",
                                ]
                            )
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
    relations = list(cluster.cluster_assets.order_by("order", "id"))
    if not relations:
        cluster.delete()
        return
    for index, relation in enumerate(relations, start=1):
        role = ClusterAsset.Role.PRIMARY if index == 1 else ClusterAsset.Role.REFERENCE
        if relation.order != index or relation.role != role:
            relation.order = index
            relation.role = role
            relation.save(update_fields=["role", "order"])
    if len(relations) == 1:
        cluster.relation_type = Cluster.RelationType.SINGLE_PRODUCT
    cluster.version += 1
    _invalidate_preparation(cluster)
    cluster.save(
        update_fields=[
            "version",
            "preparation_status",
            "preparation_error",
            "preparation_stage",
            "preparation_current",
            "preparation_total",
            "analysis_snapshot",
            "auto_generate",
            "relation_type",
            "updated_at",
        ]
    )


def _active_generation_exists(cluster):
    return cluster.generations.filter(status__in=ACTIVE_GENERATION_STATUSES).exists()


@transaction.atomic
def archive_or_delete_cluster(cluster):
    locked = Cluster.objects.select_for_update().get(id=cluster.id)
    _ensure_cluster_mutable(locked)
    if _active_generation_exists(locked):
        raise ValueError("Product has an active generation")
    asset_ids = list(locked.cluster_assets.order_by("asset_id").values_list("asset_id", flat=True))
    assets = list(Asset.objects.select_for_update().filter(id__in=asset_ids).order_by("id"))
    if locked.generations.exists():
        archived_at = timezone.now()
        locked.archived_at = archived_at
        locked.save(update_fields=["archived_at", "updated_at"])
        Asset.objects.filter(id__in=[asset.id for asset in assets]).update(archived_at=archived_at)
        return "archived"
    storage_paths = [asset.storage_path for asset in assets]
    asset_ids = [asset.id for asset in assets]
    locked.delete()
    Asset.objects.filter(id__in=asset_ids).delete()
    transaction.on_commit(lambda: [LocalStorage().delete(path) for path in storage_paths], robust=True)
    return "deleted"


@transaction.atomic
def remove_asset_from_cluster(asset):
    cluster_id = ClusterAsset.objects.filter(asset_id=asset.id).values_list("cluster_id", flat=True).first()
    if cluster_id is None:
        locked_asset = Asset.objects.select_for_update().get(id=asset.id)
        if locked_asset.archived_at is not None:
            raise ValueError("Asset is archived")
        storage_path = locked_asset.storage_path
        locked_asset.delete()
        transaction.on_commit(lambda: LocalStorage().delete(storage_path), robust=True)
        return "deleted"
    cluster = Cluster.objects.select_for_update().get(id=cluster_id)
    _ensure_cluster_mutable(cluster)
    locked_asset = Asset.objects.select_for_update().get(id=asset.id)
    if locked_asset.archived_at is not None:
        raise ValueError("Asset is archived")
    relation = ClusterAsset.objects.filter(cluster=cluster, asset=locked_asset).first()
    if relation is None:
        raise ValueError("Asset changed; refresh before deleting")
    if _active_generation_exists(cluster):
        raise ValueError("Product has an active generation")
    if cluster.cluster_assets.count() == 1:
        return archive_or_delete_cluster(cluster)
    relation.delete()
    if cluster.generations.exists():
        locked_asset.archived_at = timezone.now()
        locked_asset.save(update_fields=["archived_at"])
    else:
        storage_path = locked_asset.storage_path
        locked_asset.delete()
        transaction.on_commit(lambda: LocalStorage().delete(storage_path), robust=True)
    _promote_primary_if_needed(cluster)
    return "archived" if cluster.generations.exists() else "deleted"


@transaction.atomic
def merge_asset_into_cluster(asset, target_cluster, expected_version=None):
    target = Cluster.objects.select_for_update().get(id=target_cluster.id)
    _ensure_cluster_mutable(target)
    if asset.archived_at is not None:
        raise ValueError("Archived products cannot be changed")
    if expected_version is not None and target.version != expected_version:
        raise ValueError("Cluster changed; refresh before saving")
    old_cluster = None
    old_relation = ClusterAsset.objects.select_related("cluster").filter(asset=asset).first()
    if old_relation is not None and old_relation.cluster_id != target.id:
        old_cluster = Cluster.objects.select_for_update().get(id=old_relation.cluster_id)
        _ensure_cluster_mutable(old_cluster)
    relation = target.add_asset(asset)
    if target.relation_type == Cluster.RelationType.SINGLE_PRODUCT:
        target.relation_type = Cluster.RelationType.SAME_PRODUCT
    _invalidate_preparation(target)
    target.save(
        update_fields=[
            "preparation_status",
            "preparation_error",
            "preparation_stage",
            "preparation_current",
            "preparation_total",
            "analysis_snapshot",
            "auto_generate",
            "relation_type",
            "updated_at",
        ]
    )
    if old_cluster is not None:
        _promote_primary_if_needed(old_cluster)
    return relation


@transaction.atomic
def move_asset_to_new_cluster(asset):
    if asset.archived_at is not None:
        raise ValueError("Archived assets cannot be changed")
    old_relation = ClusterAsset.objects.select_related("cluster").filter(asset=asset).first()
    old_cluster = None
    if old_relation is not None:
        old_cluster = Cluster.objects.select_for_update().get(id=old_relation.cluster_id)
        _ensure_cluster_mutable(old_cluster)
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
        if _active_sealed_submission_exists():
            raise ValueError("A generation has an active submission")
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
        "status": template.status,
        "platform": template.platform,
        "site": template.site,
        "default_size": template.default_size,
        "default_resolution": template.default_resolution,
        "slot": {"id": str(slot.id), "name": slot.name, "order": slot.order, "purpose": slot.purpose},
    }


def _rule_snapshot(rule_profile):
    if rule_profile is None:
        return {}
    return {
        "id": str(rule_profile.id),
        "name": rule_profile.name,
        "version": rule_profile.version,
        "status": rule_profile.status,
        "platform": rule_profile.platform,
        "site": rule_profile.site,
        "source_url": rule_profile.source_url,
        "checked_at": (
            rule_profile.checked_at.isoformat()
            if rule_profile.checked_at
            else None
        ),
        "rules": rule_profile.rules,
    }


def _slot_scope(slot):
    if is_source_product_photo_slot(slot):
        return "source"
    return "cover" if is_standard_product_hero_slot(slot) else "gallery"


def _applicable_rules(
    batch,
    slot,
    *,
    effective_config=None,
    rule_profile=None,
):
    config = effective_config or _default_config(batch)
    selected_profile = rule_profile if rule_profile is not None else batch.rule_profile
    cache = getattr(batch, "_prompt_rule_profiles_by_config", {})
    cache_key = (
        config["platform"],
        str(config.get("market") or "").upper(),
        str(config.get("sellerTier") or "general").lower(),
        str(selected_profile.id) if selected_profile is not None else None,
    )
    profiles = cache.get(cache_key)
    if profiles is None:
        profiles = []
        internal = RuleProfile.objects.filter(
            seed_key="global-marketplace-prompt-os-v2-rule",
            status=RuleProfile.Status.PUBLISHED,
        ).first()
        platform_baseline = RuleProfile.objects.filter(
            platform=config["platform"],
            site="",
            status=RuleProfile.Status.PUBLISHED,
        ).order_by("-checked_at", "-version", "-id").first()
        for profile in (internal, platform_baseline, selected_profile):
            if profile is not None and profile.id not in {item.id for item in profiles}:
                profiles.append(profile)
        cache[cache_key] = profiles
        batch._prompt_rule_profiles_by_config = cache
    market = str(config.get("market") or "").upper()
    scope = _slot_scope(slot)
    allowed_tiers = {"general", str(config.get("sellerTier") or "general").lower()}
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


GENERIC_COPY_PHRASES = {
    "a gift with taste",
    "a little joy every day",
    "clear value at a glance",
    "cute with character",
    "feels good to have around",
    "here to brighten your day",
    "made for moments you keep",
    "picture it in your day",
    "premium quality",
    "perfect for every moment",
    "everyday essential",
    "ideal choice",
    "take me home",
    "品质生活",
    "高级感",
    "理想之选",
    "轻松便携",
}


_HIGH_RISK_VISIBLE_COPY_RE = re.compile(
    r"(100\s*%|百分百|医疗|治疗|疗效|减肥|瘦身|认证|获奖|折扣|优惠|价格|包治|guarantee|certified|medical|cure|discount|price)",
    re.IGNORECASE,
)
_STRICT_SEMANTIC_BLOCK_RE = re.compile(
    r"(illegal|sexual|violence|hate|minor|terror|self[_\-. ]?harm)",
    re.IGNORECASE,
)


def _copy_lock_checks(prompt, visible_text_lines, localized_copy, fact_ids):
    localized_copy = localized_copy if isinstance(localized_copy, dict) else {}
    locked_lines = _string_list(localized_copy.get("lines"))
    source_fact_refs = _string_list(localized_copy.get("source_fact_refs"))
    known_fact_ids = {str(item) for item in (fact_ids or set())}
    generic_hits = [
        line
        for line in locked_lines
        if line.strip().lower() in GENERIC_COPY_PHRASES
    ]
    copy_checks = {
        "lines_match_visible_text": locked_lines == visible_text_lines,
        "each_line_present_once": all(prompt.count(line) == 1 for line in locked_lines),
        "language_match": True,
        "fact_refs_valid": all(ref in known_fact_ids for ref in source_fact_refs),
        "generic_phrase_hits": generic_hits,
    }
    warnings = []
    if localized_copy and (
        not copy_checks["lines_match_visible_text"]
        or not copy_checks["each_line_present_once"]
    ):
        warnings.append("copy.literal_lock")
    if localized_copy and not copy_checks["fact_refs_valid"]:
        warnings.append("copy.unknown_fact_ref")
    rewrite_reasons = ["copy.generic_or_repeated"] if generic_hits else []
    return copy_checks, warnings, rewrite_reasons


def evaluate_prompt_rule_gate(
    batch,
    slot,
    prompt,
    *,
    visible_text_lines=None,
    localized_copy=None,
    fact_ids=None,
    references=None,
    effective_config=None,
    rule_profile=None,
):
    visible_text_lines = [str(line).strip() for line in (visible_text_lines or []) if str(line).strip()]
    rules = _applicable_rules(
        batch,
        slot,
        effective_config=effective_config,
        rule_profile=rule_profile,
    )
    hard_blocks = []
    warnings = []
    if len(prompt) > 3500:
        hard_blocks.append("prompt.max_3500_characters")
    if len(visible_text_lines) > 3:
        warnings.append("prompt.visible_text_max_three_lines")
    if is_standard_product_hero_slot(slot) and visible_text_lines:
        warnings.append("hero.no_added_text")
    copy_checks, copy_warnings, rewrite_reasons = _copy_lock_checks(
        prompt,
        visible_text_lines,
        localized_copy,
        fact_ids,
    )
    warnings.extend(copy_warnings)
    if any(_HIGH_RISK_VISIBLE_COPY_RE.search(line) for line in visible_text_lines):
        warnings.append("copy.high_risk_claim")
    for rule in rules:
        rule_id = str(rule.get("rule_id") or "")
        severity = str(rule.get("severity") or "")
        if severity not in {"HARD_PLATFORM", "HARD_MALL"}:
            continue
        if rule_id.endswith(".no_added_text") and visible_text_lines:
            warnings.append(rule_id)
        if rule_id.endswith(".no_digital_rendering") and not is_source_product_photo_slot(slot):
            warnings.append(rule_id)
    return {
        "decision": "block" if hard_blocks else "pass",
        "hard_blocks": list(dict.fromkeys(hard_blocks)),
        "semantic_risks": [],
        "warnings": list(dict.fromkeys(warnings)),
        "resolved_rule_refs": [str(rule.get("rule_id")) for rule in rules if rule.get("rule_id")],
        "prompt_checks": {
            "character_count": len(prompt),
            "text_line_count": len(visible_text_lines),
            "main_scene_count": 1,
            "main_action_count": 1,
            "reference_assets_valid": all(isinstance(path, str) for path in (references or [])),
        },
        "copy_checks": copy_checks,
        "rewrite_reasons": rewrite_reasons,
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


def _prompt_node_contract(node_name):
    node_template = _published_prompt_node(node_name)
    if node_template is not None:
        return node_template.instruction, node_template.version
    if not settings.APIMART_FAKE_MODE:
        raise ValueError(f"published {node_name} prompt node template is required")
    instruction = TEST_NODE_INSTRUCTIONS.get(node_name.split(".", 1)[0])
    if not instruction:
        raise ValueError(f"test prompt node template is not defined for {node_name}")
    return instruction, "test-v1"


def _render_node_user_message(node_name, instruction, payload):
    node_template = _published_prompt_node(node_name)
    template = (
        node_template.user_message_template.strip()
        if node_template is not None
        else ""
    )
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    output_schema = (
        json.dumps(node_template.output_schema, ensure_ascii=False, sort_keys=True)
        if node_template is not None and node_template.output_schema
        else ""
    )
    if "{{input_json}}" in template:
        template = template.replace("{{input_json}}", serialized)
        serialized = ""
    if "{{output_schema}}" in template:
        template = template.replace("{{output_schema}}", output_schema)
        output_schema = ""
    return "\n".join(
        part
        for part in (
            f"NODE {node_name}",
            instruction,
            template,
            f"OUTPUT_SCHEMA={output_schema}" if output_schema else "",
            "Return exactly one JSON object without Markdown or commentary.",
            serialized,
        )
        if part
    )


def _validate_json_schema(value, schema, path="output"):
    if not isinstance(schema, dict) or not schema:
        return
    if "oneOf" in schema:
        errors = []
        for candidate in schema["oneOf"]:
            try:
                _validate_json_schema(value, candidate, path)
                break
            except ValueError as exc:
                errors.append(str(exc))
        else:
            raise ValueError(f"{path} does not match any allowed schema")
        return
    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected] if expected else []
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if expected_types and not any(checks[kind](value) for kind in expected_types if kind in checks):
        raise ValueError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for field in schema.get("required", []):
            if field not in value:
                raise ValueError(f"{path}.{field} is required")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise ValueError(f"{path} contains unsupported fields: {', '.join(sorted(extra))}")
        for field, item in value.items():
            if field in properties:
                _validate_json_schema(item, properties[field], f"{path}.{field}")
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate_json_schema(item, schema["items"], f"{path}[{index}]")


def _validate_prompt_node_schema(node_name, value):
    node_template = _published_prompt_node(node_name)
    if node_template is not None:
        _validate_json_schema(value, node_template.output_schema)


def _prompt_node_template_binding(node_name, version):
    if node_name == "source_passthrough":
        content = {
            "instruction": "Preserve the original seller product photo without AI modification.",
            "output_schema": {},
        }
        return {
            "node_name": node_name,
            "version": version,
            "status": "builtin",
            "content_hash": _snapshot_hash(content),
        }
    node_template = PromptNodeTemplate.objects.filter(
        node_name=node_name,
        version=version,
    ).first()
    if node_template is not None:
        if node_template.status != PromptNodeTemplate.Status.PUBLISHED:
            raise ValueError(
                f"published {node_name}/{version} prompt node template is required"
            )
        content = {
            "instruction": node_template.instruction,
            "user_message_template": node_template.user_message_template,
            "output_schema": node_template.output_schema,
        }
        return {
            "node_name": node_name,
            "version": version,
            "status": node_template.status,
            "content_hash": _snapshot_hash(content),
        }
    if settings.APIMART_FAKE_MODE and version in {"test-v1", "builtin-v1"}:
        instruction = TEST_NODE_INSTRUCTIONS.get(node_name.split(".", 1)[0])
        if not instruction:
            raise ValueError(f"test prompt node template is not defined for {node_name}")
        return {
            "node_name": node_name,
            "version": version,
            "status": "test",
            "content_hash": _snapshot_hash(
                {"instruction": instruction, "output_schema": {}}
            ),
        }
    raise ValueError(f"published {node_name}/{version} prompt node template is required")


def _platform_prompt_node(base_node, platform):
    platform = str(platform or "generic").lower()
    suffix = "shopee" if platform == "shopee" else "tiktok" if platform in {"tiktok", "tiktok_shop"} else "generic"
    return f"{base_node}.{suffix}"


def _node_family(node_name):
    return str(node_name or "").split(".", 1)[0]


def _prompt_node_temperature(node_name):
    temperature = NODE_TEMPERATURES.get(_node_family(node_name))
    if temperature is None:
        return None
    return _validate_prompt_temperature(temperature)


def _image_request_snapshot(*, size, resolution, model=None):
    return {
        "model": model or settings.APIMART_IMAGE_MODEL,
        "size": size,
        "resolution": resolution,
        "generation_params": {
            "n": 1,
            "official_fallback": False,
        },
    }


def _prompt_node(node_name, node_template=_UNSET):
    if node_template is _UNSET:
        node_template = _published_prompt_node(node_name)
    if node_template is None:
        return node_name, "builtin-v1", ""
    return node_template.node_name, node_template.version, node_template.instruction


def _language_allows_cjk(language):
    return str(language or "").lower().startswith("zh")


_THAI_RE = re.compile(r"[\u0e00-\u0e7f]")
_VIETNAMESE_RE = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]")
_KNOWN_ENGLISH_COPY = {
    "easy to love every day",
    "clear value at a glance",
    "picture it in your day",
    "made for moments you keep",
    "a little joy every day",
    "feels good to have around",
    "take me home",
    "here to brighten your day",
    "a gift with taste",
    "cute with character",
}


def _contains_cjk(value):
    return bool(_CJK_RE.search(str(value or "")))


def _chinese_product_phrase(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if _contains_cjk(text):
        return text
    lower = text.lower()
    if any(token in lower for token in ("chopstick", "utensil", "wooden spoon", "spoon")):
        if "set" in lower or "tray" in lower or "case" in lower or "chopstick" in lower:
            return "餐具套装"
        return "餐具"
    if any(token in lower for token in ("plush", "mascot", "stuffed", "doll", "toy", "character")):
        return "黄色毛绒玩偶" if "yellow" in lower else "毛绒玩偶"
    return text


def _chinese_fact_phrase(value):
    text = str(value or "").strip()
    if not text or _contains_cjk(text):
        return text
    lower = text.lower()
    if any(token in lower for token in ("chopstick", "utensil", "spoon")):
        return "可见餐具套装，包含餐具主体和收纳托盘"
    if any(token in lower for token in ("plush", "mascot", "stuffed", "doll", "character")):
        return "可见黄色毛绒玩偶，圆形黑眼睛，毛绒质感"
    return text


def _contains_target_language(value, language):
    language = str(language or "").lower()
    value = str(value or "")
    if language.startswith("th"):
        return bool(_THAI_RE.search(value))
    if language.startswith("vi"):
        return bool(_VIETNAMESE_RE.search(value))
    return True


def _visible_text_for_language(lines, language):
    lines = [str(line).strip() for line in (lines or []) if str(line).strip()]
    if _language_allows_cjk(language):
        return lines
    filtered = []
    for line in lines:
        if _contains_cjk(line):
            continue
        if not _contains_target_language(line, language):
            continue
        if str(language or "").lower() != "en" and line.lower() in _KNOWN_ENGLISH_COPY:
            continue
        filtered.append(line)
    return filtered


def _language_display_name(language):
    language = str(language or "").lower()
    if language.startswith("th"):
        return "natural Thai"
    if language.startswith("vi"):
        return "natural Vietnamese with full diacritics"
    if language.startswith("ms"):
        return "natural Bahasa Malaysia"
    if language.startswith("id"):
        return "natural Bahasa Indonesia"
    if language.startswith("pt"):
        return "Brazilian Portuguese"
    if language.startswith("zh"):
        return "Traditional Chinese when required by the selected market"
    return "natural ecommerce English"


def _localized_copy_lines(prompt_version):
    structured = (
        prompt_version.structured_output
        if isinstance(prompt_version.structured_output, dict)
        else {}
    )
    node_output = (
        structured.get("node_output") if isinstance(structured.get("node_output"), dict) else {}
    )
    localized = node_output.get("localized_copy")
    if not isinstance(localized, dict):
        localized = structured.get("localized_copy")
    if not isinstance(localized, dict):
        return []
    return _string_list(localized.get("lines"))


def _image_model_submission_prompt(generation, batch, cluster):
    _, _, effective_config = _effective_cluster_resources(batch, cluster)
    language = _market_language(effective_config["market"])
    localized_lines = _visible_text_for_language(
        _localized_copy_lines(generation.prompt_version),
        language,
    )
    prompt = generation.prompt_text.strip()
    copy_policy = (
        "Render only these quoted consumer-visible copy lines exactly once; do not translate, rewrite, add, omit, or render any other text: "
        f"{json.dumps(localized_lines, ensure_ascii=False)}"
        if localized_lines
        else "Do not add new visible words, labels, product names, captions, badges, or random characters."
    )
    return "\n".join(
        [
            "Create the final ecommerce product image from the supplied product references.",
            "Use the source prompt below as creative direction. If it contains Chinese operator notes, interpret them as requirements and express the visual instruction naturally in English before rendering.",
            "Product names and internal notes are metadata; do not render them as visible text unless they are listed in the quoted copy policy.",
            f"Consumer-visible copy language: {_language_display_name(language)}.",
            "Before rendering, internally check that any quoted consumer copy is fluent, unambiguous, and suitable for the selected market and scene.",
            copy_policy,
            "Source prompt:",
            prompt,
        ]
    )


def _sanitize_image_prompt_language(prompt, language):
    if _language_allows_cjk(language):
        return prompt
    return _CJK_RUN_RE.sub("the uploaded product", prompt)


def _limit_generation_prompt(prompt, visible_text_lines, limit=3500):
    prompt = str(prompt or "").strip()
    if len(prompt) <= limit:
        return prompt
    tail = (
        "Only render these exact quoted visible text lines once: "
        f"{json.dumps(visible_text_lines, ensure_ascii=False)}"
        if visible_text_lines
        else "Do not render any new text, title, product name, label, caption, watermark, or random characters."
    )
    head_limit = max(0, limit - len(tail) - 1)
    return f"{prompt[:head_limit].rstrip()} {tail}".strip()


def compile_slot_prompt(
    cluster,
    slot,
    *,
    batch=None,
    template=None,
    provider_model=None,
    style_dna=None,
    slot_directive=None,
    visible_text_lines=None,
    main_scene=None,
    main_action=None,
    node_name="slot_prompt",
    node_template=_UNSET,
    preserve_directive_language=False,
):
    batch = batch or cluster.batch
    effective_template, effective_rules, effective_config = _effective_cluster_resources(
        batch, cluster
    )
    template = template or effective_template
    market = effective_config["market"]
    size = effective_config["size"] or template.default_size
    resolution = effective_config["resolution"] or template.default_resolution
    consumer = target_consumer_for_cluster(cluster)
    sanitized_style_dna = _cluster_style_dna(cluster, style_dna)
    template_snapshot = _template_snapshot(template, slot)
    rule_snapshot = _rule_snapshot(effective_rules)
    applicable_rules = _applicable_rules(
        batch,
        slot,
        effective_config=effective_config,
        rule_profile=effective_rules,
    )
    rule_snapshot["resolved_rules"] = applicable_rules
    references = _reference_snapshot(cluster)
    resolved_node_name, node_version, node_instruction = _prompt_node(node_name, node_template)
    product_name = cluster.product_name or "not provided"
    product_facts = cluster.product_facts or "not provided"
    identity_lock = _identity_text(cluster.identity_lock) or "Preserve visible product identity; do not change unprovided attributes."
    global_requirements = effective_config["globalPrompt"] or "not provided"
    creative_requirements = cluster.prompt_override or "not provided"
    scene = main_scene or sanitized_style_dna.get("scene_density") or slot.purpose or "not specified"
    composition = sanitized_style_dna.get("composition") or slot.purpose or "not specified"
    lighting = sanitized_style_dna.get("lighting") or "not specified"
    material = "Use only material explicitly stated in Product facts; otherwise not specified."
    language = _market_language(market)
    visible_text_lines = _visible_text_for_language(visible_text_lines, language)
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
        prompt_lines = [
            str(slot_directive).strip(),
            f"Product identity: {product_name}. Product facts: {product_facts}. Identity lock: {identity_lock}.",
            "Use the supplied product reference images to understand product identity, usable components, included parts, variants, proportions, and material cues.",
            "Create a new ecommerce composition with a fresh camera angle, background, lighting, scene, and product arrangement.",
            "For lifestyle/use scenes, make the functional product component(s) needed for the action the hero.",
            "If the product is a functional set with multiple co-primary usable pieces, show the natural co-use subset for the action instead of isolating only the easiest piece.",
            "Storage cases, trays, boxes, holders, packaging, and extra kit pieces are secondary context only when they help the buyer understand the scene.",
            "For white-background, overview, or contents slots, show the complete set clearly. For detail slots, show the component or touchpoint that proves the current buying point.",
            "Keep visible copy, branding, and claims limited to confirmed product evidence and the locked localized text.",
        ]
    if visible_text_lines:
        prompt_lines.append(
            "Only render these exact quoted visible text lines; do not translate, rewrite, add, omit, or render any other text: "
            f"{json.dumps(visible_text_lines, ensure_ascii=False)}"
        )
    else:
        prompt_lines.append(
            "Do not render any new text, title, product name, label, caption, watermark, or random characters; preserve only text physically present on the product if unavoidable."
        )
    input_snapshot = {
        "market": market,
        "product_name": cluster.product_name,
        "product_facts": cluster.product_facts,
        "identity_lock": cluster.identity_lock,
        "global_requirements": effective_config["globalPrompt"],
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
    if not slot_directive or not preserve_directive_language:
        prompt = _sanitize_image_prompt_language(prompt, language)
    prompt = _limit_generation_prompt(prompt, visible_text_lines)
    gate = evaluate_prompt_rule_gate(
        batch,
        slot,
        prompt,
        visible_text_lines=visible_text_lines,
        references=references,
        effective_config=effective_config,
        rule_profile=effective_rules,
    )
    return {
        "node_name": resolved_node_name,
        "template_version": node_version,
        "provider_model": provider_model or settings.APIMART_PROMPT_MODEL,
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
    if not str(text or "").strip():
        raise ValueError("provider returned empty JSON")
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


def _validated_provider_json(response, normalize, repair):
    text = response.get("output_text", "")
    try:
        return normalize(_json_object(text))
    except (TypeError, ValueError, json.JSONDecodeError):
        fixed = repair(text)
        return normalize(_json_object(fixed.get("output_text", "")))


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


def _repair_observation_json(
    client,
    text,
    *,
    system_instruction,
    observation_input,
    image_path,
):
    return client.observe_images(
        "\n".join(
            [
                "NODE N1",
                f"ASSET_ID={observation_input['asset_id']}",
                system_instruction,
                _render_node_user_message(
                    "N1",
                    "Repeat the same owned-image observation using the same image evidence and input.",
                    observation_input,
                ),
                "Never copy schema placeholder words such as string. Every string value must be concrete visual evidence from the image.",
                _json_repair_prompt(
                    text,
                    (
                        'Required schema: {"asset_id":"<asset id>","image_role":"clean_product",'
                        '"asset_kind":"owned_product","contains_target_product":true,'
                        '"target_is_physical_product":true,"target_visibility":0,'
                        '"target_complete":true,"background_complexity":"low",'
                        '"observed_identity":{"category_candidates":["concrete product category"],'
                        '"dominant_colors":[],"overall_shape":"concrete visible shape",'
                        '"visible_material_cues":[],"logos_or_markings":[],"controls_ports_connectors":[],'
                        '"distinctive_parts":[],"count_observations":[]},'
                        '"observed_use_relationships":[],"non_target_objects":[],"package_or_text_clues":[],'
                        '"conflicts_with_confirmed_points":[],"reference_quality":0,'
                        '"recommended_use":"reuse","style_dna":null,"reason":"",'
                        '"candidate_product_name":"concrete product name",'
                        '"candidate_product_name_confidence":0.0}.'
                    ),
                ),
            ]
        ),
        [image_path],
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
        return [
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip() and not _is_schema_placeholder(item)
        ]
    if isinstance(value, str) and value.strip():
        return [] if _is_schema_placeholder(value) else [value.strip()]
    return []


def _is_schema_placeholder(value):
    return isinstance(value, str) and value.strip().lower() == "string"


def _clean_schema_placeholder(value):
    if isinstance(value, str):
        value = value.strip()
        return "" if _is_schema_placeholder(value) else value
    return value


def _strip_schema_placeholders(value):
    if isinstance(value, str):
        return _clean_schema_placeholder(value)
    if isinstance(value, list):
        cleaned = [_strip_schema_placeholders(item) for item in value]
        return [item for item in cleaned if item not in ("", None, [], {})]
    if isinstance(value, dict):
        cleaned = {key: _strip_schema_placeholders(item) for key, item in value.items()}
        return {
            key: item
            for key, item in cleaned.items()
            if item not in ("", None, [], {})
        }
    return value


def _required_string(payload, field):
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _normalized_confidence(value, field="confidence"):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    confidence = float(value)
    if confidence > 1:
        confidence /= 100
    if not 0 <= confidence <= 1:
        raise ValueError(f"{field} must be between 0 and 1")
    return confidence


def _normalized_percent(value, field, *, default=None):
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    percent = float(value)
    if 0 <= percent <= 1:
        percent *= 100
    if not 0 <= percent <= 100:
        raise ValueError(f"{field} must be between 0 and 100")
    return int(round(percent))


def _required_string_list(payload, field):
    value = payload.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be an array of strings")
    return [item.strip() for item in value if item.strip()]


def _required_list(payload, field):
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _has_nonempty_value(value):
    if isinstance(value, dict):
        return any(_has_nonempty_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_nonempty_value(item) for item in value)
    if isinstance(value, str):
        return bool(value.strip()) and not _is_schema_placeholder(value)
    return value is not None and value is not False


def _normalize_owned_image_role(value, *, fallback=None):
    try:
        role = _required_string({"image_role": value}, "image_role").strip().lower().replace("-", "_").replace(" ", "_")
    except ValueError:
        if fallback:
            return fallback
        raise
    aliases = {
        "clean_product": "clean_product",
        "product": "clean_product",
        "main_product": "clean_product",
        "hero": "clean_product",
        "product_detail": "detail",
        "detail": "detail",
        "closeup": "detail",
        "close_up": "detail",
        "component": "detail",
        "packaging": "packaging",
        "package": "packaging",
        "box": "packaging",
        "usage": "use_context",
        "use_context": "use_context",
        "lifestyle": "use_context",
        "model": "use_context",
        "accessory": "accessory",
        "accessories": "accessory",
    }
    normalized = aliases.get(role)
    if normalized is None:
        if fallback:
            return fallback
        raise ValueError("image_role must identify an owned product reference")
    return normalized


def _normalize_n1_observation(payload, expected_asset_id):
    if not isinstance(payload, dict):
        raise ValueError("N1 output must be an object")
    asset_id = _required_string(payload, "asset_id")
    if asset_id != str(expected_asset_id):
        raise ValueError("N1 asset_id does not match the requested cluster asset")
    asset_kind = _required_string(payload, "asset_kind").strip().lower()
    if asset_kind not in {"owned_product", "owned_reference", "product", "reference"}:
        raise ValueError("asset_kind must be owned_product")
    asset_kind = "owned_product"
    observed_identity = payload.get("observed_identity")
    if not isinstance(observed_identity, dict):
        observed_identity = {}
    candidate_name = payload.get("candidate_product_name", payload.get("product_name"))
    candidate_name = _clean_schema_placeholder(candidate_name)
    visible_identity = _has_nonempty_value(observed_identity) or bool(candidate_name)
    contains_target = payload.get("contains_target_product")
    if not isinstance(contains_target, bool):
        contains_target = visible_identity
    target_is_physical = payload.get("target_is_physical_product")
    if not isinstance(target_is_physical, bool):
        target_is_physical = contains_target
    image_role = _normalize_owned_image_role(
        payload.get("image_role"),
        fallback="clean_product" if contains_target and target_is_physical else "detail",
    )
    target_complete = payload.get("target_complete")
    if not isinstance(target_complete, bool):
        target_complete = bool(contains_target)
    background_complexity = str(payload.get("background_complexity") or "medium").strip()
    if background_complexity not in {"low", "medium", "high"}:
        background_complexity = "medium"
    recommended_use = str(payload.get("recommended_use") or "").strip()
    if recommended_use not in {
        "reuse",
        "cutout_source",
        "semantic_extract_source",
        "evidence_only",
        "reject",
    }:
        recommended_use = "reuse" if contains_target else "reject"
    observed_identity = copy.deepcopy(observed_identity)
    if contains_target:
        category_candidates = _string_list(observed_identity.get("category_candidates"))
        if not category_candidates:
            category_candidates = _string_list([candidate_name]) or ["可见商品"]
        overall_shape = _clean_schema_placeholder(observed_identity.get("overall_shape"))
        if not isinstance(overall_shape, str) or not overall_shape:
            overall_shape = candidate_name or category_candidates[0]
        observed_identity["category_candidates"] = category_candidates
        observed_identity["overall_shape"] = overall_shape.strip()
    target_visibility = _normalized_percent(payload.get("target_visibility"), "target_visibility", default=80 if contains_target else 0)
    reference_quality = _normalized_percent(payload.get("reference_quality"), "reference_quality", default=80 if contains_target else 0)
    if contains_target:
        target_visibility = max(target_visibility, 1)
        reference_quality = max(reference_quality, 1)
    for field in (
        "dominant_colors",
        "visible_material_cues",
        "logos_or_markings",
        "controls_ports_connectors",
        "distinctive_parts",
    ):
        if not isinstance(observed_identity.get(field), list):
            observed_identity[field] = []
    count_observations = observed_identity.get("count_observations")
    if not isinstance(count_observations, list):
        count_observations = []
        observed_identity["count_observations"] = count_observations
    for item in count_observations:
        if not isinstance(item, dict):
            raise ValueError("observed_identity.count_observations items must be objects")
        if not isinstance(item.get("part"), str):
            raise ValueError("observed_identity.count_observations.part must be a string")
        count = item.get("count")
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
            raise ValueError("observed_identity.count_observations.count must be a non-negative integer or null")
        _normalized_confidence(item.get("confidence"), "observed_identity.count_observations.confidence")
    for field in (
        "observed_use_relationships",
        "non_target_objects",
        "package_or_text_clues",
        "conflicts_with_confirmed_points",
    ):
        if not isinstance(payload.get(field), list):
            payload[field] = []
    candidate_confidence = payload.get(
        "candidate_product_name_confidence",
        payload.get("confidence", 0.5 if contains_target else 0),
    )
    normalized = copy.deepcopy(payload)
    normalized.update(
        {
            "asset_id": asset_id,
            "asset_kind": asset_kind,
            "image_role": image_role,
            "contains_target_product": contains_target,
            "target_is_physical_product": target_is_physical,
            "target_visibility": target_visibility,
            "target_complete": target_complete,
            "background_complexity": background_complexity,
            "observed_identity": observed_identity,
            "reference_quality": reference_quality,
            "recommended_use": recommended_use,
            "style_dna": None,
            "reason": str(payload.get("reason") or ""),
            "candidate_product_name": "" if not isinstance(candidate_name, str) else candidate_name.strip(),
            "candidate_product_name_confidence": _normalized_confidence(
                candidate_confidence,
                "candidate_product_name_confidence",
            ),
        }
    )
    return normalized


def _force_minimal_product_observation(observation, product_name):
    fallback_name = _clean_schema_placeholder(product_name) or "可见商品"
    observed_identity = observation.get("observed_identity")
    if not isinstance(observed_identity, dict):
        observed_identity = {}
    observed_identity = copy.deepcopy(observed_identity)
    category_candidates = _string_list(observed_identity.get("category_candidates")) or [fallback_name]
    overall_shape = _clean_schema_placeholder(observed_identity.get("overall_shape")) or fallback_name
    observed_identity.update(
        {
            "category_candidates": category_candidates,
            "overall_shape": overall_shape,
            "dominant_colors": _string_list(observed_identity.get("dominant_colors")),
            "visible_material_cues": _string_list(observed_identity.get("visible_material_cues")),
            "logos_or_markings": _string_list(observed_identity.get("logos_or_markings")),
            "controls_ports_connectors": _string_list(observed_identity.get("controls_ports_connectors")),
            "distinctive_parts": _string_list(observed_identity.get("distinctive_parts")),
            "count_observations": observed_identity.get("count_observations")
            if isinstance(observed_identity.get("count_observations"), list)
            else [],
        }
    )
    observation.update(
        {
            "contains_target_product": True,
            "target_is_physical_product": True,
            "target_visibility": max(int(observation.get("target_visibility") or 0), 50),
            "target_complete": bool(observation.get("target_complete", True)),
            "reference_quality": max(int(observation.get("reference_quality") or 0), 50),
            "recommended_use": "reuse",
            "observed_identity": observed_identity,
            "candidate_product_name": _clean_schema_placeholder(observation.get("candidate_product_name")) or fallback_name,
            "candidate_product_name_confidence": max(float(observation.get("candidate_product_name_confidence") or 0), 0.5),
            "product_facts": _string_list(observation.get("product_facts")) or [fallback_name],
            "identity_lock": _clean_schema_placeholder(observation.get("identity_lock")) or f"保持参考图中的{fallback_name}主体外观。",
            "reason": str(observation.get("reason") or "前置识别不确定，按用户上传图片继续作为商品参考。"),
        }
    )
    return observation


def _fallback_n1_observation(asset_id, product_name):
    return _normalize_n1_observation(
        _force_minimal_product_observation(
            {
                "asset_id": str(asset_id),
                "asset_kind": "owned_product",
                "image_role": "clean_product",
                "observed_identity": {},
            },
            product_name,
        ),
        asset_id,
    )


def _normalize_n2_identity(
    payload,
    valid_asset_ids,
    required_primary_asset_id=None,
    *,
    require_continue_when_valid=False,
):
    if not isinstance(payload, dict):
        raise ValueError("N2 output must be an object")
    valid_asset_ids = {str(asset_id) for asset_id in valid_asset_ids}
    decision = _required_string(payload, "decision")
    if decision not in {"continue", "needs_input"}:
        raise ValueError("decision must be continue or needs_input")
    if require_continue_when_valid and valid_asset_ids and decision != "continue":
        raise ValueError("N2 may only block when all product images are invalid")
    needs_input_reason = payload.get("needs_input_reason")
    if not isinstance(needs_input_reason, str):
        raise ValueError("needs_input_reason must be a string")
    if decision == "needs_input" and not needs_input_reason.strip():
        raise ValueError("needs_input_reason is required when decision is needs_input")
    standardization_mode = _required_string(payload, "standardization_mode")
    if standardization_mode != "reuse":
        raise ValueError("standardization_mode must be reuse")
    standardization_reason = payload.get("standardization_reason")
    if not isinstance(standardization_reason, str):
        raise ValueError("standardization_reason must be a string")
    conflict_state = _required_string(payload, "conflict_state")
    if conflict_state not in {"match", "unknown", "conflict"}:
        raise ValueError("conflict_state must be match, unknown, or conflict")
    product_profile = payload.get("product_profile")
    identity_lock = payload.get("identity_lock")
    if not isinstance(product_profile, dict):
        raise ValueError("product_profile must be an object")
    if not isinstance(identity_lock, dict):
        raise ValueError("identity_lock must be an object")
    product_profile = copy.deepcopy(product_profile)
    identity_lock = copy.deepcopy(identity_lock)
    product_name = _clean_schema_placeholder(_required_string(payload, "product_name"))
    if decision == "continue":
        must_not_change = identity_lock.get("must_not_change")
        must_not_change = _string_list(must_not_change)
        identity_lock["must_not_change"] = must_not_change
        if not must_not_change:
            identity_lock["must_not_change"] = ["保持上传图片中的商品主体外观"]
        if not product_name:
            product_name = "可见商品"
        category = _clean_schema_placeholder(product_profile.get("category"))
        if not isinstance(category, str) or not category:
            category = product_name
        product_profile["category"] = category
        primary_appearance = _clean_schema_placeholder(product_profile.get("primary_appearance"))
        if not isinstance(primary_appearance, str) or not primary_appearance:
            primary_appearance = category
        product_profile["primary_appearance"] = primary_appearance
    else:
        for field in ("category", "primary_appearance"):
            if field in product_profile:
                product_profile[field] = _clean_schema_placeholder(product_profile[field])
    for field in (
        "shared_structure",
        "visible_fixed_counts",
        "verified_use_relationships",
        "included_items",
        "other_variants",
        "known_conflicts",
    ):
        _required_list(product_profile, field)
        product_profile[field] = [
            item
            for item in (_strip_schema_placeholders(product_profile[field]) or [])
            if item not in ("", None, [], {})
        ]
    for field in (
        "family_invariants",
        "primary_variant_attributes",
        "exact_component_constraints",
        "verified_hidden_or_internal_structure",
        "use_relationship_constraints",
        "must_not_change",
    ):
        _required_list(identity_lock, field)
        identity_lock[field] = [
            item
            for item in (_strip_schema_placeholders(identity_lock[field]) or [])
            if item not in ("", None, [], {})
        ]
    primary_asset_id = payload.get("primary_asset_id")
    if primary_asset_id is not None:
        primary_asset_id = str(primary_asset_id)
        if primary_asset_id not in valid_asset_ids:
            raise ValueError("primary_asset_id must identify a cluster asset")
    if decision == "continue" and primary_asset_id is None:
        primary_asset_id = str(required_primary_asset_id or next(iter(valid_asset_ids), ""))
    if required_primary_asset_id is not None and decision == "continue" and primary_asset_id != str(required_primary_asset_id):
        primary_asset_id = str(required_primary_asset_id)
    supporting = payload.get("supporting_asset_ids")
    if not isinstance(supporting, list):
        raise ValueError("supporting_asset_ids must be an array")
    supporting = [str(asset_id) for asset_id in supporting]
    if len(supporting) > 3 or len(supporting) != len(set(supporting)):
        raise ValueError("supporting_asset_ids must contain at most three unique cluster assets")
    if any(asset_id not in valid_asset_ids or asset_id == primary_asset_id for asset_id in supporting):
        raise ValueError("supporting_asset_ids must identify distinct cluster assets")
    appearances = payload.get("target_appearances")
    explicit_appearances = appearances is not None
    if appearances is None:
        appearances = ([{
            "appearance_id": "appearance.primary",
            "label": product_profile.get("primary_appearance", ""),
            "variant_attributes": list(identity_lock.get("primary_variant_attributes", [])),
            "asset_ids": [primary_asset_id, *supporting] if primary_asset_id else [],
            "primary_asset_id": primary_asset_id,
        }] if decision == "continue" else [])
    if not isinstance(appearances, list):
        raise ValueError("target_appearances must be an array")
    normalized_appearances = []
    appearance_ids = set()
    assigned_assets = set()
    for item in appearances:
        if not isinstance(item, dict):
            raise ValueError("target_appearances must contain objects")
        appearance_id = _clean_schema_placeholder(item.get("appearance_id")) or f"appearance.{len(normalized_appearances) + 1}"
        if appearance_id in appearance_ids:
            raise ValueError("target_appearances appearance_id values must be unique")
        asset_ids = item.get("asset_ids")
        if not isinstance(asset_ids, list) or not asset_ids:
            raise ValueError("target_appearances asset_ids must be a non-empty array")
        asset_ids = [str(asset_id) for asset_id in asset_ids]
        appearance_primary = str(item.get("primary_asset_id") or "")
        if (
            len(asset_ids) != len(set(asset_ids))
            or any(asset_id not in valid_asset_ids for asset_id in asset_ids)
            or appearance_primary not in asset_ids
            or assigned_assets.intersection(asset_ids)
        ):
            raise ValueError("target_appearances must assign valid cluster assets exactly once")
        appearance_ids.add(appearance_id)
        assigned_assets.update(asset_ids)
        normalized_appearances.append({
            "appearance_id": appearance_id,
            "label": str(item.get("label") or "").strip(),
            "variant_attributes": _required_string_list(item, "variant_attributes"),
            "asset_ids": asset_ids,
            "primary_asset_id": appearance_primary,
        })
    if decision == "continue" and (not normalized_appearances or primary_asset_id not in assigned_assets):
        raise ValueError("target_appearances must include the primary product appearance")
    if decision == "continue" and explicit_appearances and assigned_assets != valid_asset_ids:
        raise ValueError("target_appearances must assign every valid product image")
    normalized = copy.deepcopy(payload)
    normalized.update(
        {
            "decision": decision,
            "needs_input_reason": needs_input_reason.strip(),
            "product_name": product_name,
            "confidence": _normalized_confidence(payload.get("confidence")),
            "conflict_state": conflict_state,
            "primary_asset_id": primary_asset_id,
            "supporting_asset_ids": supporting,
            "target_appearances": normalized_appearances,
            "identity_lock": identity_lock,
            "product_profile": product_profile,
            "standardization_mode": standardization_mode,
            "standardization_reason": standardization_reason.strip(),
        }
    )
    return normalized


def _n2_observation_fallbacks(payload, identity_input):
    if not isinstance(payload, dict):
        return payload
    normalized = copy.deepcopy(payload)
    observations = [
        item
        for item in identity_input.get("observations", [])
        if item.get("contains_target_product") and item.get("target_is_physical_product")
    ]
    first_observation = observations[0] if observations else {}
    observed_identity = first_observation.get("observed_identity") or {}
    fallback_name = _clean_schema_placeholder(identity_input.get("product_name")) or _clean_schema_placeholder(
        first_observation.get("candidate_product_name")
    )
    fallback_category = next(iter(_string_list(observed_identity.get("category_candidates"))), "")
    fallback_shape = _clean_schema_placeholder(observed_identity.get("overall_shape")) or "; ".join(
        _string_list(first_observation.get("product_facts"))
    )
    fallback_name = fallback_name or fallback_category or fallback_shape or "可见商品"
    fallback_category = fallback_category or fallback_name
    fallback_shape = fallback_shape or fallback_name
    valid_asset_ids = [str(item["asset_id"]) for item in observations if item.get("asset_id")]
    if valid_asset_ids and normalized.get("decision") != "continue":
        normalized["decision"] = "continue"
    if valid_asset_ids:
        normalized["needs_input_reason"] = ""
        if str(normalized.get("primary_asset_id") or "") not in valid_asset_ids:
            normalized["primary_asset_id"] = valid_asset_ids[0]
        primary = str(normalized["primary_asset_id"])
        supporting = normalized.get("supporting_asset_ids")
        if not isinstance(supporting, list):
            supporting = []
        normalized["supporting_asset_ids"] = [
            str(asset_id)
            for asset_id in supporting
            if str(asset_id) in valid_asset_ids and str(asset_id) != primary
        ][:3] or [asset_id for asset_id in valid_asset_ids if asset_id != primary][:3]
        normalized["confidence"] = normalized.get("confidence") or first_observation.get("candidate_product_name_confidence") or 0.5
        normalized["conflict_state"] = normalized.get("conflict_state") if normalized.get("conflict_state") in {"match", "unknown", "conflict"} else "unknown"
        normalized["standardization_mode"] = "reuse"
        normalized["standardization_reason"] = str(normalized.get("standardization_reason") or "")
    if fallback_name and not _clean_schema_placeholder(normalized.get("product_name")):
        normalized["product_name"] = fallback_name
    profile = normalized.get("product_profile")
    if not isinstance(profile, dict):
        profile = {}
    profile = copy.deepcopy(profile)
    if fallback_category and not _clean_schema_placeholder(profile.get("category")):
        profile["category"] = fallback_category
    if fallback_shape and not _clean_schema_placeholder(profile.get("primary_appearance")):
        profile["primary_appearance"] = fallback_shape
    for field in (
        "shared_structure",
        "visible_fixed_counts",
        "verified_use_relationships",
        "included_items",
        "other_variants",
        "known_conflicts",
    ):
        if not isinstance(profile.get(field), list):
            profile[field] = []
    normalized["product_profile"] = profile
    lock = normalized.get("identity_lock")
    if not isinstance(lock, dict):
        lock = {}
    lock = copy.deepcopy(lock)
    for field in (
        "family_invariants",
        "primary_variant_attributes",
        "exact_component_constraints",
        "verified_hidden_or_internal_structure",
        "use_relationship_constraints",
        "must_not_change",
    ):
        if not isinstance(lock.get(field), list):
            lock[field] = []
    must_not_change = _string_list(lock.get("must_not_change"))
    if not must_not_change:
        lock["must_not_change"] = _string_list([fallback_shape, fallback_category, fallback_name])
    normalized["identity_lock"] = lock

    def fallback_appearances():
        return [
            {
                "appearance_id": f"appearance.{index + 1}",
                "label": _clean_schema_placeholder(item.get("candidate_product_name")) or fallback_category or fallback_name or f"Appearance {index + 1}",
                "variant_attributes": _string_list((item.get("observed_identity") or {}).get("dominant_colors")),
                "asset_ids": [str(item["asset_id"])],
                "primary_asset_id": str(item["asset_id"]),
            }
            for index, item in enumerate(observations)
            if item.get("asset_id")
        ]

    appearances = normalized.get("target_appearances")
    if isinstance(appearances, list):
        cleaned = []
        assigned = set()
        for item in appearances:
            if not isinstance(item, dict):
                continue
            appearance = copy.deepcopy(item)
            asset_ids = [
                str(asset_id)
                for asset_id in appearance.get("asset_ids", [])
                if str(asset_id) in valid_asset_ids and str(asset_id) not in assigned
            ]
            if not asset_ids:
                continue
            primary_asset_id = str(appearance.get("primary_asset_id") or asset_ids[0])
            if primary_asset_id not in asset_ids:
                primary_asset_id = asset_ids[0]
            appearance["appearance_id"] = _clean_schema_placeholder(appearance.get("appearance_id")) or f"appearance.{len(cleaned) + 1}"
            appearance["asset_ids"] = asset_ids
            appearance["primary_asset_id"] = primary_asset_id
            if fallback_category and not _clean_schema_placeholder(appearance.get("label")):
                appearance["label"] = fallback_category
            appearance["variant_attributes"] = _string_list(appearance.get("variant_attributes"))
            cleaned.append(appearance)
            assigned.update(asset_ids)
        for missing_asset_id in [asset_id for asset_id in valid_asset_ids if asset_id not in assigned]:
            index = len(cleaned) + 1
            cleaned.append({
                "appearance_id": f"appearance.{index}",
                "label": fallback_category or fallback_name or f"Appearance {index}",
                "variant_attributes": [],
                "asset_ids": [missing_asset_id],
                "primary_asset_id": missing_asset_id,
            })
        normalized["target_appearances"] = cleaned
    elif valid_asset_ids:
        normalized["target_appearances"] = fallback_appearances()
    return normalized


def _fallback_n3_ledger(ledger_input, known_evidence_refs=None):
    refs = sorted(known_evidence_refs or [])
    asset_ref = next((value for value in refs if str(value).startswith("asset:")), None)
    evidence_refs = [asset_ref or "product_name"]
    statement = _chinese_fact_phrase(
        _clean_schema_placeholder(ledger_input.get("product_name"))
        or _clean_schema_placeholder(
            (ledger_input.get("product_profile") or {}).get("primary_appearance")
        )
        or "可见商品"
    )
    return _normalize_n3_ledger(
        {
            "ledger_version": "2.0.0",
            "facts": [
                {
                    "fact_id": "fact.visible_product.001",
                    "statement": statement,
                    "fact_class": "observed",
                    "confidence": 0.6,
                    "evidence_refs": evidence_refs,
                    "risk_level": "low",
                    "allowed_uses": ["identity", "visual_prompt", "scene_planning", "consumer_copy_pending_review"],
                    "review_note": "已按上传图片继续生成，结果进入人工审核。",
                }
            ],
            "blocked_claim_topics": [],
            "unresolved_questions": [],
            "review_summary": {},
        },
        known_evidence_refs,
    )


def _normalize_n3_ledger(payload, known_evidence_refs=None):
    if not isinstance(payload, dict):
        raise ValueError("N3 output must be an object")
    ledger_version = _required_string(payload, "ledger_version")
    facts = payload.get("facts")
    if not isinstance(facts, list):
        raise ValueError("facts must be an array")
    allowed_classes = {"confirmed", "observed", "inferred"}
    allowed_uses = {
        "identity",
        "visual_prompt",
        "scene_planning",
        "consumer_copy",
        "consumer_copy_pending_review",
        "blocked",
    }
    normalized_facts = []
    seen_ids = set()
    for item in facts:
        if not isinstance(item, dict):
            raise ValueError("each fact must be an object")
        fact_id = _required_string(item, "fact_id")
        if fact_id in seen_ids:
            raise ValueError("fact_id values must be unique")
        seen_ids.add(fact_id)
        fact_class = _required_string(item, "fact_class")
        if fact_class not in allowed_classes:
            raise ValueError("fact_class is invalid")
        risk_level = _required_string(item, "risk_level")
        evidence_refs = _required_string_list(item, "evidence_refs")
        if known_evidence_refs is not None:
            known_evidence_refs = set(known_evidence_refs)
            evidence_refs = [value for value in evidence_refs if value in known_evidence_refs]
            if not evidence_refs:
                asset_refs = sorted(value for value in known_evidence_refs if value.startswith("asset:"))
                fallback_ref = (asset_refs or sorted(known_evidence_refs) or ["product_name"])[0]
                evidence_refs = [fallback_ref]
        uses = _required_string_list(item, "allowed_uses")
        if any(value not in allowed_uses for value in uses):
            raise ValueError("allowed_uses contains an invalid value")
        if fact_class == "inferred" and risk_level == "high":
            uses = ["blocked"]
        normalized_item = copy.deepcopy(item)
        normalized_item.update(
            {
                "fact_id": fact_id,
                "statement": _required_string(item, "statement"),
                "fact_class": fact_class,
                "confidence": _normalized_confidence(item.get("confidence")),
                "evidence_refs": evidence_refs,
                "risk_level": risk_level,
                "allowed_uses": uses,
                "review_note": str(item.get("review_note") or ""),
            }
        )
        normalized_facts.append(normalized_item)
    blocked_claim_topics = _required_string_list(payload, "blocked_claim_topics")
    unresolved_questions = _required_string_list(payload, "unresolved_questions")
    review_summary = payload.get("review_summary")
    if not isinstance(review_summary, dict):
        raise ValueError("review_summary must be an object")
    normalized = copy.deepcopy(payload)
    normalized.update(
        {
            "ledger_version": ledger_version,
            "facts": normalized_facts,
            "blocked_claim_topics": blocked_claim_topics,
            "unresolved_questions": unresolved_questions,
            "review_summary": {
                "confirmed_count": sum(item["fact_class"] == "confirmed" for item in normalized_facts),
                "observed_count": sum(item["fact_class"] == "observed" for item in normalized_facts),
                "inferred_count": sum(item["fact_class"] == "inferred" for item in normalized_facts),
                "high_risk_count": sum(item["risk_level"] == "high" for item in normalized_facts),
            },
        }
    )
    return normalized


def _validate_known_refs(refs, known, label):
    if any(ref not in known for ref in refs):
        kind = {"fact_refs": "fact", "inference_refs": "inference", "rule_refs": "rule"}[label]
        raise ValueError(f"{label} contains an unknown {kind} reference")


def _normalize_generation_parameters(payload):
    parameters = payload.get("generation_parameters")
    if not isinstance(parameters, dict):
        raise ValueError("generation_parameters must be an object")
    if parameters.get("model") != "gpt-image-2" or parameters.get("n") != 1:
        raise ValueError("generation_parameters must use gpt-image-2 with n=1")
    if not isinstance(parameters.get("size"), str) or not isinstance(parameters.get("resolution"), str):
        raise ValueError("generation_parameters size and resolution are required")
    return copy.deepcopy(parameters)


def _normalize_compiled_prompt(payload, slot_order, identity, ledger, rule_refs, *, hero):
    if not isinstance(payload, dict):
        raise ValueError("compiled prompt output must be an object")
    raw_slot = payload.get("slot_id", payload.get("slot_order"))
    try:
        normalized_slot = int(raw_slot)
    except (TypeError, ValueError):
        raise ValueError("slot_id must identify the requested slot") from None
    if normalized_slot != int(slot_order):
        raise ValueError("slot_id must identify the requested slot")
    prompt = _required_string(payload, "prompt")
    if len(prompt) > 3500:
        raise ValueError("prompt must not exceed 3500 characters")
    main_scene = _required_string(payload, "main_scene")
    main_action = _required_string(payload, "main_action")
    display_prompt = str(payload.get("display_prompt") or "").strip()
    if not display_prompt:
        if hero:
            display_prompt = "生成一张 1:1 标准白底商品图。商品完整居中呈现，结构、颜色、材质和数量清楚，使用均匀棚拍柔光和干净白色背景。"
        else:
            display_prompt = "生成一张 1:1 Shopee 商品营销图。用一个具体可见的购买瞬间呈现商品作用，写清镜头、光线、材质、色彩层次和目标语言文字版式。"
    visible_text_lines = _required_string_list(payload, "visible_text_lines")
    if len(visible_text_lines) > 3 or (hero and visible_text_lines):
        raise ValueError("visible_text_lines violate the slot limit")
    if hero and main_action != "none":
        raise ValueError("standard white-background prompt main_action must be none")
    reference_plan = payload.get("reference_plan")
    if not isinstance(reference_plan, dict):
        raise ValueError("reference_plan must be an object")
    primary_asset_id = reference_plan.get("primary_asset_id")
    if str(primary_asset_id) != str(identity.get("primary_asset_id")):
        raise ValueError("reference_plan primary_asset_id must match N2")
    supporting = reference_plan.get("supporting_asset_ids")
    if not isinstance(supporting, list):
        raise ValueError("reference_plan supporting_asset_ids must be an array")
    supporting = [str(item) for item in supporting]
    appearance_asset_ids = {
        str(asset_id)
        for appearance in identity.get("target_appearances", [])
        if isinstance(appearance, dict)
        for asset_id in appearance.get("asset_ids", [])
    }
    approved_supporting = {
        str(item) for item in identity.get("supporting_asset_ids", [])
    } | appearance_asset_ids
    if hero:
        if supporting != [str(item) for item in identity.get("supporting_asset_ids", [])]:
            raise ValueError("reference_plan supporting_asset_ids must match N2")
    elif len(supporting) != len(set(supporting)) or any(
        item not in approved_supporting for item in supporting
    ):
        raise ValueError("marketing reference_plan must identify N2-approved product assets")
    fact_ids = {item["fact_id"] for item in ledger["facts"]}
    inferred_ids = {
        item["fact_id"] for item in ledger["facts"] if item["fact_class"] == "inferred"
    }
    fact_trace = _required_string_list(payload, "fact_trace")
    inference_trace = _required_string_list(payload, "inference_trace")
    resolved_rule_refs = _required_string_list(payload, "rule_refs")
    _validate_known_refs(fact_trace, fact_ids, "fact_refs")
    _validate_known_refs(inference_trace, inferred_ids, "inference_refs")
    _validate_known_refs(resolved_rule_refs, set(rule_refs), "rule_refs")
    if payload.get("review_required") is not True:
        raise ValueError("review_required must be true")
    normalized = copy.deepcopy(payload)
    normalized.update(
        {
            "slot_id": str(normalized_slot),
            "main_scene": main_scene,
            "main_action": main_action,
            "display_prompt": display_prompt,
            "visible_text_lines": visible_text_lines,
            "prompt": prompt,
            "character_count": len(prompt),
            "reference_plan": copy.deepcopy(reference_plan),
            "fact_trace": fact_trace,
            "inference_trace": inference_trace,
            "rule_refs": resolved_rule_refs,
            "generation_parameters": _normalize_generation_parameters(payload),
            "review_required": True,
            "prompt_source": str(payload.get("prompt_source") or "deepseek").strip() or "deepseek",
        }
    )
    return normalized


def _normalize_n4_prompt(payload, slot_order, identity, ledger, rule_refs):
    return _normalize_compiled_prompt(
        payload,
        slot_order,
        identity,
        ledger,
        rule_refs,
        hero=True,
    )


def _validate_marketing_diversity(plans):
    if not plans:
        return
    for field, max_share in MARKETING_DIVERSITY_MAX_SHARE.items():
        values = [str(plan.get(field) or "").strip().casefold() for plan in plans]
        if any(not value for value in values):
            raise ValueError(f"marketing plan diversity requires non-empty {field}")
        allowed_repeats = max(1, int(len(values) * max_share))
        most_common = Counter(values).most_common(1)[0][1]
        if most_common > allowed_repeats:
            raise ValueError(
                f"marketing plan diversity repeats {field} {most_common} times; "
                f"at most {allowed_repeats} are allowed"
            )


def _strategy_text(value, field, *, required=True):
    if not isinstance(value, str):
        raise ValueError(f"creative_strategy {field} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"creative_strategy {field} is required")
    return value


def _fallback_creative_strategy(plan, mode):
    fact_refs = plan.get("fact_refs", [])
    return {
        "mode": mode,
        "source_fact_refs": list(fact_refs),
        "user_job": plan["decision_task"],
        "consumer_tension": plan.get("copy_intent") or plan["decision_task"],
        "feature": "; ".join(plan.get("must_show", [])) or plan["subject_relationship"],
        "advantage": plan["decision_task"],
        "consumer_benefit": plan.get("copy_intent") or plan["decision_task"],
        "mental_simulation": plan["main_scene"],
        "emotional_shift": plan.get("copy_intent") or plan["decision_task"],
        "product_voice": "",
        "identity_signal": "",
        "selection_reason": "Fallback strategy built from the normalized slot plan.",
    }


def _normalize_creative_strategy(plan, fact_ids, index):
    strategy = plan.get("creative_strategy")
    if strategy is None:
        strategy = _fallback_creative_strategy(
            plan,
            MARKETING_STRATEGY_MODES[index % len(MARKETING_STRATEGY_MODES)],
        )
    if not isinstance(strategy, dict):
        raise ValueError("creative_strategy must be an object")
    normalized = copy.deepcopy(strategy)
    mode = _strategy_text(normalized.get("mode"), "mode")
    if mode not in _MARKETING_STRATEGY_MODE_SET:
        raise ValueError("creative_strategy mode is not supported")
    refs = _required_string_list(normalized, "source_fact_refs")
    unknown_refs = [ref for ref in refs if ref not in fact_ids]
    if unknown_refs:
        raise ValueError("creative_strategy source_fact_refs contains an unknown fact reference")
    required_strings = (
        "user_job",
        "consumer_tension",
        "feature",
        "advantage",
        "consumer_benefit",
        "mental_simulation",
        "emotional_shift",
        "selection_reason",
    )
    for field in required_strings:
        normalized[field] = _strategy_text(normalized.get(field), field)
    for field in ("product_voice", "identity_signal"):
        normalized[field] = _strategy_text(normalized.get(field, ""), field, required=False)
    normalized["mode"] = mode
    normalized["source_fact_refs"] = refs
    return normalized


_TEXT_LAYOUT_THEMES = (
    "premium_whisper",
    "clean_benefit_stack",
    "soft_family_label",
    "tech_spec_edge",
    "deal_pop_corner",
    "lifestyle_caption_float",
    "feature_callout_pin",
    "youth_sticker_light",
)

def _fallback_text_layout_theme(index):
    return _TEXT_LAYOUT_THEMES[index % len(_TEXT_LAYOUT_THEMES)]


def _fallback_product_action_menu(product_label):
    text = str(product_label or "")
    if any(token in text for token in ("餐具", "筷", "勺", "刀叉", "碗", "盘", "杯")):
        return "从收纳托盘取出、摆到餐垫旁、夹起食物、放回盒内或随餐携带"
    if any(token in text for token in ("玩偶", "毛绒", "娃娃", "公仔")):
        return "抱起、靠在包口、放在床边、递给孩子或摆进礼物角"
    if any(token in text for token in ("袜", "鞋", "衣", "裤", "裙", "手套", "帽")):
        return "穿戴、拉平、贴合身体、搭配鞋服或整理边缘"
    if any(token in text for token in ("收纳", "盒", "架", "袋", "包", "篮")):
        return "放入、取出、整理、挂起或带走"
    return "拿起、放下、摆好、打开、收纳、携带或靠近使用位置"


def _prompt_os_allow_fallback():
    return bool(getattr(settings, "PROMPT_OS_ALLOW_FALLBACK", False))


def _require_prompt_os_fallback():
    if not _prompt_os_allow_fallback():
        raise RuntimeError("PROMPT_OS_ALLOW_FALLBACK=true is required to use deterministic Prompt OS fallback")


def _normalize_subject_plan(plan):
    value = plan.get("subject_plan")
    if not isinstance(value, dict):
        value = {}
    value = copy.deepcopy(value)
    defaults = {
        "product_scope": plan.get("subject_relationship") or "当前槽位需要的商品主体或合理子集",
        "visible_unit_count": "按当前槽位画面目标决定商品数量：总览/包含物显示完整，细节、情绪、使用或比例图可只展示一个代表性商品或必要子集",
        "person_presence": "按真实使用关系决定是否出现人物、手部或宠物",
        "usage_relationship": plan.get("subject_relationship") or "商品按真实用途、接触点和支撑关系参与画面",
        "reason": plan.get("decision_task") or "商品数量和人物关系服务当前购买决策",
    }
    for field, fallback in defaults.items():
        value[field] = str(value.get(field) or fallback).strip()
    return value


def _normalize_composition_plan(plan):
    value = plan.get("composition_plan")
    if not isinstance(value, dict):
        value = {}
    value = copy.deepcopy(value)
    defaults = {
        "canvas": "1:1 方形",
        "camera": plan.get("camera") or "清楚的商业摄影机位",
        "shot_scale": plan.get("composition") or "商品占主要视觉重量",
        "subject_share": "商品约占画面 50%–70%，按槽位信息层级调整",
        "text_area": "文字约占画面 8%–18%，落在自然浅色区域、空气感留白或柔焦空间",
        "diversity_signature": " / ".join(
            str(plan.get(field) or "").strip()
            for field in ("decision_task", "main_scene", "main_action", "camera")
            if str(plan.get(field) or "").strip()
        )
        or "当前槽位独立构图签名",
    }
    for field, fallback in defaults.items():
        value[field] = str(value.get(field) or fallback).strip()
    return value


def _normalize_style_plan(plan):
    value = plan.get("style_plan")
    if not isinstance(value, dict):
        value = {}
    value = copy.deepcopy(value)
    defaults = {
        "lighting": plan.get("aesthetic_point_of_view") or "真实商业摄影柔光",
        "material_focus": plan.get("subject_relationship") or "商品可见材质与关键结构清楚",
        "palette": plan.get("aesthetic_point_of_view") or "色彩服务当前购买情绪",
    }
    for field, fallback in defaults.items():
        value[field] = str(value.get(field) or fallback).strip()
    value["props"] = _string_list(value.get("props"))
    return value


def _slot_text(slot):
    return f"{getattr(slot, 'name', '')} {getattr(slot, 'purpose', '')}".casefold()


def _slot_name(slot):
    return str(getattr(slot, "name", "") or "").casefold()


def _slot_purpose(slot):
    return str(getattr(slot, "purpose", "") or "").casefold()


def _slot_requires_model_or_scale(slot):
    text = _slot_text(slot)
    return any(
        token in text
        for token in ("model", "scale", "wearer", "模特", "比例", "尺度", "真人")
    )


def _slot_requires_real_use(slot):
    purpose = _slot_purpose(slot)
    name = _slot_name(slot)
    if any(token in purpose for token in ("usage", "realistic product use", "function", "使用", "功能", "操作", "穿戴", "佩戴", "手持")):
        return True
    return any(token in name for token in ("usage", "real use", "function", "使用场景", "功能说明", "使用图", "操作图", "穿戴图", "佩戴图", "手持图"))


def _apply_slot_visual_contract(plan, slot):
    subject = plan.get("subject_plan") if isinstance(plan.get("subject_plan"), dict) else {}
    subject = copy.deepcopy(subject)
    if _slot_requires_model_or_scale(slot):
        subject.update(
            {
                "person_presence": "安排真人手部、身体局部、模特、用户、宠物或真实空间尺度线索；人物/手/宠物和商品形成握持、佩戴、抱起、靠近或比例对照",
                "usage_relationship": "用人物、手部、宠物或空间参照清楚展示商品真实大小、接触位置或使用尺度",
                "reason": "当前槽位是模特/比例图，核心任务是让买家理解商品与真实用户或空间的尺度关系",
            }
        )
        plan["main_action"] = "真人手部拿起、抱起、佩戴或靠近商品，形成清楚的比例参照"
        plan["specific_moment"] = "真人手部、身体局部、用户或宠物正与商品同框互动，买家一眼看懂商品大小和适用对象"
        plan["must_show"] = list(dict.fromkeys([*plan.get("must_show", []), "person/hand/pet/scale context"]))
    elif _slot_requires_real_use(slot):
        subject.update(
            {
                "person_presence": "优先安排真人手部、身体局部、用户或宠物，实际握持、佩戴、携带、摆放或操作商品",
                "usage_relationship": "商品处在真实或品类显而易见的使用位置，人物/手部/宠物与商品有清楚接触点和单一动作",
                "reason": "当前槽位要解释商品怎么被使用，人物或手部关系能比静态摆拍更快说明用途",
            }
        )
        plan["main_action"] = "用户手部正在拿起、摆放或使用商品，展示一个清楚的真实使用关系"
        plan["specific_moment"] = "用户或手部正把商品用于一个明确场景，画面说明商品此刻解决的具体购买疑问"
        plan["must_show"] = list(dict.fromkeys([*plan.get("must_show", []), "real user/hand use relationship"]))
    plan["subject_plan"] = subject
    return plan


def _normalize_copywriting_chain(plan, fact_ids):
    value = plan.get("copywriting_chain")
    if not isinstance(value, dict):
        value = {}
    value = copy.deepcopy(value)
    strategy = plan.get("creative_strategy") if isinstance(plan.get("creative_strategy"), dict) else {}
    defaults = {
        "raw_fact": "; ".join(plan.get("fact_refs", [])) or plan.get("decision_task") or "商品事实",
        "feature": strategy.get("feature") or plan.get("subject_relationship") or "商品可见特征",
        "advantage": strategy.get("advantage") or plan.get("decision_task") or "购买理解更清楚",
        "user_result": strategy.get("consumer_benefit") or plan.get("copy_intent") or plan.get("conversion_goal") or "买家更快理解商品价值",
        "use_scene": strategy.get("mental_simulation") or plan.get("main_scene") or "当前画面场景",
        "emotion_hook": strategy.get("emotional_shift") or plan.get("copy_intent") or "从犹豫到更有把握",
        "copy_angle": plan.get("copy_intent") or strategy.get("selection_reason") or plan.get("decision_task") or "贴合当前主题的短标题角度",
        "risk_note": "只使用已确认或允许的低风险商品事实",
    }
    for field, fallback in defaults.items():
        value[field] = str(value.get(field) or fallback).strip()
    refs = _required_string_list({"source_fact_refs": value.get("source_fact_refs") or plan.get("fact_refs", [])}, "source_fact_refs")
    unknown_refs = [ref for ref in refs if ref not in fact_ids]
    if unknown_refs:
        raise ValueError("copywriting_chain source_fact_refs contains an unknown fact reference")
    value["source_fact_refs"] = refs
    return value


def _validate_marketing_strategy_distribution(plans):
    if not plans:
        return
    modes = [plan["creative_strategy"]["mode"] for plan in plans]
    if len(set(modes)) < min(4, len(modes)):
        raise ValueError("creative strategy distribution requires enough distinct modes")
    counts = Counter(modes)
    if len(modes) >= 8 and counts.get("fab_value", 0) < 1:
        raise ValueError("creative strategy distribution requires at least one fab_value")
    if counts.get("personification", 0) > 1:
        raise ValueError("creative strategy distribution allows at most one personification")
    repeated = [mode for mode, count in counts.items() if count > 3]
    if repeated:
        raise ValueError("creative strategy distribution repeats one mode too often")


def _normalize_n5_plans(payload, marketing_slots, fact_ids, inference_ids, target_appearance_ids=None):
    plans = _normalized_marketing_plans(payload, marketing_slots)
    expected = {slot.order for slot in marketing_slots}
    if set(plans) != expected:
        raise ValueError("marketing plan must contain exactly one plan for every input slot")
    required_strings = (
        "role",
        "decision_task",
        "main_scene",
        "main_action",
        "subject_relationship",
        "composition",
        "text_mode",
        "scene_family",
        "environment",
        "camera",
        "visual_theme",
        "specific_moment",
        "aesthetic_point_of_view",
        "typography_direction",
        "text_layout_theme",
    )
    required_lists = (
        "fact_refs",
        "inference_refs",
        "localization_notes",
        "must_show",
        "must_avoid",
    )
    normalized = []
    target_appearance_ids = set(target_appearance_ids or [])
    covered_appearance_ids = set()
    for index, slot in enumerate(marketing_slots):
        plan = copy.deepcopy(plans[slot.order])
        plan.setdefault("visual_theme", plan.get("role") or plan.get("decision_task") or f"第 {slot.order} 张营销主题")
        plan.setdefault("specific_moment", plan.get("main_action") or plan.get("main_scene") or f"第 {slot.order} 张商品画面瞬间")
        plan.setdefault("aesthetic_point_of_view", "真实商业摄影，光线、色彩、材质和空间层次服务当前购买理由")
        plan.setdefault("typography_direction", "左上角两行目标语言标题，第一行 32px 粗体无衬线，第二行 21px 常规无衬线，深色文字，宽度约画面 36%")
        plan.setdefault("text_layout_theme", _fallback_text_layout_theme(index))
        for field in required_strings:
            plan[field] = _required_string(plan, field)
        if not isinstance(plan.get("copy_intent"), str):
            raise ValueError("copy_intent must be a string")
        for field in required_lists:
            plan[field] = _required_string_list(plan, field)
        plan["prompt_source"] = str(plan.get("prompt_source") or "deepseek").strip() or "deepseek"
        plan = _apply_slot_visual_contract(plan, slot)
        _validate_known_refs(plan["fact_refs"], set(fact_ids), "fact_refs")
        _validate_known_refs(plan["inference_refs"], set(inference_ids), "inference_refs")
        appearance_ids = plan.get("appearance_ids")
        if appearance_ids is None:
            appearance_ids = sorted(target_appearance_ids)
        appearance_ids = _required_string_list({"appearance_ids": appearance_ids}, "appearance_ids")
        if target_appearance_ids and (not appearance_ids or any(item not in target_appearance_ids for item in appearance_ids)):
            raise ValueError("appearance_ids must identify target appearances")
        plan["appearance_ids"] = appearance_ids
        plan["creative_strategy"] = _normalize_creative_strategy(
            plan,
            set(fact_ids),
            index,
        )
        plan["subject_plan"] = _normalize_subject_plan(plan)
        plan["composition_plan"] = _normalize_composition_plan(plan)
        plan["style_plan"] = _normalize_style_plan(plan)
        plan["copywriting_chain"] = _normalize_copywriting_chain(plan, set(fact_ids))
        covered_appearance_ids.update(appearance_ids)
        normalized.append(plan)
    if target_appearance_ids - covered_appearance_ids:
        raise ValueError("marketing plans must cover every target appearance")
    _validate_marketing_diversity(normalized)
    _validate_marketing_strategy_distribution(normalized)
    result = copy.deepcopy(payload)
    result["plans"] = normalized
    result["prompt_source"] = str(payload.get("prompt_source") or "deepseek").strip() or "deepseek"
    return result


def _normalize_n6_prompt(payload, slot_order, identity, ledger, rule_refs):
    normalized = _normalize_compiled_prompt(
        payload,
        slot_order,
        identity,
        ledger,
        rule_refs,
        hero=False,
    )
    if not str(payload.get("visual_theme") or "").strip():
        payload["visual_theme"] = payload.get("main_scene") or "当前商品营销主题"
    if not str(payload.get("typography_plan") or "").strip():
        payload["typography_plan"] = "左上角两行目标语言标题，第一行 32px 粗体无衬线，第二行 21px 常规无衬线，深色文字，宽度约画面 36%"
    if not str(payload.get("text_layout_theme") or "").strip():
        payload["text_layout_theme"] = _fallback_text_layout_theme(max(0, int(slot_order) - 2))
    normalized["visual_theme"] = _required_string(payload, "visual_theme")
    normalized["text_layout_theme"] = _required_string(payload, "text_layout_theme")
    normalized["typography_plan"] = _required_string(payload, "typography_plan")
    localized_copy = payload.get("localized_copy")
    if not isinstance(localized_copy, dict):
        raise ValueError("localized_copy must be an object")
    localized_copy = copy.deepcopy(localized_copy)
    localized_copy["language"] = _required_string(localized_copy, "language")
    raw_localized_lines = _required_string_list(localized_copy, "lines")
    raw_visible_lines = list(normalized["visible_text_lines"])
    localized_copy["lines"] = raw_localized_lines
    localized_copy["source_fact_refs"] = _required_string_list(
        localized_copy,
        "source_fact_refs",
    )
    localized_copy["source_inference_refs"] = _required_string_list(
        localized_copy,
        "source_inference_refs",
    )
    fact_ids = {item["fact_id"] for item in ledger["facts"]}
    inference_ids = {
        item["fact_id"] for item in ledger["facts"] if item["fact_class"] == "inferred"
    }
    _validate_known_refs(localized_copy["source_fact_refs"], fact_ids, "fact_refs")
    _validate_known_refs(
        localized_copy["source_inference_refs"],
        inference_ids,
        "inference_refs",
    )
    localized_copy["lines"] = _visible_text_for_language(
        localized_copy["lines"],
        localized_copy["language"],
    )
    normalized["visible_text_lines"] = _visible_text_for_language(
        normalized["visible_text_lines"],
        localized_copy["language"],
    )
    if (raw_localized_lines or raw_visible_lines) and not normalized["visible_text_lines"]:
        plan = {
            "creative_strategy": payload.get("creative_strategy") or {},
            "text_mode": payload.get("text_mode"),
        }
        fallback_lines = _fallback_marketing_copy_lines(
            plan,
            "",
            localized_copy["language"],
        )
        localized_copy["lines"] = fallback_lines
        normalized["visible_text_lines"] = fallback_lines
        for line in set(raw_localized_lines + raw_visible_lines):
            normalized["prompt"] = normalized["prompt"].replace(line, "")
    if localized_copy["lines"] != normalized["visible_text_lines"]:
        raise ValueError("localized_copy lines must match visible_text_lines")
    normalized["prompt"] = _append_visible_copy_lock(
        normalized["prompt"],
        normalized["visible_text_lines"],
    )
    normalized["character_count"] = len(normalized["prompt"])
    normalized["localized_copy"] = localized_copy
    return normalized


def _normalize_n7_semantic(payload):
    if not isinstance(payload, dict):
        raise ValueError("N7 output must be an object")
    decision = _required_string(payload, "decision")
    if decision not in {"pass", "block"}:
        raise ValueError("N7 decision must be pass or block")
    normalized = copy.deepcopy(payload)
    for field in ("hard_blocks", "semantic_risks", "warnings", "resolved_rule_refs"):
        normalized[field] = _required_string_list(payload, field)
    if not isinstance(payload.get("prompt_checks"), dict):
        raise ValueError("N7 prompt_checks must be an object")
    if not isinstance(payload.get("copy_checks"), dict):
        raise ValueError("N7 copy_checks must be an object")
    if payload.get("review_required") is not True:
        raise ValueError("N7 review_required must be true")
    normalized["decision"] = decision
    normalized["review_required"] = True
    return normalized


def _fallback_n4_prompt(hero_input, identity, ledger, rule_refs):
    fact_trace = [item["fact_id"] for item in ledger["facts"][:2]]
    prompt = (
        "Create a clean ecommerce white-background product image. "
        "Use the uploaded product references as the source of truth. "
        "Keep the visible product identity, quantities, colors, proportions, and included items unchanged. "
        "No added text, watermark, logo, price, badge, or extra accessory."
    )
    return _normalize_n4_prompt(
        {
            "slot_id": str(hero_input["slot_order"]),
            "main_scene": "pure white ecommerce studio",
            "main_action": "none",
            "visible_text_lines": [],
            "prompt": prompt,
            "character_count": len(prompt),
            "reference_plan": {
                "primary_asset_id": identity["primary_asset_id"],
                "supporting_asset_ids": list(identity.get("supporting_asset_ids", [])),
                "include_completed_white_image": False,
            },
            "fact_trace": fact_trace,
            "inference_trace": [],
            "rule_refs": sorted(rule_refs),
            "generation_parameters": {
                "model": "gpt-image-2",
                "n": 1,
                "size": hero_input.get("size") or "1:1",
                "resolution": hero_input.get("resolution") or "1k",
            },
            "review_required": True,
        },
        hero_input["slot_order"],
        identity,
        ledger,
        rule_refs,
    )


def _fallback_n5_plans(
    marketing_input,
    marketing_slots,
    fact_ids,
    inference_ids,
    target_appearance_ids,
):
    _require_prompt_os_fallback()
    fact_ref = next(iter(fact_ids), "")
    appearances = sorted(target_appearance_ids or [])
    seed_style = str(marketing_input.get("seed_style") or "").strip()
    product_label = _chinese_product_phrase(str(marketing_input.get("product_name") or "").strip()) or "商品"
    action_menu = _fallback_product_action_menu(product_label)
    recipes = [
        {
            "mode": "fab_value",
            "family": "core-benefit",
            "task": "从商品最显眼的真实特征提炼第一张营销钩子，让买家立刻知道它为什么值得点开",
            "theme": f"{product_label}第一眼记忆点",
            "moment": f"{product_label}被放在一个有生活结果的开场画面里，动作围绕{action_menu}展开，商品的轮廓、颜色和关键使用部位在第一眼就成立",
            "aesthetic": "明亮商业摄影混合一点杂志广告感，背景真实但留白干净，主体轮廓利落，颜色有轻微反差和记忆点",
            "typography": "左上角两行目标语言标题，第一行 34px 粗体无衬线，第二行 22px 常规无衬线，行距 1.08，颜色取商品主色的深色版本，文字宽度约画面 36%，直接压在自然留白上",
            "scene": f"{product_label}出现在真实可拥有的使用空间里，空间可以由品类自由选择为餐桌、玄关、梳妆台、书桌、床边、礼物台或户外随身场景",
            "action": f"用一次正在发生的{action_menu}动作，让{product_label}和一个清楚的日常结果同框",
            "camera": "轻俯三分之四中近景，镜头离商品很近但保留环境线索",
            "composition": "商品占画面 58%–65%，前景给商品，背景只保留一到两个有记忆点的生活结果线索，文字落在自然浅色留白区",
        },
        {
            "mode": "scene_ownership",
            "family": "detail-trust",
            "task": "把材质、边缘、连接、纹理或开合处拍成可信细节，让买家产生真实拥有感",
            "theme": f"{product_label}可触摸细节",
            "moment": f"手指、光线或阴影停在{product_label}最值得看的纹理、边缘、接口、弧面或接触点，像买家正在近距离检查实物",
            "aesthetic": "微距商业质感，柔和侧光拉出纹理阴影，背景虚化成低饱和层次，画面有高级产品目录的精致感",
            "typography": "右上角两行目标语言短句，第一行 30px 半粗无衬线，第二行 20px 常规无衬线，深灰或深棕文字，行距 1.12，文字宽度约画面 32%，直接排在虚化浅背景上",
            "scene": f"{product_label}的细节检查画面，场景可以是手边台面、布面、托盘、抽屉、鞋柜、包内或其他符合品类的真实接触表面",
            "action": f"用手指轻触、掀开、拉近、拿起边缘或让光扫过{product_label}的关键细节，使材质层次和结构关系可见",
            "camera": "近景微距或半微距，浅景深，焦点落在商品细节和接触动作上",
            "composition": "关键细节占画面 50%–58%，完整商品轮廓保留在中景或边缘可识别，背景形成斜向或纵深层次",
        },
        {
            "mode": "emotion",
            "family": "real-use",
            "task": "做一张真人或手部参与的真实使用图，让买家看到商品进入生活的一秒",
            "theme": f"{product_label}刚好用上的一秒",
            "moment": f"人物手部、身体局部或使用环境正在把{product_label}带入一个明确动作，画面像真实生活里刚发生的一帧",
            "aesthetic": "真实生活商业摄影，暖白自然光，场景有温度和轻微动态感，背景不拥挤，商品质感仍是主角",
            "typography": "左侧或上方自然留白排两行目标语言文案，第一行 32px 粗体圆润无衬线，第二行 21px 常规无衬线，文字占画面宽度约 34%，加极轻柔光阴影",
            "scene": f"{product_label}进入真实生活的一秒，动作围绕{action_menu}展开，具体空间由品类和买家动机决定",
            "action": f"让真人手部或身体局部和{product_label}形成一个清楚接触点，从{action_menu}里选择一个动作完成画面叙事",
            "camera": "平视中近景或略低机位，人眼高度，保留手部动作和商品细节",
            "composition": "商品占画面 54%–62%，人物或手部占 18%–28% 辅助说明，前景动作清楚，中后景提供生活情绪",
        },
        {
            "mode": "personification",
            "family": "function-explain",
            "task": "用轻拟人、舞台感或商品主角感做功能说明，让画面更有记忆点",
            "theme": f"{product_label}小剧场说明",
            "moment": f"{product_label}像主角一样站在一个微型广告场面里，周围道具、光线和姿态共同讲一个简单但具体的价值点",
            "aesthetic": "轻广告剧场感，背景干净，色彩可以更大胆一点，商品材质保持真实，画面有趣但不变成卡通说明书",
            "typography": "画面上方居中一行 32px 粗体目标语言标题，下面一行 20px 短副标题，文字总高约画面 8%，颜色与主题道具形成清楚对比，字距正常",
            "scene": f"{product_label}主导的微型舞台、桌面陈列、收纳角、礼物角或使用前后同框场景，由商品品类决定最有趣的空间",
            "action": f"用{product_label}的位置、朝向、层次和周围道具关系，讲清一个功能或使用结果",
            "camera": "有秩序的俯视、轻俯视或正面小舞台机位",
            "composition": "商品位于中心偏下 52%–60% 区域，道具呈弧线、对角线或前后排布辅助叙事，画面像广告海报而不是说明书",
        },
        {
            "mode": "identity_signal",
            "family": "scale-context",
            "task": "让商品进入一种审美、身份、送礼或尺寸对照语境，帮助买家判断适不适合自己",
            "theme": f"{product_label}审美和比例选择",
            "moment": f"{product_label}被放进一个有品味的空间、手边物、身体局部或礼物语境中，买家能感到它的尺寸、气质和使用对象",
            "aesthetic": "低饱和编辑感商业摄影，材质干净，空间留白有呼吸感，可带一点艺术陈列或精品店橱窗感",
            "typography": "右侧竖向或右上角两行目标语言文字，第一行 31px 中粗无衬线，第二行 20px 常规无衬线，文字宽度约画面 30%，排版靠自然留白对齐",
            "scene": f"{product_label}与手、身体局部、宠物、家具、包袋、礼盒、桌面器物或其他尺度线索形成搭配关系",
            "action": f"让{product_label}被拿起、靠近、放入、摆在旁边或与尺度物同框，呈现真实大小和审美适配",
            "camera": "中近景商品角度或平视比例角度",
            "composition": "商品在前景占 56%–62%，尺度或礼物线索在后景或侧边占 20%–28%，背景简洁但有品味记忆点",
        },
        {
            "mode": "fab_value",
            "family": "variant-overview",
            "task": "把买家下单前要确认的款式、颜色、角度、数量或随身尺寸整理清楚",
            "theme": f"{product_label}下单前看清楚",
            "moment": f"{product_label}以整齐但不呆板的方式展开，买家能快速确认外观、数量、组合关系或某个需要比较的角度",
            "aesthetic": "清爽目录摄影带一点生活策展感，均匀柔光，边缘清晰，信息层级像高级商品清单",
            "typography": "左下角小标题组，第一行 28px 粗体无衬线，第二行 19px 常规无衬线，深色文字，宽度约画面 32%，旁边可加细线或小圆点但保持轻量",
            "scene": f"{product_label}的对照、角度、数量或内容确认画面，表面可选择浅色桌面、布面、托盘、抽屉、旅行包内或干净地面",
            "action": f"把{product_label}按对角线、弧线、网格或打开状态整理出来，让数量和结构一眼可数",
            "camera": "俯视目录角度或轻俯角度",
            "composition": "目标外观或商品角度占画面 62%–70%，文字靠边轻量呈现，留白像商品编辑页而不是表格",
        },
        {
            "mode": "scene_ownership",
            "family": "lifestyle-desire",
            "task": "制造想拥有的生活方式画面，让买家看到商品进入日常之后的气氛",
            "theme": f"{product_label}放进日常之后",
            "moment": f"{product_label}已经自然出现在一个完整生活片段里，早餐、通勤、收纳、床边、桌面、礼物或外出场景都可以按品类自由发挥",
            "aesthetic": "柔和编辑感电商摄影，浅景深，背景光斑克制，色彩与商品主色呼应，画面像可购买的生活杂志切片",
            "typography": "顶部留白放两行目标语言标题，第一行 33px 粗体无衬线，第二行 21px 常规无衬线，颜色为暖深灰，文字宽度约画面 38%，透明叠字配轻阴影",
            "scene": f"{product_label}融入日常节奏的生活方式场景，空间有真实使用痕迹但保持整洁",
            "action": f"让{product_label}已经完成一次{action_menu}动作，画面表现拥有后的轻松感",
            "camera": "柔和编辑式电商角度，平视或轻俯都可",
            "composition": "商品功能部位在画面中景占 52%–58%，生活背景提供情绪和尺度，前后景有自然遮挡和空气感",
        },
        {
            "mode": "emotion",
            "family": "conversion-final",
            "task": "补上最后一个转化理由，让买家感觉这件商品已经准备好进入今天的生活",
            "theme": f"{product_label}今天就能用上",
            "moment": f"{product_label}以完整、清楚、有吸引力的状态收束整套图，像广告最后一帧，商品已经摆好、拿好或进入待使用状态",
            "aesthetic": "更精致的收尾广告摄影，主光略强，背景干净，商品边缘有清楚高光，可加入一点庆祝、礼物、出门前或整理完成的情绪线索",
            "typography": "画面左上或上方居中放两行目标语言收尾文案，第一行 35px 粗体无衬线，第二行 22px 常规无衬线，文字占画面宽度 36%–42%，排在真实光影留白上",
            "scene": f"{product_label}兼具清晰和情绪触发的转化收尾画面，空间由模型按品类选择更有想象力但可信的生活或礼物语境",
            "action": f"把{product_label}的一个实际使用结果和一个情绪触发点合在同一个画面里，动作从{action_menu}中选择最适合本品类的一种",
            "camera": "平衡商业商品角度，主体清晰，背景有轻微纵深",
            "composition": "商品占画面 58%–64%，背景干净但有生活线索，文字与商品形成斜向视觉流，整体像套图的收束海报",
        },
    ]
    plans = []
    covered = set()
    for index, slot in enumerate(marketing_slots):
        recipe = recipes[max(0, slot.order - 2) % len(recipes)]
        selected = []
        if appearances:
            selected = [appearances[index % len(appearances)]]
            covered.update(selected)
            if index == len(marketing_slots) - 1:
                selected = list(dict.fromkeys([*selected, *(set(appearances) - covered)]))
                covered.update(selected)
        purpose = slot.purpose or slot.name or f"第 {slot.order} 张营销图"
        fact_refs = [fact_ref] if fact_ref else []
        plan = {
            "slot_order": slot.order,
            "role": purpose,
            "appearance_ids": selected,
            "scene_family": f"{recipe['family']}-{slot.order}",
            "environment": recipe["scene"],
            "camera": recipe["camera"],
            "visual_theme": recipe["theme"],
            "specific_moment": recipe["moment"],
            "aesthetic_point_of_view": recipe["aesthetic"],
            "typography_direction": recipe["typography"],
            "decision_task": recipe["task"],
            "conversion_goal": recipe["task"],
            "fact_refs": fact_refs,
            "inference_refs": [],
            "main_scene": recipe["scene"],
            "main_action": recipe["action"],
            "subject_relationship": "上传商品是画面主体，多款、多色或重复实例按当前槽位需要的外观和数量展示",
            "composition": recipe["composition"],
            "copy_intent": recipe["task"],
            "text_mode": "up_to_3_lines",
            "visible_text_lines": [],
            "localization_notes": [],
            "must_show": ["product clearly visible"],
            "must_avoid": ["random text", "wrong product", "extra logo", "unprovided claim", "discount badge"],
            "seed_style": seed_style,
            "prompt_source": "fallback",
            "creative_strategy": _fallback_creative_strategy(
                {
                    "fact_refs": fact_refs,
                    "decision_task": recipe["task"],
                    "copy_intent": recipe["task"],
                    "subject_relationship": "the uploaded product remains the main subject",
                    "main_scene": recipe["scene"],
                    "must_show": ["product clearly visible"],
                },
                recipe["mode"],
            ),
        }
        plans.append(plan)
    result = _normalize_n5_plans(
        {"plans": plans, "prompt_source": "fallback"},
        marketing_slots,
        fact_ids,
        inference_ids,
        target_appearance_ids,
    )
    result["prompt_source"] = "fallback"
    for plan in result["plans"]:
        plan["prompt_source"] = "fallback"
    return result


def _fallback_marketing_copy_lines(plan, product_name, language):
    if str(plan.get("text_mode") or "").lower() == "none":
        return []
    slot_order = int(plan.get("slot_order") or 2)
    index = max(0, min(7, slot_order - 2))
    language_key = str(language or "en").lower()
    copy_bank = {
        "vi": [
            ["Gọn gàng sau bữa", "Dùng là thấy tiện"],
            ["Nhìn rõ từng chi tiết", "Chọn yên tâm hơn"],
            ["Dùng đúng lúc cần", "Việc nhỏ nhẹ hơn"],
            ["Ở nhà hay mang đi", "Luôn vừa tay"],
            ["Đặt vào là gọn", "Lấy ra là dùng"],
            ["Trọn bộ dễ chọn", "Không phải nghĩ thêm"],
            ["Hợp với nhịp sống của bạn", "Nhìn là muốn dùng"],
            ["Sẵn sàng cho hôm nay", "Chọn một lần, dùng lâu"],
        ],
        "th": [
            ["หยิบใช้แล้วรู้สึกสะดวก", "เก็บง่ายทุกวัน"],
            ["เห็นรายละเอียดชัด", "เลือกได้มั่นใจกว่า"],
            ["ใช้ได้พอดีกับจังหวะชีวิต", "เรื่องเล็กง่ายขึ้น"],
            ["อยู่บ้านหรือพกไปก็ลงตัว", "หยิบใช้สบายมือ"],
            ["วางแล้วเป็นระเบียบ", "หยิบใช้ได้ทันที"],
            ["ครบชุด เลือกง่าย", "ไม่ต้องคิดเยอะ"],
            ["เข้ากับวันของคุณ", "เห็นแล้วอยากใช้"],
            ["พร้อมสำหรับวันนี้", "เลือกครั้งเดียว ใช้ได้นาน"],
        ],
        "ms": [
            ["Kemas selepas digunakan", "Mudah dipakai setiap hari"],
            ["Butiran jelas dilihat", "Lebih yakin untuk pilih"],
            ["Berguna tepat pada masanya", "Rutin jadi lebih mudah"],
            ["Di rumah atau dibawa keluar", "Selesa di tangan"],
            ["Simpan kemas, ambil mudah", "Sedia bila diperlukan"],
            ["Satu set yang senang dipilih", "Tak perlu fikir lama"],
            ["Sesuai dengan gaya harian anda", "Nampak terus ingin guna"],
            ["Sedia untuk hari ini", "Pilih sekali, guna lama"],
        ],
        "id": [
            ["Rapi setelah dipakai", "Mudah untuk sehari-hari"],
            ["Detail terlihat jelas", "Lebih yakin memilih"],
            ["Pas saat dibutuhkan", "Rutinitas jadi ringan"],
            ["Di rumah atau dibawa pergi", "Nyaman digenggam"],
            ["Simpan rapi, ambil cepat", "Siap saat perlu"],
            ["Satu set, mudah dipilih", "Tak perlu bingung"],
            ["Cocok untuk hari Anda", "Lihat sekali ingin pakai"],
            ["Siap untuk hari ini", "Pilih sekali, pakai lama"],
        ],
        "zh": [
            ["用完好收納", "每天都順手"],
            ["細節看得見", "選得更安心"],
            ["剛好用在需要的時刻", "小事變輕鬆"],
            ["在家或外出都合適", "拿起來就順手"],
            ["放好就整齊", "要用隨手拿"],
            ["一套就好選", "不用多想"],
            ["放進你的日常", "看見就想用"],
            ["今天就能用上", "選一次，用很久"],
        ],
        "pt": [
            ["Tudo no lugar depois de usar", "Prático no dia a dia"],
            ["Detalhes que dão confiança", "Escolha com mais segurança"],
            ["Na hora certa, do jeito certo", "A rotina fica mais leve"],
            ["Em casa ou para levar", "Conforto na mão"],
            ["Guardar fácil, usar rápido", "Pronto quando precisar"],
            ["Um conjunto fácil de escolher", "Sem complicar"],
            ["Combina com seu dia", "Dá vontade de usar"],
            ["Pronto para hoje", "Escolha uma vez, use por muito tempo"],
        ],
        "en": [
            ["Tidy after every use", "Ready when you need it"],
            ["Details you can trust", "Choose with confidence"],
            ["Useful right when it matters", "Small tasks feel easier"],
            ["At home or on the go", "Comfort in hand"],
            ["Store neatly, grab quickly", "Ready in seconds"],
            ["A complete set, easy to choose", "No second guessing"],
            ["Fits into your day", "Made to be used"],
            ["Ready for today", "Choose once, use often"],
        ],
    }
    if language_key.startswith("vi"):
        return copy_bank["vi"][index]
    if language_key.startswith("th"):
        return copy_bank["th"][index]
    if language_key.startswith("ms"):
        return copy_bank["ms"][index]
    if language_key.startswith("id"):
        return copy_bank["id"][index]
    if language_key.startswith("zh"):
        return copy_bank["zh"][index]
    if language_key.startswith("pt"):
        return copy_bank["pt"][index]
    return copy_bank["en"][index]


def _fallback_display_prompt(plan, visible_text_lines):
    slot_order = int(plan.get("slot_order") or plan.get("slot_id") or 2)
    visual_theme = str(plan.get("visual_theme") or f"第 {slot_order} 张营销主题").strip()
    specific_moment = str(plan.get("specific_moment") or plan.get("main_scene") or "商品处在一个明确的购买决策瞬间").strip()
    aesthetic = str(plan.get("aesthetic_point_of_view") or "真实商业摄影，光线、色彩和材质都服务当前购买理由").strip()
    typography = str(plan.get("typography_direction") or "左上角两行目标语言标题，第一行 32px 粗体无衬线，第二行 21px 常规无衬线，深色文字，宽度约画面 36%").strip()
    main_scene = str(plan.get("main_scene") or "简洁商业摄影场景").strip()
    main_action = str(plan.get("main_action") or "none").strip()
    subject_plan = _normalize_subject_plan(plan)
    composition_plan = _normalize_composition_plan(plan)
    style_plan = _normalize_style_plan(plan)
    text_layout_theme = str(plan.get("text_layout_theme") or _fallback_text_layout_theme(max(0, slot_order - 2))).strip()
    composition = str(plan.get("composition") or composition_plan["shot_scale"]).strip()
    camera = str(plan.get("camera") or composition_plan["camera"]).strip()
    props = "、".join(style_plan["props"])
    props_sentence = f"道具只安排{props}来托住购买情境。" if props else "道具保持克制，只服务商品和购买情境。"
    copy = "、".join(f"“{line}”" for line in visible_text_lines)
    copy_source = f"消费者文案源：画面文字使用{copy}，文案角度贴合“{visual_theme}”。" if copy else f"消费者文案源：本槽位不放可见文字，文案角度由“{visual_theme}”和画面动作承担。"
    goal = str(plan.get("conversion_goal") or plan.get("decision_task") or "让买家快速理解商品价值").strip()
    copy_sentence = (
        f"文字版式：{copy} 逐字进入画面，版式主题为 {text_layout_theme}；{typography}；文字直接落在{composition_plan['text_area']}，用透明叠字和轻微半透明阴影保证可读，保持无大块纯色底的轻量排版。"
        if copy
        else f"文字版式：画面不放可见营销文字，仍按 {text_layout_theme} 与{typography}的版式节奏组织留白，让商品、动作和光影完成转化。"
    )
    return (
        f"生成一张 1:1 Shopee 商品营销图，视觉主题是“{visual_theme}”。{copy_source}"
        f"画面目标：{goal}；画面要直接设计成一个可拍摄的营销瞬间，让买家通过商品、动作、环境和文字同时理解本槽位卖点。"
        f"商品呈现：{subject_plan['product_scope']}；{subject_plan['visible_unit_count']}；参考图只作为商品身份、比例、颜色和材质依据，画面由当前槽位需要的商品子集成为主角；{subject_plan['usage_relationship']}。"
        f"人物与场景：{subject_plan['person_presence']}；主场景为{main_scene}，主要动作是{main_action}；画面瞬间是{specific_moment}。"
        f"构图与镜头：{composition_plan['canvas']}，镜头采用{camera}，景别为{composition_plan['shot_scale']}；商品视觉占比为{composition_plan['subject_share']}，构图为{composition}；文字和商品形成清楚阅读动线，背景只保留有审美和购买理由的空间线索。"
        f"光线材质：{aesthetic}；光线为{style_plan['lighting']}，材质重点是{style_plan['material_focus']}，配色为{style_plan['palette']}。{props_sentence}"
        f"{copy_sentence}"
    )


def _append_visible_copy_lock(prompt, visible_text_lines):
    tail = (
        " Final visible copy lock: render only these exact localized copy lines once: "
        f"{json.dumps(visible_text_lines, ensure_ascii=False)}. "
        "Visible text is limited to that exact copy set."
        if visible_text_lines
        else " Final visible copy lock: keep the image free of visible marketing text, titles, captions, labels, watermarks, and random text."
    )
    prompt = str(prompt or "").strip()
    if len(prompt) + len(tail) + 1 > 3500:
        prompt = prompt[: max(0, 3499 - len(tail))].rstrip()
    return f"{prompt} {tail}".strip()


def _fallback_n6_prompt(slot_input, identity, ledger, rule_refs):
    _require_prompt_os_fallback()
    plan = slot_input["slot_plan"]
    fact_trace = [item["fact_id"] for item in ledger["facts"][:2]]
    appearance_assets = {
        str(asset_id)
        for appearance in identity.get("target_appearances", [])
        if not plan.get("appearance_ids") or appearance.get("appearance_id") in plan.get("appearance_ids", [])
        for asset_id in appearance.get("asset_ids", [])
    }
    supporting = [
        asset_id
        for asset_id in appearance_assets
        if asset_id != str(identity["primary_asset_id"])
    ][:3]
    style = str(plan.get("seed_style") or "").strip()
    language = slot_input.get("market_context", {}).get("language") or "en"
    product_label = slot_input.get("product_name") or "the uploaded product"
    if not _language_allows_cjk(language) and _contains_cjk(product_label):
        product_label = "the uploaded product"
    visible_text_lines = _fallback_marketing_copy_lines(plan, product_label, language)
    visual_theme = str(plan.get("visual_theme") or "focused ecommerce visual theme").strip()
    specific_moment = str(plan.get("specific_moment") or plan["main_scene"]).strip()
    aesthetic = str(plan.get("aesthetic_point_of_view") or "polished commercial photography with clear material detail").strip()
    typography_plan = str(plan.get("typography_direction") or "top-left two-line sans-serif title, first line 32px bold, second line 21px regular, dark text, about 36% image width").strip()
    text_layout_theme = str(plan.get("text_layout_theme") or _fallback_text_layout_theme(max(0, int(slot_input["slot_order"]) - 2))).strip()
    subject_plan = _normalize_subject_plan(plan)
    composition_plan = _normalize_composition_plan(plan)
    style_plan = _normalize_style_plan(plan)
    props = ", ".join(style_plan["props"]) if style_plan["props"] else "only restrained props that support the buying moment"
    display_prompt = _fallback_display_prompt(plan, visible_text_lines)
    prompt_parts = [
        f"Create a polished 1:1 Shopee ecommerce marketing image for {product_label}.",
        f"Visual theme: {visual_theme}.",
        f"Specific moment: {specific_moment}.",
        f"Typography plan: {typography_plan}.",
        f"Director brief in Chinese for composition, moment, lighting, material, scene, product count, and typography: {display_prompt}",
        "Use the references only for product identity, visible structure, color, proportion, and material cues.",
        f"Product scope: {subject_plan['product_scope']}. Visible unit count: {subject_plan['visible_unit_count']}. Usage/contact relationship: {subject_plan['usage_relationship']}.",
        f"person/hand/pet/scale context: {subject_plan['person_presence']}.",
        f"Scene and action: {plan['main_scene']}; {plan['main_action']}; exact moment: {specific_moment}.",
        f"Camera and composition: {composition_plan['canvas']}; {composition_plan['camera']}; {composition_plan['shot_scale']}; subject share {composition_plan['subject_share']}; text area {composition_plan['text_area']}; composition detail {plan['composition']}.",
        f"Lighting, material, palette, props: lighting {style_plan['lighting']}; material focus {style_plan['material_focus']}; palette {style_plan['palette']}; props {props}.",
        f"Aesthetic direction: {aesthetic}. Keep creative freedom in background styling, small props, light, posture, and spatial rhythm while preserving the uploaded product identity.",
    ]
    if style:
        prompt_parts.insert(4, f"Creative style to blend in: {style}.")
    if visible_text_lines:
        prompt_parts.append(
            f"Text layout theme: {text_layout_theme}. Typography plan: {typography_plan}. Use transparent text overlay, mobile-readable typography on natural negative space with subtle translucent shadow. "
            "Visible copy set, rendered exactly once: "
            f"{json.dumps(visible_text_lines, ensure_ascii=False)}."
        )
    else:
        prompt_parts.append("No visible marketing text; let the product, action, light, and composition carry the selling point.")
    prompt = _append_visible_copy_lock(" ".join(prompt_parts), visible_text_lines)
    return _normalize_n6_prompt(
        {
            "slot_id": str(slot_input["slot_order"]),
            "main_scene": plan["main_scene"],
            "main_action": plan["main_action"],
            "visual_theme": visual_theme,
            "text_layout_theme": text_layout_theme,
            "typography_plan": typography_plan,
            "prompt_source": "fallback",
            "display_prompt": display_prompt,
            "visible_text_lines": visible_text_lines,
            "localized_copy": {
                "language": language,
                "lines": visible_text_lines,
                "source_fact_refs": fact_trace,
                "source_inference_refs": [],
            },
            "prompt": prompt,
            "character_count": len(prompt),
            "reference_plan": {
                "primary_asset_id": identity["primary_asset_id"],
                "supporting_asset_ids": supporting,
                "completed_white_result_id": None,
            },
            "fact_trace": fact_trace,
            "inference_trace": [],
            "rule_refs": sorted(rule_refs),
            "generation_parameters": {
                "model": "gpt-image-2",
                "n": 1,
                "size": slot_input.get("size") or "1:1",
                "resolution": slot_input.get("resolution") or "1k",
            },
            "review_required": True,
        },
        slot_input["slot_order"],
        identity,
        ledger,
        rule_refs,
    )


def _fallback_n7_semantic(deterministic_gate):
    return _normalize_n7_semantic(
        {
            "decision": "block" if deterministic_gate.get("hard_blocks") else "pass",
            "hard_blocks": list(deterministic_gate.get("hard_blocks", [])),
            "semantic_risks": [],
            "warnings": ["语义复核模型异常，已保留确定性规则检查结果。"],
            "copy_checks": {},
            "prompt_checks": {},
            "resolved_rule_refs": list(deterministic_gate.get("resolved_rule_refs", [])),
            "review_required": True,
        }
    )


def _merge_n7_gate(deterministic_gate, semantic_gate):
    merged = copy.deepcopy(deterministic_gate)
    raw_semantic_blocks = [str(item) for item in semantic_gate.get("hard_blocks", [])]
    semantic_blocks = [
        item for item in raw_semantic_blocks if _STRICT_SEMANTIC_BLOCK_RE.search(item)
    ]
    soft_blocks = [
        item for item in raw_semantic_blocks if item not in set(semantic_blocks)
    ]
    merged["hard_blocks"] = list(
        dict.fromkeys([*merged.get("hard_blocks", []), *semantic_blocks])
    )
    for field in ("semantic_risks", "warnings", "advice", "inference_disclosures"):
        merged[field] = list(
            dict.fromkeys(
                [
                    *copy.deepcopy(merged.get(field, [])),
                    *copy.deepcopy(semantic_gate.get(field, [])),
                ]
            )
        )
    for field in ("prompt_checks", "copy_checks"):
        merged[field] = {
            **copy.deepcopy(semantic_gate.get(field, {})),
            **copy.deepcopy(merged.get(field, {})),
        }
    merged["rewrite_reasons"] = list(
        dict.fromkeys(
            [
                *merged.get("rewrite_reasons", []),
                *semantic_gate.get("rewrite_reasons", []),
            ]
        )
    )
    merged["resolved_rule_refs"] = list(
        dict.fromkeys(
            [
                *merged.get("resolved_rule_refs", []),
                *semantic_gate.get("resolved_rule_refs", []),
            ]
        )
    )
    if semantic_gate.get("decision") == "block" and not semantic_blocks:
        merged.setdefault("warnings", []).append("semantic_n7_review_warning")
    if soft_blocks:
        merged.setdefault("warnings", []).extend(f"n7.soft_block:{item}" for item in soft_blocks)
    merged["decision"] = "block" if merged["hard_blocks"] else "pass"
    return merged


def _run_final_n7_gate(
    client,
    cluster,
    batch,
    slot,
    prompt,
    references,
    *,
    deterministic_gate,
    lineage,
    node_template_binding,
    image_request,
    localized_copy=None,
    marketing_plan=None,
    hero_generation=None,
    parent_prompt_version=None,
    structural_asset_id=None,
):
    _, effective_rules, effective_config = _effective_cluster_resources(batch, cluster)
    n7_node = _platform_prompt_node("N7", effective_config["platform"])
    gate_input = {
        "slot_order": slot.order,
        "prompt": prompt,
        "rule_snapshot": _current_rule_bundle_snapshot(
            batch,
            slot,
            cluster=cluster,
            effective_config=effective_config,
            rule_profile=effective_rules,
        ),
        "marketing_plan": marketing_plan,
        "reference_snapshot": list(references),
        "structural_asset_id": str(
            structural_asset_id
            if structural_asset_id is not None
            else (cluster.analysis_snapshot or {}).get("identity", {}).get("primary_asset_id")
        ),
        "prompt_node_template": node_template_binding,
        "image_request": image_request,
        "localized_copy": copy.deepcopy(localized_copy) if localized_copy else None,
        "hero_generation_id": str(hero_generation.id) if hero_generation else None,
        "parent_prompt_version_id": (
            str(parent_prompt_version.id) if parent_prompt_version else None
        ),
        "lineage": lineage,
        "deterministic_gate": copy.deepcopy(deterministic_gate),
    }
    semantic_model = "deterministic-rule-engine"
    if getattr(settings, "PROMPT_OS_SEMANTIC_N7_ENABLED", False):
        semantic_model = settings.APIMART_PROMPT_MODEL
        try:
            semantic_gate = _prompt_node_json(
                client,
                n7_node,
                "Review semantic risks for this final slot request. Preserve every deterministic hard block and add risks or blocks only when supported by the supplied facts and rules.",
                gate_input,
                normalize=_normalize_n7_semantic,
            )
        except Exception:
            semantic_gate = _fallback_n7_semantic(deterministic_gate)
            semantic_model = "deterministic-rule-engine"
    else:
        semantic_gate = _fallback_n7_semantic(deterministic_gate)
    gate = _merge_n7_gate(deterministic_gate, semantic_gate)
    snapshot = _node_snapshot(
        n7_node,
        semantic_model,
        gate_input,
        gate,
        slot_id=slot.id,
    )
    return gate, snapshot


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
        if node_template is not None
        else "deterministic-v2.0.0"
        if model_id == "deterministic-rule-engine"
        else "builtin-v2.0.0"
    )
    snapshot = {
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
    temperature = _prompt_node_temperature(node_id)
    if temperature is not None and model_id == settings.APIMART_PROMPT_MODEL:
        snapshot["temperature"] = temperature
    return snapshot


def _prompt_node_json(client, node_id, instruction, payload, normalize=None, repair=None):
    system_instruction, _ = _prompt_node_contract(node_id)
    node_text = _render_node_user_message(node_id, instruction, payload)
    temperature = _prompt_node_temperature(node_id)
    request_payload = {
        "system": system_instruction,
        "text": node_text,
    }
    if temperature is not None:
        request_payload["temperature"] = temperature
    response = client.optimize_prompt(request_payload)

    def repair_invalid_json(text):
        repair_payload = {
            "system": system_instruction,
            "text": "\n".join(
                [
                    node_text,
                    "The previous response was invalid. Repair it once.",
                    _json_repair_prompt(
                        text,
                        f"Return the valid JSON object required by NODE {node_id}.",
                    ),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ]
            ),
        }
        if temperature is not None:
            repair_payload["temperature"] = temperature
        return client.optimize_prompt(repair_payload)

    repair = repair or (
        repair_invalid_json
    )
    def validate(value):
        _validate_prompt_node_schema(node_id, value)
        return normalize(value) if normalize is not None else value

    return _validated_provider_json(response, validate, repair)


def _prompt_node_text(client, node_id, instruction, payload):
    system_instruction, _ = _prompt_node_contract(node_id)
    node_text = _render_node_user_message(node_id, instruction, payload)
    temperature = _prompt_node_temperature(node_id)
    return _prompt_node_text_from_rendered(client, system_instruction, node_text, temperature)


def _prompt_node_text_from_rendered(client, system_instruction, node_text, temperature=None):
    request_payload = {
        "system": system_instruction,
        "text": node_text,
    }
    if temperature is not None:
        request_payload["temperature"] = temperature
    response = client.optimize_prompt(request_payload)
    text = str(response.get("output_text") or "").strip()
    if not text:
        raise RuntimeError(PROMPT_GENERATION_FAILED_MESSAGE)
    return text


def _deepseek_text_n5_plans(raw_text, marketing_input, marketing_slots, fact_ids, inference_ids, target_appearance_ids):
    text = str(raw_text or "").strip()
    fact_ref = next(iter(fact_ids), "")
    appearances = sorted(target_appearance_ids or [])
    covered = set()
    plans = []
    modes = list(MARKETING_STRATEGY_MODES)
    for index, slot in enumerate(marketing_slots):
        selected = []
        if appearances:
            selected = [appearances[index % len(appearances)]]
            covered.update(selected)
            if index == len(marketing_slots) - 1:
                selected = list(dict.fromkeys([*selected, *(set(appearances) - covered)]))
        fact_refs = [fact_ref] if fact_ref else []
        theme = f"DeepSeek 原文方案 {slot.order}"
        plan = {
            "slot_order": slot.order,
            "role": slot.purpose or slot.name or theme,
            "appearance_ids": selected,
            "scene_family": f"deepseek-text-{slot.order}",
            "environment": f"槽位 {slot.order} 按 DeepSeek 原文设计：{text}",
            "camera": f"按 DeepSeek 原文镜头执行，槽位 {slot.order}",
            "visual_theme": theme,
            "specific_moment": text,
            "aesthetic_point_of_view": text,
            "typography_direction": text,
            "decision_task": text,
            "conversion_goal": text,
            "fact_refs": fact_refs,
            "inference_refs": [],
            "main_scene": text,
            "main_action": f"按 DeepSeek 原文动作执行，槽位 {slot.order}",
            "subject_relationship": text,
            "composition": f"按 DeepSeek 原文构图执行，槽位 {slot.order}",
            "copy_intent": text,
            "text_mode": "up_to_3_lines",
            "visible_text_lines": [],
            "localization_notes": [],
            "must_show": [],
            "must_avoid": [],
            "prompt_source": "deepseek",
            "raw_model_text": text,
            "creative_strategy": _fallback_creative_strategy(
                {
                    "fact_refs": fact_refs,
                    "decision_task": text,
                    "copy_intent": text,
                    "subject_relationship": text,
                    "main_scene": text,
                    "must_show": [],
                },
                modes[index % len(modes)],
            ),
        }
        plans.append(plan)
    result = _normalize_n5_plans(
        {"plans": plans, "prompt_source": "deepseek", "raw_model_text": text},
        marketing_slots,
        fact_ids,
        inference_ids,
        target_appearance_ids,
    )
    result["raw_model_text"] = text
    return result


def _deepseek_text_n6_prompt(raw_text, slot_input, identity, ledger, rule_refs):
    text = str(raw_text or "").strip()
    plan = slot_input.get("slot_plan") or {}
    fact_trace = [item["fact_id"] for item in ledger["facts"][:2]]
    prompt = _limit_generation_prompt(text, [], limit=3500)
    payload = {
        "slot_id": str(slot_input["slot_order"]),
        "main_scene": str(plan.get("main_scene") or text or "DeepSeek image design"),
        "main_action": str(plan.get("main_action") or "follow the DeepSeek image design"),
        "visual_theme": str(plan.get("visual_theme") or f"DeepSeek design {slot_input['slot_order']}"),
        "text_layout_theme": str(plan.get("text_layout_theme") or _fallback_text_layout_theme(max(0, int(slot_input["slot_order"]) - 2))),
        "typography_plan": str(plan.get("typography_direction") or text or "follow the DeepSeek typography design"),
        "display_prompt": text,
        "visible_text_lines": [],
        "localized_copy": {
            "language": slot_input.get("market_context", {}).get("language") or "en",
            "lines": [],
            "source_fact_refs": [],
            "source_inference_refs": [],
        },
        "prompt": prompt,
        "character_count": len(prompt),
        "reference_plan": {
            "primary_asset_id": identity["primary_asset_id"],
            "supporting_asset_ids": [],
            "completed_white_result_id": None,
        },
        "fact_trace": fact_trace,
        "inference_trace": [],
        "rule_refs": sorted(rule_refs),
        "generation_parameters": {
            "model": "gpt-image-2",
            "n": 1,
            "size": slot_input.get("size") or "1:1",
            "resolution": slot_input.get("resolution") or "1k",
        },
        "review_required": True,
        "prompt_source": "deepseek",
        "raw_model_text": text,
    }
    return _normalize_n6_prompt(payload, slot_input["slot_order"], identity, ledger, rule_refs)


def _prompt_node_n5_plan(client, node_id, instruction, payload, marketing_slots, fact_ids, inference_ids, target_appearance_ids):
    text = _prompt_node_text(client, node_id, instruction, payload)
    try:
        value = _json_object(text)
        _validate_prompt_node_schema(node_id, value)
        result = _normalize_n5_plans(
            value,
            marketing_slots,
            fact_ids,
            inference_ids,
            target_appearance_ids,
        )
    except Exception:
        result = _deepseek_text_n5_plans(
            text,
            payload,
            marketing_slots,
            fact_ids,
            inference_ids,
            target_appearance_ids,
        )
    result["prompt_source"] = "deepseek"
    result.setdefault("raw_model_text", text)
    for plan in result["plans"]:
        plan["prompt_source"] = "deepseek"
        plan.setdefault("raw_model_text", text)
    return result


def _prompt_node_n6_plan(client, node_id, instruction, payload, identity, ledger, rule_refs):
    text = _prompt_node_text(client, node_id, instruction, payload)
    return _normalize_n6_model_text(text, node_id, None, payload, identity, ledger, rule_refs)


def _normalize_n6_model_text(text, node_id, output_schema, payload, identity, ledger, rule_refs):
    try:
        value = _json_object(text)
        if output_schema is None:
            _validate_prompt_node_schema(node_id, value)
        elif output_schema:
            _validate_json_schema(value, output_schema)
        result = _normalize_n6_prompt(value, payload["slot_order"], identity, ledger, rule_refs)
    except Exception:
        result = _deepseek_text_n6_prompt(text, payload, identity, ledger, rule_refs)
    result["prompt_source"] = "deepseek"
    result.setdefault("raw_model_text", text)
    return result


def _prompt_node_n6_plan_from_rendered(client, node_id, rendered, payload, identity, ledger, rule_refs):
    system_instruction, node_text, temperature, output_schema = rendered
    text = _prompt_node_text_from_rendered(
        client,
        system_instruction,
        node_text,
        temperature,
    )
    return _normalize_n6_model_text(text, node_id, output_schema, payload, identity, ledger, rule_refs)


def _parallel_prompt_client(client):
    if client.__class__ is APIMartClient:
        return APIMartClient(timeout=client.timeout)
    return client


def _identity_facts(identity):
    profile = identity.get("product_profile") if isinstance(identity.get("product_profile"), dict) else {}
    values = []
    for value in profile.values():
        if isinstance(value, list):
            values.extend(_string_list(value))
        elif value:
            values.append(str(value).strip())
    return "; ".join(dict.fromkeys(item for item in values if item))


def _fact_text_items(value):
    items = []
    for item in _string_list(value):
        items.extend(part.strip() for part in item.split(";") if part.strip())
    return list(dict.fromkeys(items))


def _target_observed_product_facts(observations, identity):
    target_asset_ids = {str(identity.get("primary_asset_id") or "")}
    target_asset_ids.update(str(item) for item in _string_list(identity.get("supporting_asset_ids")))
    for appearance in identity.get("target_appearances", []):
        if not isinstance(appearance, dict):
            continue
        target_asset_ids.update(str(item) for item in _string_list(appearance.get("asset_ids")))
        if appearance.get("primary_asset_id"):
            target_asset_ids.add(str(appearance["primary_asset_id"]))
    target_asset_ids.discard("")
    facts = []
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        asset_id = str(observation.get("asset_id") or "")
        if target_asset_ids and asset_id not in target_asset_ids:
            continue
        if observation.get("contains_target_product") is False:
            continue
        facts.extend(_fact_text_items(observation.get("product_facts") or observation.get("facts")))
    return list(dict.fromkeys(facts))


def _n5_consumer_context(identity, observations, product_facts):
    profile = identity.get("product_profile") if isinstance(identity.get("product_profile"), dict) else {}
    target_consumer = next(
        (
            _clean_schema_placeholder(item.get("target_consumer"))
            for item in observations
            if _clean_schema_placeholder(item.get("target_consumer"))
        ),
        "",
    )
    observed_facts = _target_observed_product_facts(observations, identity)
    combined_facts = list(dict.fromkeys([*_fact_text_items(product_facts), *observed_facts]))
    return {
        "target_consumer": str(target_consumer or ""),
        "verified_use_relationships": _string_list(profile.get("verified_use_relationships")),
        "product_facts": combined_facts,
        "product_category": str(_clean_schema_placeholder(profile.get("category")) or ""),
        "primary_appearance": str(_clean_schema_placeholder(profile.get("primary_appearance")) or ""),
    }


def _normalized_marketing_plans(marketing_plan, marketing_slots):
    raw_plans = marketing_plan.get("plans")
    if not isinstance(raw_plans, list):
        raw_plans = marketing_plan.get("slot_plans")
    if not isinstance(raw_plans, list):
        raw_plans = marketing_plan.get("slots", [])
    slot_orders_by_name = {slot.name: slot.order for slot in marketing_slots}
    plans = {}
    for index, item in enumerate(raw_plans):
        if not isinstance(item, dict):
            continue
        raw_order = item.get("slot_order", item.get("slot_id"))
        if str(raw_order or "").isdigit():
            order = int(raw_order)
        else:
            order = slot_orders_by_name.get(str(item.get("slot_name") or ""))
        if order is None and len(raw_plans) == len(marketing_slots):
            order = marketing_slots[index].order
        if order is None:
            continue
        normalized = dict(item)
        normalized["slot_order"] = order
        normalized.setdefault("main_scene", normalized.get("primary_scene", ""))
        normalized.setdefault("main_action", normalized.get("primary_action", "none"))
        normalized.setdefault(
            "scene_family",
            normalized.get("scene_title")
            or normalized.get("role")
            or normalized.get("decision_task")
            or normalized.get("main_scene")
            or f"slot-{order}",
        )
        normalized.setdefault(
            "conversion_goal",
            normalized.get("decision_task") or normalized.get("copy_intent", ""),
        )
        normalized.setdefault("visible_text_lines", [])
        plans[order] = normalized
    return plans


def _repair_marketing_plan_schema(client, marketing_plan, marketing_slots):
    required = [
        {"slot_order": slot.order, "slot_name": slot.name}
        for slot in marketing_slots
    ]
    response = client.optimize_prompt(
        {
            "system": (
                "Normalize an ecommerce marketing plan into exactly one valid JSON object. "
                "Do not add or remove slots and return no markdown or explanation."
            ),
            "text": "\n".join(
                [
                    "Return this exact root schema:",
                    '{"plans":[{"slot_order":2,"scene_family":"string","conversion_goal":"string",'
                    '"main_scene":"string","main_action":"string","visible_text_lines":[]}]}',
                    f"Required slots: {json.dumps(required, ensure_ascii=False)}",
                    f"Previous JSON: {json.dumps(marketing_plan, ensure_ascii=False)[:6000]}",
                ]
            ),
        }
    )
    return _json_object(response.get("output_text", ""))


def _repair_marketing_plan_response(client, text, marketing_slots):
    try:
        marketing_plan = _json_object(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        marketing_plan = {}
    return {
        "output_text": json.dumps(
            _repair_marketing_plan_schema(client, marketing_plan, marketing_slots),
            ensure_ascii=False,
        )
    }


def _identity_input_signature(cluster, cluster_assets):
    return _snapshot_hash(
        {
            "cluster_version": cluster.version,
            "product_name": cluster.product_name,
            "product_facts": cluster.product_facts,
            "relation_type": cluster.relation_type,
            "assets": [
                {
                    "asset_id": str(relation.asset_id),
                    "content_hash": relation.asset.sha256,
                    "role": relation.role,
                    "order": relation.order,
                }
                for relation in cluster_assets
                if relation.asset.kind == Asset.Kind.IMAGE
            ],
        }
    )


def _effective_config_ready(config):
    return (
        config.get("platform") in SUPPORTED_PLATFORMS
        and isinstance(config.get("market"), str)
        and bool(config["market"].strip())
    )


def _effective_config_signature(batch, cluster):
    return _snapshot_hash(_configuration_signature(batch, cluster))


def _runtime_node_bindings(node_names):
    nodes = []
    for node_name in node_names:
        template = _published_prompt_node(node_name)
        if template is None:
            instruction, version = _prompt_node_contract(node_name)
            nodes.append(
                {
                    "node_name": node_name,
                    "version": version,
                    "content_hash": _snapshot_hash(
                        {"instruction": instruction, "output_schema": {}}
                    ),
                }
            )
        else:
            nodes.append(
                {
                    "node_name": node_name,
                    "version": template.version,
                    "content_hash": _snapshot_hash(
                        {
                            "instruction": template.instruction,
                            "user_message_template": template.user_message_template,
                            "output_schema": template.output_schema,
                        }
                    ),
                }
            )
    return nodes


def _identity_runtime_contract_fingerprint():
    return _snapshot_hash(
        {
            "nodes": _runtime_node_bindings(["N1", "N2"]),
            "vision_model": settings.APIMART_VISION_MODEL,
            "prompt_model": settings.APIMART_PROMPT_MODEL,
        }
    )


def _prompt_runtime_contract_fingerprint(batch, cluster):
    config = _effective_config(batch, cluster)
    return _snapshot_hash(
        {
            "nodes": _runtime_node_bindings(
                [
                    "N1",
                    "N2",
                    "N3",
                    "N4",
                    _platform_prompt_node("N5", config["platform"]),
                    _platform_prompt_node("N6", config["platform"]),
                    _platform_prompt_node("N7", config["platform"]),
                ]
            ),
            "vision_model": settings.APIMART_VISION_MODEL,
            "prompt_model": settings.APIMART_PROMPT_MODEL,
            "image_model": settings.APIMART_IMAGE_MODEL,
        }
    )


def cluster_preparation_is_current(cluster):
    analysis = cluster.analysis_snapshot if isinstance(cluster.analysis_snapshot, dict) else {}
    return (
        cluster.preparation_status == Cluster.PreparationStatus.READY
        and analysis.get("_runtime_contract_fingerprint")
        == _prompt_runtime_contract_fingerprint(cluster.batch, cluster)
    )


def _current_rule_bundle_snapshot(
    batch,
    slot,
    *,
    cluster=None,
    effective_config=None,
    rule_profile=None,
):
    if cluster is not None and (effective_config is None or rule_profile is None):
        _, resolved_profile, resolved_config = _effective_cluster_resources(batch, cluster)
        effective_config = effective_config or resolved_config
        rule_profile = rule_profile or resolved_profile
    selected_profile = rule_profile if rule_profile is not None else batch.rule_profile
    snapshot = _rule_snapshot(selected_profile)
    snapshot["resolved_rules"] = _applicable_rules(
        batch,
        slot,
        effective_config=effective_config,
        rule_profile=selected_profile,
    )
    return snapshot


def _preparation_lineage(cluster, batch, slot, *, cluster_assets=None, template=None):
    cluster_assets = cluster_assets or list(
        cluster.cluster_assets.select_related("asset").order_by("order", "id")
    )
    effective_template, effective_rules, effective_config = _effective_cluster_resources(
        batch, cluster
    )
    template = template or effective_template
    return {
        "preparation_revision": _preparation_revision(cluster.analysis_snapshot),
        "cluster_version": cluster.version,
        "identity_input_signature": _identity_input_signature(cluster, cluster_assets),
        "effective_config_signature": _effective_config_signature(batch, cluster),
        "template_content_hash": _snapshot_hash(_template_snapshot(template, slot)),
        "rule_bundle_content_hash": _snapshot_hash(
            _current_rule_bundle_snapshot(
                batch,
                slot,
                cluster=cluster,
                effective_config=effective_config,
                rule_profile=effective_rules,
            )
        ),
        "runtime_contract_fingerprint": _prompt_runtime_contract_fingerprint(
            batch, cluster
        ),
    }


def _identity_reference_paths(cluster_assets, identity):
    paths_by_id = {
        str(relation.asset_id): relation.asset.storage_path
        for relation in cluster_assets
        if relation.asset.kind == Asset.Kind.IMAGE
    }
    asset_ids = [
        identity.get("primary_asset_id"),
        *identity.get("supporting_asset_ids", [])[:3],
    ]
    references = [
        paths_by_id[str(asset_id)]
        for asset_id in asset_ids
        if asset_id is not None and str(asset_id) in paths_by_id
    ]
    if not references:
        raise ValueError("N2 did not approve a valid product reference")
    return list(dict.fromkeys(references))


def _appearance_reference_paths(cluster_assets, identity, appearance_ids=None):
    paths_by_id = {
        str(relation.asset_id): relation.asset.storage_path
        for relation in cluster_assets
        if relation.asset.kind == Asset.Kind.IMAGE
    }
    appearances = identity.get("target_appearances") or []
    selected = set(
        [item.get("appearance_id") for item in appearances]
        if appearance_ids is None
        else appearance_ids
    )
    asset_ids = []
    for item in appearances:
        if item.get("appearance_id") not in selected:
            continue
        primary_asset_id = item.get("primary_asset_id")
        if primary_asset_id is not None:
            asset_ids.append(primary_asset_id)
        asset_ids.extend(item.get("asset_ids") or [])
    references = [
        paths_by_id[str(asset_id)]
        for asset_id in asset_ids
        if asset_id is not None and str(asset_id) in paths_by_id
    ]
    return list(dict.fromkeys(references)) or _identity_reference_paths(cluster_assets, identity)


def _market_language(market):
    return {
        "MY": "ms-MY",
        "TH": "th-TH",
        "VN": "vi-VN",
        "ID": "id-ID",
        "TW": "zh-TW",
        "BR": "pt-BR",
    }.get(str(market or "").upper(), "en")


def _marketing_diversity_valid(marketing_plan):
    try:
        _validate_marketing_diversity(marketing_plan.get("plans", []))
    except ValueError:
        return False
    return True


def _claim_prompt_cluster(candidate):
    claimed = Cluster.objects.filter(
        id=candidate.id,
        preparation_status=Cluster.PreparationStatus.PENDING,
        archived_at__isnull=True,
        analysis_snapshot=copy.deepcopy(candidate.analysis_snapshot),
    ).update(
        preparation_status=Cluster.PreparationStatus.PREPARING,
        preparation_error="",
        preparation_stage="N1",
        preparation_current=0,
        preparation_total=7,
        updated_at=timezone.now(),
    )
    if not claimed:
        return None
    return Cluster.objects.select_related(
        "batch",
        "batch__owner",
        "batch__output_template",
    ).get(id=candidate.id)


def _claim_next_prompt_cluster():
    base = Cluster.objects.filter(
        preparation_status=Cluster.PreparationStatus.PENDING,
        archived_at__isnull=True,
    ).order_by("updated_at", "created_at", "id")
    with transaction.atomic():
        queryset = base
        if connection.features.has_select_for_update_skip_locked:
            queryset = queryset.select_for_update(skip_locked=True)
        candidate = queryset.first()
        if candidate is None:
            return None
        candidate.preparation_status = Cluster.PreparationStatus.PREPARING
        candidate.preparation_error = ""
        candidate.preparation_stage = "N1"
        candidate.preparation_current = 0
        candidate.preparation_total = 7
        candidate.updated_at = timezone.now()
        if connection.features.has_select_for_update_skip_locked:
            candidate.save(
                update_fields=[
                    "preparation_status",
                    "preparation_error",
                    "preparation_stage",
                    "preparation_current",
                    "preparation_total",
                    "updated_at",
                ]
            )
            return Cluster.objects.select_related(
                "batch",
                "batch__owner",
                "batch__output_template",
            ).get(id=candidate.id)
        claimed = Cluster.objects.filter(
            id=candidate.id,
            preparation_status=Cluster.PreparationStatus.PENDING,
            archived_at__isnull=True,
            analysis_snapshot=copy.deepcopy(candidate.analysis_snapshot),
        ).update(
            preparation_status=Cluster.PreparationStatus.PREPARING,
            preparation_error="",
            preparation_stage="N1",
            preparation_current=0,
            preparation_total=7,
            updated_at=timezone.now(),
        )
    if not claimed:
        return None
    return Cluster.objects.select_related(
        "batch",
        "batch__owner",
        "batch__output_template",
    ).get(id=candidate.id)


def _restore_stale_preparations():
    stale_before = timezone.now() - timedelta(seconds=settings.PROMPT_PREPARATION_STALE_SECONDS)
    Cluster.objects.filter(
        preparation_status=Cluster.PreparationStatus.PREPARING,
        updated_at__lt=stale_before,
        archived_at__isnull=True,
    ).update(
        preparation_status=Cluster.PreparationStatus.PENDING,
        preparation_stage="queued",
        preparation_error="",
        updated_at=timezone.now(),
    )


def process_prompt_once(client=None, storage=None):
    client = client or (FakeAPIMartClient() if settings.APIMART_FAKE_MODE else APIMartClient())
    storage = storage or LocalStorage()
    _restore_stale_preparations()
    cluster = _claim_next_prompt_cluster()
    if cluster is None:
        return 0
    claimed_revision = _preparation_revision(cluster.analysis_snapshot)
    try:
        _set_preparation_progress(cluster.id, claimed_revision, "N1", 0)
        cluster_assets = list(cluster.cluster_assets.select_related("asset").order_by("order", "id"))
        image_relations = [
            relation for relation in cluster_assets if relation.asset.kind == Asset.Kind.IMAGE
        ]
        if not image_relations:
            raise ValueError("N1 requires at least one product image")
        previous = cluster.analysis_snapshot if isinstance(cluster.analysis_snapshot, dict) else {}
        current_identity_signature = _identity_input_signature(cluster, cluster_assets)
        identity_revision = previous.get("identity_revision", {})
        reuse_identity = (
            isinstance(identity_revision, dict)
            and identity_revision.get("signature") == current_identity_signature
            and identity_revision.get("runtime_contract_fingerprint")
            == _identity_runtime_contract_fingerprint()
            and isinstance(previous.get("observations"), list)
            and isinstance(previous.get("identity"), dict)
        )
        if reuse_identity:
            observations = copy.deepcopy(previous["observations"])
            identity = copy.deepcopy(previous["identity"])
            node_snapshots = [
                copy.deepcopy(snapshot)
                for snapshot in previous.get("prompt_os", [])
                if snapshot.get("node_id") in {"N1", "N2"}
            ]
        else:
            node_snapshots = []
            observations = []
            observation_system, _ = _prompt_node_contract("N1")
            references = [relation.asset.storage_path for relation in image_relations]
            with storage.reference_paths(references) as image_paths:
                def observe_relation(index, relation, image_path):
                    observation_input = {
                        "asset_id": str(relation.asset_id),
                        "asset_kind": "owned_product",
                        "product_name": cluster.product_name,
                        "confirmed_points": _string_list(cluster.product_facts),
                    }
                    try:
                        observation = _validated_provider_json(
                            client.observe_images(
                                "\n".join(
                                    [
                                        "NODE N1",
                                        f"ASSET_ID={relation.asset_id}",
                                        observation_system,
                                        _render_node_user_message(
                                            "N1",
                                            "Observe only visible product evidence in this single owned-product image. Return strict JSON with visible identity, candidate product name, confidence, role, and reference quality.",
                                            observation_input,
                                        ),
                                    ]
                                ),
                                [image_path],
                            ),
                            lambda value, asset_id=relation.asset_id: _normalize_n1_observation(
                                value,
                                asset_id,
                            ),
                            lambda text, node_input=observation_input, path=image_path: _repair_observation_json(
                                client,
                                text,
                                system_instruction=observation_system,
                                observation_input=node_input,
                                image_path=path,
                            ),
                        )
                    except Exception:
                        fallback_name = cluster.product_name.strip()
                        if fallback_name == "名称待确认" or _is_schema_placeholder(fallback_name):
                            fallback_name = ""
                        observation = _fallback_n1_observation(
                            relation.asset_id,
                            fallback_name,
                        )
                    return index, observation_input, observation

                n1_jobs = list(enumerate(zip(image_relations, image_paths)))
                n1_workers = min(
                    len(n1_jobs),
                    max(1, int(getattr(settings, "PROMPT_OS_SLOT_CONCURRENCY", 1))),
                )
                if n1_workers <= 1:
                    n1_results = [
                        observe_relation(index, relation, image_path)
                        for index, (relation, image_path) in n1_jobs
                    ]
                else:
                    with ThreadPoolExecutor(max_workers=n1_workers) as executor:
                        futures = [
                            executor.submit(observe_relation, index, relation, image_path)
                            for index, (relation, image_path) in n1_jobs
                        ]
                        n1_results = [future.result() for future in as_completed(futures)]
                    n1_results.sort(key=lambda item: item[0])
                for _, observation_input, observation in n1_results:
                    observations.append(observation)
                    node_snapshots.append(
                        _node_snapshot(
                            "N1",
                            settings.APIMART_VISION_MODEL,
                            observation_input,
                            observation,
                        )
                    )
            if not any(
                item.get("contains_target_product")
                and item.get("target_is_physical_product")
                for item in observations
            ):
                fallback_name = cluster.product_name.strip()
                if fallback_name == "名称待确认" or _is_schema_placeholder(fallback_name):
                    fallback_name = ""
                observations[0] = _force_minimal_product_observation(
                    observations[0],
                    fallback_name,
                )
                for snapshot in node_snapshots:
                    if snapshot.get("node_id") == "N1" and str(snapshot["output_snapshot"].get("asset_id")) == str(observations[0]["asset_id"]):
                        snapshot["output_snapshot"] = copy.deepcopy(observations[0])
                        snapshot["output_hash"] = _snapshot_hash(observations[0])
                        break

            observed_name = next(
                (
                    item["candidate_product_name"]
                    for item in sorted(
                        observations,
                        key=lambda value: value["candidate_product_name_confidence"],
                        reverse=True,
                    )
                    if item["contains_target_product"]
                ),
                "",
            )
            observed_facts = [
                fact
                for item in observations
                for fact in _string_list(item.get("product_facts") or item.get("facts"))
            ]
            confirmed_product_name = cluster.product_name.strip()
            if confirmed_product_name == "名称待确认":
                confirmed_product_name = ""
            if previous.get("product_name_source") == "ai":
                confirmed_product_name = ""
            identity_input = {
                "product_name": confirmed_product_name or observed_name,
                "confirmed_points": _string_list(cluster.product_facts) or observed_facts,
                "relation_type": cluster.relation_type,
                "observations": observations,
                "max_supporting_images": 3,
            }
            valid_asset_ids = {
                str(item["asset_id"])
                for item in observations
                if item.get("contains_target_product")
                and item.get("target_is_physical_product")
            }
            required_primary_asset_id = next(
                (
                    relation.asset_id
                    for relation in image_relations
                    if str(relation.asset_id) in valid_asset_ids
                ),
                None,
            )
            _set_preparation_progress(cluster.id, claimed_revision, "N2", 1)
            try:
                identity = _prompt_node_json(
                    client,
                    "N2",
                    "Merge owned observations into one product identity. Report ERP/visual conflict_state, select one primary asset, at most three supporting assets, and an identity lock.",
                    identity_input,
                    normalize=lambda value: _normalize_n2_identity(
                        _n2_observation_fallbacks(value, identity_input),
                        valid_asset_ids,
                        required_primary_asset_id=required_primary_asset_id,
                        require_continue_when_valid=True,
                    ),
                )
                n2_model = settings.APIMART_PROMPT_MODEL
            except Exception:
                identity = _normalize_n2_identity(
                    _n2_observation_fallbacks({}, identity_input),
                    valid_asset_ids,
                    required_primary_asset_id=required_primary_asset_id,
                    require_continue_when_valid=True,
                )
                n2_model = "deterministic-rule-engine"
            node_snapshots.append(
                _node_snapshot("N2", n2_model, identity_input, identity)
            )

        confirmed_product_name = cluster.product_name.strip()
        if confirmed_product_name == "名称待确认":
            confirmed_product_name = ""
        if previous.get("product_name_source") == "ai":
            confirmed_product_name = ""
        if _is_schema_placeholder(confirmed_product_name):
            confirmed_product_name = ""
        identity_product_name = str(identity.get("product_name") or "").strip()
        if identity["decision"] != "continue" or _is_schema_placeholder(identity_product_name):
            identity_product_name = ""
        product_name = str(confirmed_product_name or _chinese_product_phrase(identity_product_name)).strip()
        if product_name:
            identity["product_name"] = product_name
            profile = identity.get("product_profile")
            if isinstance(profile, dict):
                for field in ("category", "primary_appearance"):
                    profile[field] = _chinese_fact_phrase(profile.get(field))
        identity_lock = identity["identity_lock"]
        identity_facts = _identity_facts(identity)
        observed_facts = [
            _chinese_fact_phrase(item)
            for item in _target_observed_product_facts(observations, identity)
        ]
        product_facts = (
            cluster.product_facts
            or "; ".join(observed_facts)
            or _chinese_fact_phrase(identity_facts)
        )
        ledger_confirmed_points = list(
            dict.fromkeys(
                [
                    *(_string_list(product_name) if product_name else []),
                    *_fact_text_items(identity_facts),
                    *observed_facts,
                ]
            )
        )
        cluster_updates = {
            "product_name": product_name,
            "name": product_name or cluster.name,
            "product_facts": product_facts,
            "identity_lock": _identity_text(identity_lock),
        }
        for field, value in cluster_updates.items():
            setattr(cluster, field, value)
        identity_signature = _identity_input_signature(cluster, cluster_assets)
        analysis = {
            "observations": observations,
            "identity": identity,
            "prompt_os": node_snapshots,
            "identity_revision": {
                "signature": identity_signature,
                "cluster_version": cluster.version,
                "asset_ids": [str(relation.asset_id) for relation in image_relations],
                "preparation_revision": claimed_revision,
                "runtime_contract_fingerprint": _identity_runtime_contract_fingerprint(),
            },
            "_preparation_revision": claimed_revision,
            "product_name_source": (
                previous.get("product_name_source") or "manual"
                if confirmed_product_name
                else "ai"
            ),
        }
        if identity["conflict_state"] == "conflict":
            analysis["readiness_warning"] = {
                "code": "identity_conflict_review",
                "message": "商品名和图片识别不完全一致，已按当前商品图继续生成，审核时确认。",
            }
        if identity["decision"] != "continue":
            code = "identity_needs_input"
            analysis["readiness"] = {"status": "blocked", "code": code}
            _persist_prompt_terminal(
                cluster.id,
                claimed_revision,
                [],
                analysis,
                Cluster.PreparationStatus.BLOCKED,
                f"{code}: product identity requires confirmation",
                cluster.batch.owner,
                cluster_updates=cluster_updates,
            )
            return 1

        effective_config = _effective_config(cluster.batch, cluster)
        n5_node = _platform_prompt_node("N5", effective_config["platform"])
        n6_node = _platform_prompt_node("N6", effective_config["platform"])
        n7_node = _platform_prompt_node("N7", effective_config["platform"])
        if not _effective_config_ready(effective_config):
            analysis["readiness"] = {
                "status": "waiting",
                "code": "configuration_required",
                "required_fields": ["platform", "market"],
            }
            _persist_prompt_terminal(
                cluster.id,
                claimed_revision,
                [],
                analysis,
                Cluster.PreparationStatus.BLOCKED,
                "configuration_required: select platform and market",
                cluster.batch.owner,
                cluster_updates=cluster_updates,
            )
            return 1

        config_signature = _effective_config_signature(cluster.batch, cluster)
        target_appearance_ids = {
            item["appearance_id"] for item in identity.get("target_appearances", [])
        }
        approved_references = _appearance_reference_paths(cluster_assets, identity)
        _set_preparation_progress(cluster.id, claimed_revision, "N3", 2)
        ledger_input = {
            "product_name": product_name,
            "confirmed_points": ledger_confirmed_points,
            "product_profile": identity["product_profile"],
            "identity_lock": identity_lock,
            "owned_observations": observations,
            "market_context": {
                "platform": effective_config["platform"],
                "market": effective_config["market"],
                "language": _market_language(effective_config["market"]),
            },
        }
        known_evidence_refs = {
            "product_name",
            "confirmed_points",
            *[f"asset:{item['asset_id']}" for item in observations],
            *[
                f"observation:{snapshot['snapshot_id']}"
                for snapshot in node_snapshots
                if snapshot.get("node_id") == "N1"
            ],
        }
        try:
            ledger = _prompt_node_json(
                client,
                "N3",
                "Classify every fact as confirmed, observed, or inferred with confidence, risk, evidence, and allowed uses.",
                ledger_input,
                normalize=lambda value: _normalize_n3_ledger(
                    value,
                    known_evidence_refs,
                ),
            )
            n3_model = settings.APIMART_PROMPT_MODEL
        except Exception:
            ledger = _fallback_n3_ledger(ledger_input, known_evidence_refs)
            n3_model = "deterministic-rule-engine"
        node_snapshots.append(
            _node_snapshot("N3", n3_model, ledger_input, ledger)
        )

        template, effective_rules, effective_config = _effective_cluster_resources(
            cluster.batch, cluster
        )
        slots = list(template.slots.order_by("order", "id"))
        hero_slot = standard_product_hero_slot(template)
        if hero_slot is None:
            raise ValueError("output template requires a standard product hero")
        generated_slots = [slot for slot in slots if not is_source_product_photo_slot(slot)]
        marketing_slots = [slot for slot in generated_slots if slot.id != hero_slot.id]
        compiled_by_slot = {}
        n6_inputs_by_slot = {}
        n6_rule_refs_by_slot = {}
        fact_ids = {item["fact_id"] for item in ledger["facts"]}

        hero_rules = _applicable_rules(
            cluster.batch,
            hero_slot,
            effective_config=effective_config,
            rule_profile=effective_rules,
        )
        hero_rule_refs = {
            str(rule.get("rule_id")) for rule in hero_rules if rule.get("rule_id")
        }
        hero_input = {
            "slot_order": hero_slot.order,
            "role": "standard_white_background",
            "product_name": product_name,
            "product_profile": identity["product_profile"],
            "identity_lock": identity_lock,
            "fact_ledger": ledger,
            "primary_asset_id": identity["primary_asset_id"],
            "supporting_asset_ids": identity["supporting_asset_ids"],
            "target_appearances": identity.get("target_appearances", []),
            "resolved_rule_directives": [
                rule.get("prompt_directive") for rule in hero_rules
            ],
            "rule_refs": sorted(hero_rule_refs),
            "size": effective_config["size"],
            "resolution": effective_config["resolution"],
            "prompt_limits": {
                "max_characters": 3500,
                "max_text_lines": 0,
                "max_main_scenes": 1,
                "max_main_actions": 1,
            },
        }

        def run_n4_prompt():
            try:
                hero_plan = _prompt_node_json(
                    _parallel_prompt_client(client),
                    "N4",
                    "Compile the standard white-background product hero. One scene, no action, no new visible text.",
                    hero_input,
                    normalize=lambda value: _normalize_n4_prompt(
                        value,
                        hero_slot.order,
                        identity,
                        ledger,
                        hero_rule_refs,
                    ),
                )
                n4_model = settings.APIMART_PROMPT_MODEL
            except Exception:
                hero_plan = _fallback_n4_prompt(hero_input, identity, ledger, hero_rule_refs)
                n4_model = "deterministic-rule-engine"
            return hero_plan, n4_model

        n4_snapshot_index = len(node_snapshots)

        def store_n4_result(hero_plan, n4_model):
            node_snapshots.insert(
                n4_snapshot_index,
                _node_snapshot("N4", n4_model, hero_input, hero_plan, slot_id=hero_slot.id),
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
                preserve_directive_language=True,
            )
            compiled_by_slot[hero_slot.id]["reference_snapshot"] = approved_references
            compiled_by_slot[hero_slot.id]["input_snapshot"]["reference_snapshot"] = approved_references
            compiled_by_slot[hero_slot.id]["node_output"] = hero_plan
            compiled_by_slot[hero_slot.id]["prompt_source"] = hero_plan.get("prompt_source", "deepseek")

        _set_preparation_progress(cluster.id, claimed_revision, "N4", 3)
        n4_executor = None
        n4_future = None
        if marketing_slots and int(getattr(settings, "PROMPT_OS_SLOT_CONCURRENCY", 1)) > 1:
            n4_executor = ThreadPoolExecutor(max_workers=1)
            n4_future = n4_executor.submit(run_n4_prompt)
        else:
            hero_plan, n4_model = run_n4_prompt()
            store_n4_result(hero_plan, n4_model)

        marketing_plan = {"plans": []}
        plans = {}
        if marketing_slots:
            _set_preparation_progress(cluster.id, claimed_revision, "N5", 4)
            inference_ids = {
                item["fact_id"]
                for item in ledger["facts"]
                if item["fact_class"] == "inferred"
            }
            marketing_input = {
                "product_name": product_name,
                "product_profile": identity["product_profile"],
                "identity_lock": identity_lock,
                "fact_ledger": ledger,
                "target_appearances": identity.get("target_appearances", []),
                "consumer_context": _n5_consumer_context(
                    identity,
                    observations,
                    "; ".join(ledger_confirmed_points),
                ),
                "slots": [
                    {"slot_order": slot.order, "name": slot.name, "purpose": slot.purpose}
                    for slot in marketing_slots
                ],
                "market_context": {
                    "platform": effective_config["platform"],
                    "market": effective_config["market"],
                    "seller_tier": effective_config["sellerTier"],
                    "language": _market_language(effective_config["market"]),
                },
                "seed_style": cluster.prompt_override or cluster.batch.global_prompt,
            }
            try:
                marketing_plan = _prompt_node_n5_plan(
                    client,
                    n5_node,
                    "Plan one distinct purchase-decision scene for every supplied marketing slot. Vary scene family, environment, camera, action, and composition.",
                    marketing_input,
                    marketing_slots,
                    fact_ids,
                    inference_ids,
                    target_appearance_ids,
                )
                n5_model = settings.APIMART_PROMPT_MODEL
            except Exception as exc:
                if not _prompt_os_allow_fallback():
                    raise RuntimeError(_sanitize_provider_text(str(exc))) from None
                marketing_plan = _fallback_n5_plans(marketing_input, marketing_slots, fact_ids, inference_ids, target_appearance_ids)
                n5_model = "deterministic-rule-engine"
            node_snapshots.append(
                _node_snapshot(n5_node, n5_model, marketing_input, marketing_plan)
            )
            plans = {plan["slot_order"]: plan for plan in marketing_plan["plans"]}
            _set_preparation_progress(cluster.id, claimed_revision, "N6", 5)
            n6_system_instruction, _ = _prompt_node_contract(n6_node)
            n6_template = _published_prompt_node(n6_node)
            n6_output_schema = copy.deepcopy(n6_template.output_schema) if n6_template is not None else {}
            n6_temperature = _prompt_node_temperature(n6_node)
            n6_jobs = []
            for slot in marketing_slots:
                slot_rules = _applicable_rules(
                    cluster.batch,
                    slot,
                    effective_config=effective_config,
                    rule_profile=effective_rules,
                )
                slot_rule_refs = {
                    str(rule.get("rule_id"))
                    for rule in slot_rules
                    if rule.get("rule_id")
                }
                slot_input = {
                    "slot_order": slot.order,
                    "slot_plan": plans[slot.order],
                    "product_name": product_name,
                    "product_profile": identity["product_profile"],
                    "identity_lock": identity_lock,
                    "fact_ledger": ledger,
                    "market_context": {
                        "platform": effective_config["platform"],
                        "market": effective_config["market"],
                        "language": _market_language(effective_config["market"]),
                        "text_enabled": True,
                    },
                    "primary_asset_id": identity["primary_asset_id"],
                    "supporting_asset_ids": identity["supporting_asset_ids"],
                    "target_appearances": identity.get("target_appearances", []),
                    "appearance_ids": plans[slot.order].get("appearance_ids", []),
                    "resolved_rule_directives": [
                        rule.get("prompt_directive") for rule in slot_rules
                    ],
                    "rule_refs": sorted(slot_rule_refs),
                    "size": effective_config["size"],
                    "resolution": effective_config["resolution"],
                    "prompt_limits": {
                        "max_characters": 3500,
                        "max_text_lines": 3,
                        "max_main_scenes": 1,
                        "max_main_actions": 1,
                    },
                }
                n6_inputs_by_slot[slot.id] = copy.deepcopy(slot_input)
                n6_rule_refs_by_slot[slot.id] = set(slot_rule_refs)
                n6_instruction = (
                    f"SLOT_ORDER={slot.order}\n"
                    "Compile one localized image instruction for this slot with one scene, one main action, and at most three visible text lines."
                )
                n6_rendered = (
                    n6_system_instruction,
                    _render_node_user_message(n6_node, n6_instruction, slot_input),
                    n6_temperature,
                    n6_output_schema,
                )
                n6_jobs.append((slot, slot_input, slot_rule_refs, n6_rendered))

            def run_n6_slot(slot, slot_input, slot_rule_refs, n6_rendered):
                try:
                    slot_plan = _prompt_node_n6_plan_from_rendered(
                        _parallel_prompt_client(client),
                        n6_node,
                        n6_rendered,
                        slot_input,
                        identity,
                        ledger,
                        slot_rule_refs,
                    )
                    n6_model = settings.APIMART_PROMPT_MODEL
                except Exception as exc:
                    if not _prompt_os_allow_fallback():
                        raise RuntimeError(_sanitize_provider_text(str(exc))) from None
                    slot_plan = _fallback_n6_prompt(slot_input, identity, ledger, slot_rule_refs)
                    n6_model = "deterministic-rule-engine"
                return slot, slot_input, slot_rule_refs, slot_plan, n6_model

            n6_workers = min(
                len(n6_jobs),
                max(1, int(getattr(settings, "PROMPT_OS_SLOT_CONCURRENCY", 1))),
            )
            if n6_workers <= 1:
                n6_results = [run_n6_slot(*job) for job in n6_jobs]
            else:
                with ThreadPoolExecutor(max_workers=n6_workers) as executor:
                    futures = [executor.submit(run_n6_slot, *job) for job in n6_jobs]
                    n6_results = [future.result() for future in as_completed(futures)]
                n6_results.sort(key=lambda item: item[0].order)

            for slot, slot_input, slot_rule_refs, slot_plan, n6_model in n6_results:
                node_snapshots.append(
                    _node_snapshot(
                        n6_node,
                        n6_model,
                        slot_input,
                        slot_plan,
                        slot_id=slot.id,
                    )
                )
                compiled_by_slot[slot.id] = compile_slot_prompt(
                    cluster,
                    slot,
                    batch=cluster.batch,
                    template=template,
                    slot_directive=slot_plan["prompt"],
                    visible_text_lines=slot_plan["visible_text_lines"],
                    main_scene=slot_plan["main_scene"],
                    main_action=slot_plan["main_action"],
                    node_name=n6_node,
                    preserve_directive_language=True,
                )
                slot_references = _appearance_reference_paths(
                    cluster_assets,
                    identity,
                    plans[slot.order].get("appearance_ids", []),
                )
                compiled_by_slot[slot.id]["reference_snapshot"] = slot_references
                compiled_by_slot[slot.id]["input_snapshot"][
                    "reference_snapshot"
                ] = slot_references
                compiled_by_slot[slot.id]["node_output"] = slot_plan
                compiled_by_slot[slot.id]["prompt_source"] = slot_plan.get("prompt_source", "deepseek")

        if n4_future is not None:
            try:
                hero_plan, n4_model = n4_future.result()
            finally:
                n4_executor.shutdown(wait=True)
            store_n4_result(hero_plan, n4_model)

        _set_preparation_progress(cluster.id, claimed_revision, "N7", 6)
        gate_blocks = []
        prompt_values = []
        for slot in slots:
            if is_source_product_photo_slot(slot):
                source_relation = next(
                    (
                        relation
                        for relation in cluster_assets
                        if str(relation.asset_id) == str(identity["primary_asset_id"])
                    ),
                    None,
                )
                if source_relation is None:
                    raise ValueError("source slot requires the N2 primary product asset")
                prompt_text = "Preserve the original seller product photo without AI modification."
                final_references = [source_relation.asset.storage_path]
                gate = evaluate_prompt_rule_gate(
                    cluster.batch,
                    slot,
                    prompt_text,
                    references=final_references,
                    effective_config=effective_config,
                    rule_profile=effective_rules,
                )
                compiled = {
                    "node_name": "source_passthrough",
                    "template_version": "builtin-v2.0.0",
                    "provider_model": "none",
                    "prompt": prompt_text,
                    "reference_snapshot": final_references,
                    "template_snapshot": _template_snapshot(template, slot),
                    "rule_snapshot": _current_rule_bundle_snapshot(
                        cluster.batch,
                        slot,
                        cluster=cluster,
                        effective_config=effective_config,
                        rule_profile=effective_rules,
                    ),
                    "input_snapshot": {"source_asset_id": str(source_relation.asset_id)},
                    "evaluation": {"fact_policy": "source-passthrough", "rule_gate": gate},
                    "size": cluster.batch.size or template.default_size,
                    "resolution": cluster.batch.resolution or template.default_resolution,
                }
            else:
                compiled = compiled_by_slot[slot.id]
                prompt_text = compiled["prompt"]
                final_references = list(compiled["reference_snapshot"])
                gate = evaluate_prompt_rule_gate(
                    cluster.batch,
                    slot,
                    prompt_text,
                    visible_text_lines=compiled["node_output"]["visible_text_lines"],
                    localized_copy=compiled["node_output"].get("localized_copy"),
                    fact_ids=fact_ids,
                    references=final_references,
                    effective_config=effective_config,
                    rule_profile=effective_rules,
                )
                rewritten_copy_once = False
                if gate.get("rewrite_reasons") and not gate.get("hard_blocks"):
                    rewrite_input = copy.deepcopy(n6_inputs_by_slot[slot.id])
                    rewrite_input["rewrite_attempt"] = 1
                    rewrite_input["rewrite_reasons"] = gate["rewrite_reasons"]
                    slot_rule_refs = n6_rule_refs_by_slot[slot.id]
                    try:
                        slot_plan = _prompt_node_n6_plan(
                            client,
                            n6_node,
                            (
                                f"SLOT_ORDER={slot.order}\n"
                                "Rewrite this slot once because the rule gate found low-quality, generic, or repeated copy. "
                                "Keep the same product facts, identity, target language, appearance ids, one scene, one action, "
                                "and replace only the localized copy plus matching final image prompt."
                            ),
                            rewrite_input,
                            identity,
                            ledger,
                            slot_rule_refs,
                        )
                        rewrite_model = settings.APIMART_PROMPT_MODEL
                    except Exception as exc:
                        if not _prompt_os_allow_fallback():
                            raise RuntimeError(_sanitize_provider_text(str(exc))) from None
                        slot_plan = _fallback_n6_prompt(rewrite_input, identity, ledger, slot_rule_refs)
                        rewrite_model = "deterministic-rule-engine"
                    node_snapshots.append(
                        _node_snapshot(
                            n6_node,
                            rewrite_model,
                            rewrite_input,
                            slot_plan,
                            slot_id=slot.id,
                        )
                    )
                    compiled_by_slot[slot.id] = compile_slot_prompt(
                        cluster,
                        slot,
                        batch=cluster.batch,
                        template=template,
                        slot_directive=slot_plan["prompt"],
                        visible_text_lines=slot_plan["visible_text_lines"],
                        main_scene=slot_plan["main_scene"],
                        main_action=slot_plan["main_action"],
                        node_name=n6_node,
                        preserve_directive_language=True,
                    )
                    slot_references = _appearance_reference_paths(
                        cluster_assets,
                        identity,
                        plans[slot.order].get("appearance_ids", []),
                    )
                    compiled_by_slot[slot.id]["reference_snapshot"] = slot_references
                    compiled_by_slot[slot.id]["input_snapshot"]["reference_snapshot"] = slot_references
                    compiled_by_slot[slot.id]["node_output"] = slot_plan
                    compiled_by_slot[slot.id]["prompt_source"] = slot_plan.get("prompt_source", "deepseek")
                    compiled = compiled_by_slot[slot.id]
                    prompt_text = compiled["prompt"]
                    final_references = list(compiled["reference_snapshot"])
                    gate = evaluate_prompt_rule_gate(
                        cluster.batch,
                        slot,
                        prompt_text,
                        visible_text_lines=compiled["node_output"]["visible_text_lines"],
                        localized_copy=compiled["node_output"].get("localized_copy"),
                        fact_ids=fact_ids,
                        references=final_references,
                        effective_config=effective_config,
                        rule_profile=effective_rules,
                    )
                    rewritten_copy_once = True
                if rewritten_copy_once and gate.get("rewrite_reasons") and not gate.get("hard_blocks"):
                    gate.setdefault("warnings", []).extend(gate.get("rewrite_reasons", []))
                    gate["rewrite_reasons"] = []
            if (
                slot in marketing_slots
                and not _marketing_diversity_valid(marketing_plan)
            ):
                gate.setdefault("warnings", []).append("prompt.set_diversity")
            lineage = _preparation_lineage(
                cluster,
                cluster.batch,
                slot,
                cluster_assets=cluster_assets,
                template=template,
            )
            node_template_binding = _prompt_node_template_binding(
                compiled["node_name"],
                compiled["template_version"],
            )
            image_request = _image_request_snapshot(
                model=(
                    "none"
                    if is_source_product_photo_slot(slot)
                    else settings.APIMART_IMAGE_MODEL
                ),
                size=compiled["size"],
                resolution=compiled["resolution"],
            )
            gate, gate_snapshot = _run_final_n7_gate(
                client,
                cluster,
                cluster.batch,
                slot,
                prompt_text,
                final_references,
                deterministic_gate=gate,
                lineage=lineage,
                node_template_binding=node_template_binding,
                image_request=image_request,
                localized_copy=compiled.get("node_output", {}).get("localized_copy"),
                marketing_plan=marketing_plan if slot in marketing_slots else None,
                structural_asset_id=identity["primary_asset_id"],
            )
            node_snapshots.append(gate_snapshot)
            gate = copy.deepcopy(gate)
            gate["snapshot_id"] = gate_snapshot["snapshot_id"]
            gate["preparation_revision"] = claimed_revision
            gate["effective_config_signature"] = config_signature
            gate["lineage"] = lineage
            compiled["evaluation"] = {
                **compiled.get("evaluation", {}),
                "rule_gate": gate,
            }
            gate_blocks.extend(gate["hard_blocks"])
            node_temperature = _prompt_node_temperature(compiled["node_name"])
            input_snapshot = copy.deepcopy(compiled["input_snapshot"])
            input_snapshot["_preparation_revision"] = claimed_revision
            input_snapshot["_effective_config_signature"] = config_signature
            input_snapshot["_preparation_lineage"] = lineage
            input_snapshot["_prompt_node_template"] = node_template_binding
            input_snapshot["_image_request"] = image_request
            input_snapshot["reference_snapshot"] = final_references
            if node_temperature is not None:
                input_snapshot["_node_temperature"] = node_temperature
            prompt_source = (
                compiled.get("prompt_source")
                or compiled.get("node_output", {}).get("prompt_source")
            )
            if prompt_source:
                input_snapshot["prompt_source"] = prompt_source
            source_snapshot = copy.deepcopy(input_snapshot)
            structured_output = copy.deepcopy(compiled)
            if prompt_source:
                structured_output["prompt_source"] = prompt_source
            structured_output["_preparation_revision"] = claimed_revision
            structured_output["_effective_config_signature"] = config_signature
            structured_output["_preparation_lineage"] = lineage
            structured_output["_prompt_node_template"] = node_template_binding
            structured_output["_image_request"] = image_request
            structured_output["reference_snapshot"] = final_references
            if node_temperature is not None:
                structured_output["_node_temperature"] = node_temperature
            if slot.order in plans:
                structured_output["marketing_plan"] = copy.deepcopy(plans[slot.order])
            if not is_source_product_photo_slot(slot):
                if not str(prompt_text or "").strip():
                    raise RuntimeError("提示词生成失败，请重试预备生成")
            prompt_values.append({
                "output_slot": slot,
                "node_name": compiled["node_name"],
                "template_version": compiled["template_version"],
                "provider_model": compiled["provider_model"],
                "prompt_text": prompt_text,
                "input_snapshot": input_snapshot,
                "structured_output": structured_output,
                "evaluation": compiled["evaluation"],
                "source_snapshot": source_snapshot,
            })
        analysis.update({
            "fact_ledger": ledger,
            "marketing_plan": marketing_plan,
            "rule_gate": {
                "decision": "block" if gate_blocks else "pass",
                "hard_blocks": list(dict.fromkeys(gate_blocks)),
                "semantic_risks": [],
                "warnings": [],
            },
            "prompt_os": node_snapshots,
            "_preparation_revision": claimed_revision,
            "_effective_config_signature": config_signature,
            "_runtime_contract_fingerprint": _prompt_runtime_contract_fingerprint(
                cluster.batch, cluster
            ),
            "readiness": {
                "status": "blocked" if gate_blocks else "ready",
                "code": "rule_gate_blocked" if gate_blocks else "ready",
            },
        })
        persisted = _persist_prompt_terminal(
            cluster.id, claimed_revision, prompt_values, analysis,
            Cluster.PreparationStatus.BLOCKED if gate_blocks else Cluster.PreparationStatus.READY,
            ", ".join(dict.fromkeys(gate_blocks)), cluster.batch.owner,
            cluster_updates=cluster_updates,
        )
        if not persisted:
            return 1
        cluster.refresh_from_db()
        if cluster.auto_generate and not gate_blocks:
            ensure_cluster_generations(cluster, cluster.batch.owner)
            Cluster.objects.filter(id=cluster.id).update(
                auto_generate=False,
                updated_at=timezone.now(),
            )
        return 1
    except Exception as exc:
        if "n4_executor" in locals() and n4_executor is not None:
            n4_executor.shutdown(wait=False, cancel_futures=True)
        with transaction.atomic():
            locked = Cluster.objects.select_for_update().get(id=cluster.id)
            if locked.preparation_status == Cluster.PreparationStatus.PREPARING and _preparation_revision(locked.analysis_snapshot) == claimed_revision:
                locked.preparation_status = Cluster.PreparationStatus.FAILED
                locked.preparation_error = _sanitize_provider_text(str(exc))
                locked.preparation_stage = "failed"
                locked.save(update_fields=["preparation_status", "preparation_error", "preparation_stage", "updated_at"])
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
    clusters = list(batch.clusters.filter(archived_at__isnull=True))
    cluster_count = len(clusters)
    slot_count = template.slots.count()
    generation_count = 0
    org_used = _used_generations_today()
    user_used = _used_generations_today(user)
    user_limit = user.daily_generation_limit or settings.USER_DAILY_GENERATION_LIMIT
    org_remaining = max(settings.ORG_DAILY_GENERATION_LIMIT - org_used, 0)
    user_remaining = max(user_limit - user_used, 0)
    blocking_errors = []

    if cluster_count == 0:
        blocking_errors.append("batch has no image clusters")
    for cluster in clusters:
        cluster_template, cluster_rules, cluster_config = _effective_cluster_resources(
            batch, cluster
        )
        slots = list(cluster_template.slots.order_by("order", "id"))
        slot_count = max(slot_count, len(slots))
        generated_slots = [slot for slot in slots if not is_source_product_photo_slot(slot)]
        generation_count += len(generated_slots)
        hero_slot = standard_product_hero_slot(cluster_template)
        if hero_slot is None or not is_standard_product_hero_slot(hero_slot):
            blocking_errors.append("output template requires a standard product hero")
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


def _same_slot_n7_snapshot(cluster, slot, snapshot_id):
    snapshots = (
        cluster.analysis_snapshot.get("prompt_os", [])
        if isinstance(cluster.analysis_snapshot, dict)
        else []
    )
    for snapshot in reversed(snapshots):
        if (
            snapshot.get("snapshot_id") == snapshot_id
            and _node_family(snapshot.get("node_id")) == "N7"
            and snapshot.get("slot_id") == str(slot.id)
        ):
            return snapshot
    return None


def _prompt_version_source(prompt_version):
    structured = prompt_version.structured_output if isinstance(prompt_version.structured_output, dict) else {}
    node_output = structured.get("node_output") if isinstance(structured.get("node_output"), dict) else {}
    return str(structured.get("prompt_source") or node_output.get("prompt_source") or "").strip()


def _validate_prompt_version_readiness(
    prompt_version,
    cluster,
    batch,
    slot,
    *,
    references=None,
    allowed_nodes=None,
    image_request=None,
):
    template, effective_rules, effective_config = _effective_cluster_resources(
        batch, cluster
    )
    if prompt_version is None or not prompt_version.prompt_text.strip():
        raise ValueError("Current approved PromptVersion is required before generation")
    if (
        prompt_version.cluster_id != cluster.id
        or prompt_version.output_slot_id != slot.id
    ):
        raise ValueError("PromptVersion must belong to the current product and slot")
    if slot.template_id != template.id:
        raise ValueError("PromptVersion output template is stale")
    if allowed_nodes and _node_family(prompt_version.node_name) not in allowed_nodes:
        expected = "/".join(sorted(allowed_nodes))
        raise ValueError(f"Current PromptVersion must come from {expected}")
    if _prompt_version_source(prompt_version) == "fallback" and not _prompt_os_allow_fallback():
        raise ValueError("fallback PromptVersion cannot enter formal generation")

    revision = _preparation_revision(cluster.analysis_snapshot)
    config_signature = _effective_config_signature(batch, cluster)
    for snapshot in (
        prompt_version.input_snapshot,
        prompt_version.source_snapshot,
        prompt_version.structured_output,
    ):
        if snapshot.get("_preparation_revision") != revision:
            raise ValueError("PromptVersion preparation revision is stale")
        if snapshot.get("_effective_config_signature") != config_signature:
            raise ValueError("PromptVersion effective configuration is stale")
    gate = prompt_version.evaluation.get("rule_gate", {})
    if not gate.get("snapshot_id"):
        raise ValueError("PromptVersion requires passing N7 evidence")
    if gate.get("decision") != "pass" or gate.get("hard_blocks"):
        raise ValueError("PromptVersion is blocked by the deterministic N7 gate")

    current_lineage = _preparation_lineage(cluster, batch, slot)
    current_node_template = _prompt_node_template_binding(
        prompt_version.node_name,
        prompt_version.template_version,
    )
    image_request = image_request or _image_request_snapshot(
        model=(
            "none"
            if is_source_product_photo_slot(slot)
            else settings.APIMART_IMAGE_MODEL
        ),
        size=effective_config["size"] or template.default_size,
        resolution=effective_config["resolution"] or template.default_resolution,
    )
    for snapshot in (
        prompt_version.input_snapshot,
        prompt_version.source_snapshot,
        prompt_version.structured_output,
    ):
        if snapshot.get("_image_request") != image_request:
            raise ValueError("PromptVersion image request content is stale")
        if snapshot.get("_preparation_lineage") != current_lineage:
            raise ValueError("PromptVersion content lineage is stale")
        if snapshot.get("_prompt_node_template") != current_node_template:
            raise ValueError("PromptVersion node template content is stale")

    n7 = _same_slot_n7_snapshot(cluster, slot, gate.get("snapshot_id"))
    if n7 is None:
        raise ValueError("PromptVersion requires a real same-slot N7 record")
    if (
        n7.get("status") != "succeeded"
        or n7.get("input_hash") != _snapshot_hash(n7.get("input_snapshot"))
        or n7.get("output_hash") != _snapshot_hash(n7.get("output_snapshot"))
    ):
        raise ValueError("same-slot N7 record content hash is invalid")
    n7_input = n7.get("input_snapshot", {})
    n7_output = n7.get("output_snapshot", {})
    references = list(
        references
        if references is not None
        else prompt_version.input_snapshot.get("reference_snapshot", [])
    )
    if (
        n7_input.get("lineage") != current_lineage
        or n7_input.get("prompt") != prompt_version.prompt_text
        or n7_input.get("reference_snapshot") != references
        or n7_input.get("prompt_node_template") != current_node_template
        or n7_input.get("image_request") != image_request
    ):
        raise ValueError("same-slot N7 record does not match final submission content")
    primary_asset_id = (
        cluster.analysis_snapshot.get("identity", {}).get("primary_asset_id")
        if isinstance(cluster.analysis_snapshot, dict)
        else None
    )
    if str(n7_input.get("structural_asset_id")) != str(primary_asset_id):
        raise ValueError("same-slot N7 record requires the current structural asset")
    if (
        gate.get("lineage") != current_lineage
        or gate.get("decision") != "pass"
        or gate.get("hard_blocks")
        or n7_output.get("decision") != "pass"
        or n7_output.get("hard_blocks")
    ):
        raise ValueError("PromptVersion is blocked by the deterministic N7 gate")
    return current_lineage


def _validate_generation_readiness(generation, cluster=None, batch=None):
    cluster = cluster or generation.cluster
    batch = batch or generation.batch
    slot = generation.output_slot
    prompt_version = generation.prompt_version
    if prompt_version is None:
        raise ValueError("Generation submission readiness requires PromptVersion")
    allowed_nodes = (
        {"source_passthrough"}
        if is_source_product_photo_slot(slot)
        else {"N4", "N8", "N9"}
        if is_standard_product_hero_slot(slot)
        else {"N6", "N8", "N9"}
    )
    _validate_prompt_version_readiness(
        prompt_version,
        cluster,
        batch,
        slot,
        references=generation.reference_snapshot,
        allowed_nodes=allowed_nodes,
        image_request=_image_request_snapshot(
            model=(
                "none"
                if is_source_product_photo_slot(slot)
                else settings.APIMART_IMAGE_MODEL
            ),
            size=generation.size,
            resolution=generation.resolution,
        ),
    )
    if generation.prompt_text != prompt_version.prompt_text:
        raise ValueError("Generation submission readiness prompt snapshot is inconsistent")
    template, effective_rules, effective_config = _effective_cluster_resources(
        batch, cluster
    )
    if template.status != OutputTemplate.Status.PUBLISHED:
        raise ValueError("Generation submission readiness template is not published")
    if (
        effective_rules is not None
        and effective_rules.status != RuleProfile.Status.PUBLISHED
    ):
        raise ValueError("Generation submission readiness rule profile is not published")
    if generation.template_snapshot != _template_snapshot(template, slot):
        raise ValueError("Generation submission readiness template content is stale")
    if generation.rule_snapshot != _current_rule_bundle_snapshot(
        batch,
        slot,
        cluster=cluster,
        effective_config=effective_config,
        rule_profile=effective_rules,
    ):
        raise ValueError("Generation submission readiness rule content is stale")
    if _node_family(prompt_version.node_name) in {"N8", "N9"}:
        parent_id = prompt_version.source_snapshot.get("parent_prompt_version_id")
        parent = PromptVersion.objects.filter(
            id=parent_id,
            cluster=cluster,
            output_slot=slot,
        ).first()
        if parent is None or _node_family(parent.node_name) not in {"N4", "N6", "N8", "N9"}:
            raise ValueError("Generation submission readiness follow-up lineage is invalid")
    return prompt_version


def _latest_current_completed_hero(cluster, batch, template):
    hero_slot = standard_product_hero_slot(template)
    if hero_slot is None:
        return None
    candidates = (
        cluster.generations.filter(
            output_slot__template=template,
            output_slot=hero_slot,
            status=Generation.Status.COMPLETED,
            result_assets__isnull=False,
        )
        .select_related("prompt_version", "output_slot")
        .prefetch_related("result_assets")
        .order_by("-attempt", "-created_at", "-id")
    )
    for candidate in candidates:
        try:
            _validate_generation_readiness(candidate, cluster, batch)
        except ValueError:
            continue
        return candidate
    return None


def _ensure_source_passthrough_generation(cluster, batch, template, slot, user):
    _, effective_rules, effective_config = _effective_cluster_resources(batch, cluster)
    prompt_version = _approved_prompt_for_slot(cluster, batch, slot)
    source_asset_id = prompt_version.input_snapshot.get("source_asset_id")
    relation = (
        cluster.cluster_assets.select_related("asset")
        .filter(asset_id=source_asset_id, asset__kind=Asset.Kind.IMAGE)
        .first()
    )
    if relation is None:
        raise ValueError("source PromptVersion must identify a current cluster asset")
    references = [relation.asset.storage_path]
    _validate_prompt_version_readiness(
        prompt_version,
        cluster,
        batch,
        slot,
        references=references,
        allowed_nodes={"source_passthrough"},
    )
    for existing in (
        cluster.generations.filter(
            output_slot=slot,
            status=Generation.Status.COMPLETED,
        )
        .select_related("prompt_version")
        .order_by("-attempt", "-created_at", "-id")
    ):
        try:
            _validate_prompt_version_readiness(
                existing.prompt_version,
                cluster,
                batch,
                slot,
                references=existing.reference_snapshot,
                allowed_nodes={"source_passthrough"},
            )
        except ValueError:
            continue
        if existing.reference_snapshot == references and existing.result_assets.exists():
            return existing
    prompt = prompt_version.prompt_text
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=slot,
        prompt_version=prompt_version,
        created_by=user,
        attempt=latest_attempt(cluster, slot) + 1,
        status=Generation.Status.COMPLETED,
        prompt_text=prompt,
        size=effective_config["size"] or template.default_size,
        resolution=effective_config["resolution"] or template.default_resolution,
        reference_snapshot=references,
        template_snapshot=_template_snapshot(template, slot),
        rule_snapshot=_current_rule_bundle_snapshot(
            batch,
            slot,
            cluster=cluster,
            effective_config=effective_config,
            rule_profile=effective_rules,
        ),
        completed_at=timezone.now(),
    )
    source_data = LocalStorage().read(relation.asset.storage_path)
    suffix = Path(relation.asset.storage_path).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    storage_path = (
        f"results/{batch.id}/{cluster.id}/{slot.id}/{generation.attempt}/"
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


def _expected_prompt_node_family(slot):
    if is_source_product_photo_slot(slot):
        return "source_passthrough"
    if is_standard_product_hero_slot(slot):
        return "N4"
    return "N6"


def _manual_prompt_node_name(slot, platform):
    family = _expected_prompt_node_family(slot)
    if family in {"source_passthrough", "N4"}:
        return family
    return _platform_prompt_node(family, platform)


def _manual_prompt_references(cluster, slot):
    relations = list(
        cluster.cluster_assets.select_related("asset")
        .filter(asset__kind=Asset.Kind.IMAGE, asset__archived_at__isnull=True)
        .order_by("order", "id")
    )
    identity = (
        cluster.analysis_snapshot.get("identity", {})
        if isinstance(cluster.analysis_snapshot, dict)
        else {}
    )
    references = _appearance_reference_paths(relations, identity) if identity else [
        relation.asset.storage_path for relation in relations
    ]
    if not references:
        raise ValueError("Current product reference is required before generation")
    return references[:1] if is_source_product_photo_slot(slot) else references


def _manual_prompt_payload(cluster, batch, slot, prompt):
    parent_prompt = _prompt_for_slot(cluster, slot)
    if parent_prompt is not None:
        input_snapshot = copy.deepcopy(parent_prompt.input_snapshot)
        input_snapshot["manual_edit"] = True
        input_snapshot["parent_prompt_version_id"] = str(parent_prompt.id)
        prompt, input_snapshot = apply_standard_product_hero_policy(
            slot,
            prompt,
            input_snapshot,
        )
        return {
            "slot": slot,
            "prompt": prompt,
            "input_snapshot": input_snapshot,
            "structured_output": {
                **copy.deepcopy(parent_prompt.structured_output),
                "manual_edit": True,
                "prompt": prompt,
                "slot_order": slot.order,
            },
            "source_snapshot": copy.deepcopy(parent_prompt.source_snapshot),
            "references": list(
                parent_prompt.input_snapshot.get("reference_snapshot", [])
            )
            or _manual_prompt_references(cluster, slot),
            "parent_prompt": parent_prompt,
            "node_name": parent_prompt.node_name,
            "template_version": parent_prompt.template_version,
            "provider_model": parent_prompt.provider_model,
        }

    _, _, effective_config = _effective_cluster_resources(batch, cluster)
    node_name = _manual_prompt_node_name(slot, effective_config["platform"])
    _, template_version, _ = _prompt_node(node_name)
    references = _manual_prompt_references(cluster, slot)
    base_snapshot = {
        "manual_edit": True,
        "slot_order": slot.order,
        "reference_snapshot": references,
    }
    prompt, input_snapshot = apply_standard_product_hero_policy(
        slot,
        prompt,
        base_snapshot,
    )
    return {
        "slot": slot,
        "prompt": prompt,
        "input_snapshot": input_snapshot,
        "structured_output": {
            **copy.deepcopy(input_snapshot),
            "prompt": prompt,
            "visible_text_lines": [],
            "prompt_source": "manual",
        },
        "source_snapshot": copy.deepcopy(input_snapshot),
        "references": references,
        "parent_prompt": None,
        "node_name": node_name,
        "template_version": template_version,
        "provider_model": settings.APIMART_PROMPT_MODEL,
    }


def _all_generation_prompts_ready(cluster, batch, template):
    for slot in template.slots.order_by("order", "id"):
        if is_source_product_photo_slot(slot):
            continue
        prompt_version = _prompt_for_slot(cluster, slot)
        if prompt_version is None:
            return False
        try:
            _validate_prompt_version_readiness(
                prompt_version,
                cluster,
                batch,
                slot,
                allowed_nodes={_expected_prompt_node_family(slot)},
            )
        except ValueError:
            return False
    return True


def _approved_prompt_for_slot(cluster, batch, slot):
    if cluster.preparation_status != Cluster.PreparationStatus.READY:
        raise ValueError("Product Prompt OS preparation is not ready")
    if not _effective_config_ready(_effective_config(batch, cluster)):
        raise ValueError("Product requires a configured platform and market")
    prompt_version = _prompt_for_slot(cluster, slot)
    if prompt_version is None:
        if is_source_product_photo_slot(slot):
            raise ValueError("Current source PromptVersion is required before passthrough")
        raise ValueError("Current approved PromptVersion is required before generation")
    _validate_prompt_version_readiness(
        prompt_version,
        cluster,
        batch,
        slot,
        allowed_nodes={_expected_prompt_node_family(slot)},
    )
    return prompt_version


def _create_gated_prompt_version(
    *,
    cluster,
    batch,
    slot,
    user,
    node_name,
    template_version,
    provider_model,
    prompt_text,
    input_snapshot,
    structured_output,
    source_snapshot,
    references,
    parent_prompt_version=None,
    hero_generation=None,
    fact_policy="traceable-inference",
    size=None,
    resolution=None,
    image_model=None,
    client=None,
):
    references = list(references)
    prompt_text, input_snapshot = apply_standard_product_hero_policy(
        slot,
        prompt_text,
        copy.deepcopy(input_snapshot),
    )
    _, source_snapshot = apply_standard_product_hero_policy(
        slot,
        prompt_text,
        copy.deepcopy(source_snapshot),
    )
    structured_output = copy.deepcopy(structured_output)
    structured_output["prompt"] = prompt_text
    lineage = _preparation_lineage(cluster, batch, slot)
    node_template_binding = _prompt_node_template_binding(
        node_name,
        template_version,
    )
    template, effective_rules, effective_config = _effective_cluster_resources(
        batch, cluster
    )
    image_request = _image_request_snapshot(
        model=(
            image_model
            or (
                "none"
                if is_source_product_photo_slot(slot)
                else settings.APIMART_IMAGE_MODEL
            )
        ),
        size=size or effective_config["size"] or template.default_size,
        resolution=resolution or effective_config["resolution"] or template.default_resolution,
    )
    identity = cluster.analysis_snapshot.get("identity", {})
    structural_asset_id = identity.get("primary_asset_id")
    gate = evaluate_prompt_rule_gate(
        batch,
        slot,
        prompt_text,
        visible_text_lines=structured_output.get("visible_text_lines")
        or structured_output.get("node_output", {}).get("visible_text_lines"),
        references=references,
        effective_config=effective_config,
        rule_profile=effective_rules,
    )
    client = client or (
        FakeAPIMartClient() if settings.APIMART_FAKE_MODE else APIMartClient()
    )
    gate, gate_snapshot = _run_final_n7_gate(
        client,
        cluster,
        batch,
        slot,
        prompt_text,
        references,
        deterministic_gate=gate,
        lineage=lineage,
        node_template_binding=node_template_binding,
        image_request=image_request,
        hero_generation=hero_generation,
        parent_prompt_version=parent_prompt_version,
    )
    analysis = copy.deepcopy(cluster.analysis_snapshot)
    analysis.setdefault("prompt_os", []).append(gate_snapshot)
    cluster.analysis_snapshot = analysis
    cluster.save(update_fields=["analysis_snapshot", "updated_at"])
    gate = {
        **gate,
        "snapshot_id": gate_snapshot["snapshot_id"],
        "preparation_revision": lineage["preparation_revision"],
        "effective_config_signature": lineage["effective_config_signature"],
        "lineage": lineage,
    }
    if gate["decision"] != "pass" or gate["hard_blocks"]:
        raise ValueError(", ".join(gate["hard_blocks"]) or "N7 semantic review blocked the prompt")

    node_temperature = _prompt_node_temperature(node_name)
    for snapshot in (input_snapshot, source_snapshot, structured_output):
        snapshot["_preparation_revision"] = lineage["preparation_revision"]
        snapshot["_effective_config_signature"] = lineage[
            "effective_config_signature"
        ]
        snapshot["_preparation_lineage"] = lineage
        snapshot["_prompt_node_template"] = node_template_binding
        snapshot["_image_request"] = image_request
        snapshot["reference_snapshot"] = references
        if node_temperature is not None:
            snapshot["_node_temperature"] = node_temperature
        if parent_prompt_version is not None:
            snapshot["parent_prompt_version_id"] = str(parent_prompt_version.id)
    structured_output["prompt"] = prompt_text
    return PromptVersion.objects.create(
        cluster=cluster,
        output_slot=slot,
        created_by=user,
        node_name=node_name,
        template_version=template_version,
        provider_model=provider_model,
        prompt_text=prompt_text,
        input_snapshot=input_snapshot,
        structured_output=structured_output,
        evaluation={"fact_policy": fact_policy, "rule_gate": gate},
        source_snapshot=source_snapshot,
    )


@transaction.atomic
def ensure_cluster_generations(
    cluster,
    user,
    *,
    slot_orders=None,
    force_new=False,
    include_created=False,
):
    batch = Batch.objects.select_for_update().get(id=cluster.batch_id)
    locked = (
        Cluster.objects.select_for_update()
        .prefetch_related("cluster_assets__asset")
        .get(id=cluster.id, batch_id=batch.id)
    )
    if locked.archived_at is not None:
        raise ValueError("Product is archived")
    template, effective_rules, effective_config = _effective_cluster_resources(
        batch, locked
    )
    if template.status != OutputTemplate.Status.PUBLISHED:
        raise ValueError("output template must be published before generation")
    if effective_rules is not None and effective_rules.status != RuleProfile.Status.PUBLISHED:
        raise ValueError("rule profile must be published before generation")
    if not batch.market:
        batch.market = batch.site
    if not batch.size:
        batch.size = template.default_size
    if not batch.resolution:
        batch.resolution = template.default_resolution
    batch.save(update_fields=["market", "size", "resolution", "updated_at"])

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
    hero = _latest_current_completed_hero(locked, batch, template)
    creatable = [
        slot
        for slot in slots
        if not is_source_product_photo_slot(slot)
    ]
    active_statuses = [
        Generation.Status.QUEUED,
        Generation.Status.PREPARING,
        Generation.Status.SUBMITTING,
        Generation.Status.SUBMITTED,
        Generation.Status.PROCESSING,
        Generation.Status.ARCHIVING,
        Generation.Status.SUBMIT_UNKNOWN,
    ]
    existing_statuses = active_statuses if force_new else [*active_statuses, Generation.Status.COMPLETED]
    current_prompts = {}
    existing = {}
    for generation in locked.generations.filter(
            output_slot__in=creatable,
            status__in=existing_statuses,
        ).select_related("prompt_version", "output_slot").order_by(
            "output_slot__order", "-attempt", "-id"
        ):
        if generation.output_slot_id in existing:
            continue
        if generation.status == Generation.Status.COMPLETED:
            prompt_version = current_prompts.get(generation.output_slot_id)
            if prompt_version is None:
                prompt_version = _approved_prompt_for_slot(
                    locked,
                    batch,
                    generation.output_slot,
                )
                current_prompts[generation.output_slot_id] = prompt_version
            if generation.prompt_version_id != prompt_version.id:
                continue
        try:
            _validate_generation_readiness(generation, locked, batch)
        except ValueError:
            continue
        existing[generation.output_slot_id] = generation

    to_create = [slot for slot in creatable if slot.id not in existing]
    approved_prompts = {
        slot.id: _approved_prompt_for_slot(locked, batch, slot)
        for slot in to_create
    }
    reserve_generation_usage(user, len(to_create))
    approved_references = []
    if to_create:
        identity = locked.analysis_snapshot.get("identity", {})
        cluster_assets = list(
            locked.cluster_assets.select_related("asset").order_by("order", "id")
        )
        approved_references = _appearance_reference_paths(cluster_assets, identity)
    created = []
    for slot in to_create:
        base_prompt_version = approved_prompts[slot.id]
        references = list(
            base_prompt_version.input_snapshot.get("reference_snapshot", [])
        ) or approved_references
        if slot.id != hero_slot.id:
            references = [
                reference
                for reference in references
                if reference in approved_references
            ] or approved_references
        prompt_version = base_prompt_version
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
                size=effective_config["size"] or template.default_size,
                resolution=effective_config["resolution"] or template.default_resolution,
                reference_snapshot=references,
                template_snapshot=_template_snapshot(template, slot),
                rule_snapshot=_current_rule_bundle_snapshot(
                    batch,
                    slot,
                    cluster=locked,
                    effective_config=effective_config,
                    rule_profile=effective_rules,
                ),
            )
        )
    Batch.objects.filter(id=batch.id).update(status=Batch.Status.QUEUED, updated_at=timezone.now())
    generations = list(
        locked.generations.select_related("output_slot")
        .filter(output_slot__in=slots)
        .order_by("output_slot__order", "attempt", "id")
    )
    if include_created:
        return generations, [generation.id for generation in created]
    return generations


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
        locked_batch.clusters.filter(archived_at__isnull=True).prefetch_related(
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
    structured_output = copy.deepcopy(previous.structured_output if previous else {})
    structured_output["failure_simplifier"] = output
    structured_output["node_snapshot"] = _node_snapshot(
        "N9",
        settings.APIMART_PROMPT_MODEL,
        simplifier_input,
        output,
        slot_id=generation.output_slot_id,
    )
    user = generation.created_by or generation.batch.owner

    def build_prompt_version(locked_source, locked_cluster, locked_batch):
        locked_previous = locked_source.prompt_version
        prompt_version = _create_gated_prompt_version(
            cluster=locked_cluster,
            batch=locked_batch,
            slot=locked_source.output_slot,
            user=user,
            node_name="N9",
            template_version=_prompt_node_contract("N9")[1],
            provider_model=settings.APIMART_IMAGE_MODEL,
            prompt_text=prompt,
            input_snapshot=input_snapshot,
            structured_output=structured_output,
            source_snapshot=copy.deepcopy(
                locked_previous.source_snapshot
                if locked_previous
                else input_snapshot
            ),
            references=locked_source.reference_snapshot,
            parent_prompt_version=locked_previous,
            size=locked_source.size,
            resolution=locked_source.resolution,
        )
        return {
            "prompt_version": prompt_version,
            "prompt_text": prompt_version.prompt_text,
        }

    return _create_followup_attempt(
        generation,
        user,
        prompt_builder=build_prompt_version,
    )


def _claim_generation_for_submission(generation_id):
    claimed = Generation.objects.filter(
        id=generation_id,
        status=Generation.Status.QUEUED,
        cluster__archived_at__isnull=True,
    ).update(
        status=Generation.Status.SUBMITTING,
        updated_at=timezone.now(),
    )
    if not claimed:
        return None
    return Generation.objects.select_related(
        "batch",
        "batch__owner",
        "batch__output_template",
        "batch__rule_profile",
        "cluster",
        "output_slot__template",
        "prompt_version",
    ).get(id=generation_id)


def _claim_generation_for_polling(generation_id):
    claimed = Generation.objects.filter(
        id=generation_id,
        status__in=[Generation.Status.SUBMITTED, Generation.Status.PROCESSING],
        cluster__archived_at__isnull=True,
    ).update(
        status=Generation.Status.ARCHIVING,
        updated_at=timezone.now(),
    )
    if not claimed:
        return None
    return Generation.objects.select_related(
        "batch",
        "cluster",
        "output_slot",
        "output_slot__template",
    ).get(id=generation_id)


def _claim_next_generation_for_submission(candidates):
    for candidate in candidates:
        claimed = _claim_generation_for_submission(candidate.id)
        if claimed is not None:
            return claimed
    return None


def _claim_next_generation_for_polling(candidates):
    for candidate in candidates:
        claimed = _claim_generation_for_polling(candidate.id)
        if claimed is not None:
            return claimed
    return None


def _current_n2_reference_relations(cluster):
    identity = (
        cluster.analysis_snapshot.get("identity", {})
        if isinstance(cluster.analysis_snapshot, dict)
        else {}
    )
    primary_asset_id = identity.get("primary_asset_id")
    supporting_asset_ids = identity.get("supporting_asset_ids")
    if primary_asset_id is None:
        raise ValueError("Generation submission readiness requires the N2 primary asset")
    if (
        not isinstance(supporting_asset_ids, list)
        or len(supporting_asset_ids) > 3
        or len({str(item) for item in supporting_asset_ids})
        != len(supporting_asset_ids)
    ):
        raise ValueError("Generation submission readiness N2 supporting assets are invalid")
    authorized_ids = [str(primary_asset_id), *[str(item) for item in supporting_asset_ids]]
    if len(set(authorized_ids)) != len(authorized_ids):
        raise ValueError("Generation submission readiness N2 assets must be distinct")
    relations = {
        str(relation.asset_id): relation
        for relation in cluster.cluster_assets.select_related("asset").filter(
            asset__kind=Asset.Kind.IMAGE,
            asset__archived_at__isnull=True,
        )
    }
    if any(asset_id not in relations for asset_id in authorized_ids):
        raise ValueError(
            "Generation submission readiness N2 reference is not a current product asset"
        )
    appearance_primary_ids = []
    appearances = identity.get("target_appearances")
    if appearances is not None:
        if not isinstance(appearances, list) or not appearances:
            raise ValueError(
                "Generation submission readiness N2 target appearances are invalid"
            )
        for appearance in appearances:
            if not isinstance(appearance, dict):
                raise ValueError(
                    "Generation submission readiness N2 target appearances are invalid"
                )
            asset_ids = [str(item) for item in appearance.get("asset_ids", [])]
            appearance_primary_id = str(appearance.get("primary_asset_id") or "")
            if (
                not appearance_primary_id
                or appearance_primary_id not in asset_ids
                or appearance_primary_id not in relations
            ):
                raise ValueError(
                    "Generation submission readiness N2 target appearances are invalid"
                )
            appearance_primary_ids.extend(asset_ids)
        if len(appearance_primary_ids) != len(set(appearance_primary_ids)):
            raise ValueError(
                "Generation submission readiness N2 target appearances are invalid"
            )
    return (
        identity,
        relations[authorized_ids[0]],
        [relations[asset_id] for asset_id in authorized_ids[1:]],
        [relations[asset_id] for asset_id in appearance_primary_ids],
    )


def _authorized_generation_references(generation, cluster, batch):
    _, primary, supporting, appearance_primaries = _current_n2_reference_relations(cluster)
    product_references = [
        relation.asset.storage_path
        for relation in (appearance_primaries or [primary, *supporting])
    ]
    if is_standard_product_hero_slot(generation.output_slot):
        expected = product_references
        hero = None
    else:
        effective_template, _, _ = _effective_cluster_resources(batch, cluster)
        current_hero = _latest_current_completed_hero(
            cluster,
            batch,
            effective_template,
        )
        gate = generation.prompt_version.evaluation.get("rule_gate", {})
        n7 = _same_slot_n7_snapshot(
            cluster,
            generation.output_slot,
            gate.get("snapshot_id"),
        )
        hero_id = (
            n7.get("input_snapshot", {}).get("hero_generation_id")
            if n7
            else None
        )
        hero_path = None
        if current_hero is not None and str(current_hero.id) == str(hero_id):
            hero_path = current_hero.result_assets.order_by(
                "created_at",
                "id",
            ).values_list("storage_path", flat=True).first()
        if hero_path:
            if (
                len(generation.reference_snapshot) != 2
                or generation.reference_snapshot[0] != hero_path
                or generation.reference_snapshot[1] not in product_references
            ):
                raise ValueError(
                    "Generation submission readiness references are outside the current N2 authorization"
                )
            expected = list(generation.reference_snapshot)
            hero = current_hero
        else:
            expected = list(generation.reference_snapshot)
            hero = None
            if not expected or any(path not in product_references for path in expected):
                raise ValueError(
                    "Generation submission readiness references are outside the current N2 authorization"
                )
    if generation.reference_snapshot != expected:
        raise ValueError(
            "Generation submission readiness references are outside the current N2 authorization"
        )
    return {
        "structural_asset_id": str(primary.asset_id),
        "hero_generation_id": str(hero.id) if hero else None,
    }


def _validate_generation_submission(generation, *, cluster=None, batch=None):
    if generation.status != Generation.Status.SUBMITTING:
        raise ValueError("Generation submission readiness requires a claimed item")
    cluster = cluster or Cluster.objects.select_related("batch").get(
        id=generation.cluster_id
    )
    batch = batch or Batch.objects.select_related(
        "output_template",
        "rule_profile",
    ).get(id=generation.batch_id)
    if cluster.archived_at is not None:
        raise ValueError("Generation submission readiness product is archived")
    if cluster.preparation_status != Cluster.PreparationStatus.READY:
        raise ValueError("Generation submission readiness Prompt OS is not ready")
    if not _effective_config_ready(_effective_config(batch, cluster)):
        raise ValueError("Generation submission readiness configuration is incomplete")
    generation.cluster = cluster
    generation.batch = batch
    _validate_generation_readiness(generation, cluster, batch)
    if is_source_product_photo_slot(generation.output_slot):
        raise ValueError("Source passthrough must never reach the paid provider")
    if not generation.reference_snapshot:
        raise ValueError("Generation submission readiness requires final references")
    _authorized_generation_references(generation, cluster, batch)
    return generation


def _generation_submission_snapshot(generation, cluster, batch):
    authorization = _authorized_generation_references(generation, cluster, batch)
    gate = generation.prompt_version.evaluation.get("rule_gate", {})
    provider_prompt = _image_model_submission_prompt(generation, batch, cluster)
    return {
        "generation_id": str(generation.id),
        "batch_id": str(batch.id),
        "cluster_id": str(cluster.id),
        "output_slot_id": str(generation.output_slot_id),
        "prompt_version_id": str(generation.prompt_version_id),
        "n7_snapshot_id": gate.get("snapshot_id"),
        "lineage": _preparation_lineage(
            cluster,
            batch,
            generation.output_slot,
        ),
        "template_snapshot": generation.template_snapshot,
        "rule_snapshot": generation.rule_snapshot,
        "authorization": authorization,
        "request": {
            "prompt": provider_prompt,
            "reference_images": list(generation.reference_snapshot),
            **_image_request_snapshot(
                size=generation.size,
                resolution=generation.resolution,
            ),
        },
    }


@transaction.atomic
def _seal_generation_submission(generation_id):
    candidate = Generation.objects.only("batch_id", "cluster_id").get(
        id=generation_id
    )
    batch = (
        Batch.objects.select_for_update()
        .get(id=candidate.batch_id)
    )
    cluster = Cluster.objects.select_for_update().get(
        id=candidate.cluster_id,
        batch_id=batch.id,
    )
    generation = (
        Generation.objects.select_for_update()
        .get(
            id=generation_id,
            batch_id=batch.id,
            cluster_id=cluster.id,
        )
    )
    generation.prompt_version = PromptVersion.objects.select_for_update().get(
        id=generation.prompt_version_id
    )
    generation.output_slot = (
        OutputSlot.objects.select_for_update()
        .select_related("template")
        .get(id=generation.output_slot_id)
    )
    _validate_generation_submission(
        generation,
        cluster=cluster,
        batch=batch,
    )
    snapshot = _generation_submission_snapshot(generation, cluster, batch)
    sealed = {
        "fingerprint": _snapshot_hash(snapshot),
        "request": snapshot,
    }
    payload = copy.deepcopy(generation.provider_payload)
    current = payload.get("submission") if isinstance(payload, dict) else None
    if current is not None and current != sealed:
        raise ValueError("Generation submission fingerprint is stale")
    payload = payload if isinstance(payload, dict) else {}
    payload["submission"] = sealed
    generation.provider_payload = payload
    generation.save(update_fields=["provider_payload", "updated_at"])
    generation.batch = batch
    generation.cluster = cluster
    return generation


def _lock_submission_dependencies(generation, cluster, batch):
    effective_template, _, _ = _effective_cluster_resources(batch, cluster)
    hero_slot = standard_product_hero_slot(effective_template)
    if hero_slot is not None:
        hero_ids = list(
            Generation.objects.select_for_update()
            .filter(
                batch_id=batch.id,
                cluster_id=cluster.id,
                output_slot_id=hero_slot.id,
                status=Generation.Status.COMPLETED,
            )
            .exclude(id=generation.id)
            .order_by("id")
            .values_list("id", flat=True)
        )
        list(
            ResultAsset.objects.select_for_update()
            .filter(generation_id__in=hero_ids)
            .order_by("id")
        )

    relations = list(
        ClusterAsset.objects.select_for_update()
        .filter(cluster_id=cluster.id)
        .order_by("asset_id")
    )
    list(
        Asset.objects.select_for_update()
        .filter(id__in=[relation.asset_id for relation in relations])
        .order_by("id")
    )

    template_id = generation.output_slot.template_id
    locked_template = OutputTemplate.objects.select_for_update().get(id=template_id)
    locked_slot = OutputSlot.objects.select_for_update().get(id=generation.output_slot_id)
    batch.output_template = locked_template
    generation.output_slot = locked_slot

    rule_filter = Q(seed_key="global-marketplace-prompt-os-v2-rule") | Q(
        platform=batch.platform,
        site="",
    )
    if batch.rule_profile_id:
        rule_filter |= Q(id=batch.rule_profile_id)
    list(
        RuleProfile.objects.select_for_update()
        .filter(rule_filter)
        .order_by("id")
    )
    if batch.rule_profile_id:
        batch.rule_profile = RuleProfile.objects.get(id=batch.rule_profile_id)
    if hasattr(batch, "_prompt_rule_profiles"):
        del batch._prompt_rule_profiles

    list(
        PromptNodeTemplate.objects.select_for_update()
        .filter(node_name=generation.prompt_version.node_name)
        .order_by("id")
    )


def _locked_submission_fingerprint_current(generation_id, fingerprint):
    candidate = Generation.objects.only("batch_id", "cluster_id").get(
        id=generation_id
    )
    batch = (
        Batch.objects.select_for_update()
        .get(id=candidate.batch_id)
    )
    cluster = Cluster.objects.select_for_update().get(
        id=candidate.cluster_id,
        batch_id=batch.id,
    )
    generation = (
        Generation.objects.select_for_update()
        .get(
            id=generation_id,
            batch_id=batch.id,
            cluster_id=cluster.id,
        )
    )
    generation.prompt_version = PromptVersion.objects.select_for_update().get(
        id=generation.prompt_version_id
    )
    generation.output_slot = (
        OutputSlot.objects.select_for_update()
        .select_related("template")
        .get(id=generation.output_slot_id)
    )
    _lock_submission_dependencies(generation, cluster, batch)
    _validate_generation_submission(
        generation,
        cluster=cluster,
        batch=batch,
    )
    sealed = (
        generation.provider_payload.get("submission")
        if isinstance(generation.provider_payload, dict)
        else None
    )
    snapshot = _generation_submission_snapshot(generation, cluster, batch)
    current = _snapshot_hash(snapshot)
    if (
        not isinstance(sealed, dict)
        or sealed.get("fingerprint") != fingerprint
        or sealed.get("request") != snapshot
        or current != fingerprint
    ):
        raise ValueError("Generation submission fingerprint is stale")
    return generation


@transaction.atomic
def _assert_submission_fingerprint_current(generation_id, fingerprint):
    return _locked_submission_fingerprint_current(generation_id, fingerprint)


@transaction.atomic
def _submit_generation_under_lock(
    generation_id,
    fingerprint,
    client,
    image_paths,
    image_urls=None,
):
    generation = _locked_submission_fingerprint_current(
        generation_id,
        fingerprint,
    )
    submission = (
        generation.provider_payload.get("submission")
        if isinstance(generation.provider_payload, dict)
        else {}
    )
    request = (
        submission.get("request", {}).get("request", {})
        if isinstance(submission, dict)
        else {}
    )
    prompt = request.get("prompt") or generation.prompt_text
    if image_urls is None:
        return client.submit_generation(
            prompt,
            image_paths,
            generation.size,
            generation.resolution,
        )
    return client.submit_generation(
        prompt,
        [],
        generation.size,
        generation.resolution,
        image_urls=image_urls,
    )


def _provider_payload_with(generation, **values):
    payload = (
        copy.deepcopy(generation.provider_payload)
        if isinstance(generation.provider_payload, dict)
        else {}
    )
    payload.update(values)
    return payload


def _uploaded_reference_urls(client, storage, storage_paths):
    urls = []
    for storage_path in storage_paths:
        with _APIMART_UPLOAD_URL_CACHE_LOCK:
            cached = _APIMART_UPLOAD_URL_CACHE.get(storage_path)
            if cached:
                urls.append(cached)
                continue
            with storage.reference_paths([storage_path]) as image_paths:
                url = client.upload_image(image_paths[0])
            _APIMART_UPLOAD_URL_CACHE[storage_path] = url
            urls.append(url)
    return urls


PROVIDER_ACTIVE_GENERATION_STATUSES = (
    Generation.Status.SUBMITTING,
    Generation.Status.SUBMITTED,
    Generation.Status.PROCESSING,
)


def _restore_stale_generation_submissions():
    stale_before = timezone.now() - timedelta(seconds=600)
    Generation.objects.filter(
        Q(provider_task_id__isnull=True) | Q(provider_task_id=""),
        status=Generation.Status.SUBMITTING,
        updated_at__lt=stale_before,
        cluster__archived_at__isnull=True,
    ).update(
        status=Generation.Status.QUEUED,
        failure_reason="",
        updated_at=timezone.now(),
    )


def _generation_active_limit():
    provider_limit = max(1, int(getattr(settings, "GENERATION_PROVIDER_ACTIVE_LIMIT", 500)))
    configured = max(1, int(getattr(settings, "MAX_ACTIVE_GENERATIONS", provider_limit)))
    return min(configured, provider_limit)


def _active_provider_generation_count():
    return Generation.objects.filter(
        status__in=PROVIDER_ACTIVE_GENERATION_STATUSES,
        cluster__archived_at__isnull=True,
    ).count()


def generation_worker_batch_size(requested):
    requested = max(1, int(requested))
    active_count = _active_provider_generation_count()
    active_limit = _generation_active_limit()
    if active_count >= active_limit:
        return requested
    return min(requested, max(1, active_limit - active_count))


def _generation_was_canceled(generation):
    if not Generation.objects.filter(id=generation.id, status=Generation.Status.CANCELED).exists():
        return False
    generation.batch.recompute_status()
    return True


def _active_generation_user_counts():
    user_ids = Generation.objects.filter(
        status__in=PROVIDER_ACTIVE_GENERATION_STATUSES,
        cluster__archived_at__isnull=True,
    ).values_list("created_by_id", "batch__owner_id")
    counts = Counter()
    for created_by_id, owner_id in user_ids:
        counts[created_by_id or owner_id] += 1
    return counts


def _generation_owner_id(generation):
    return generation.created_by_id or generation.batch.owner_id


def _queue_is_fair_candidate(candidate, active_by_user, queued_owner_ids):
    soft_limit = max(1, int(getattr(settings, "GENERATION_USER_ACTIVE_SOFT_LIMIT", 10)))
    owner_id = _generation_owner_id(candidate)
    if active_by_user[owner_id] < soft_limit:
        return True
    return not any(active_by_user[queued_owner_id] < soft_limit for queued_owner_id in queued_owner_ids)


def process_generation_once(client=None, storage=None):
    client = client or (FakeAPIMartClient() if settings.APIMART_FAKE_MODE else APIMartClient())
    storage = storage or LocalStorage()
    _restore_stale_generation_submissions()
    queued = None
    active_count = _active_provider_generation_count()
    active_limit = _generation_active_limit()
    queued_candidates = (
        Generation.objects.select_related("batch", "batch__owner", "cluster", "output_slot__template", "prompt_version")
        .filter(status=Generation.Status.QUEUED, cluster__archived_at__isnull=True)
        .order_by("created_at", "id")
    )
    if active_count < active_limit:
        scan_limit = max(1, int(getattr(settings, "GENERATION_QUEUE_CANDIDATE_SCAN_LIMIT", 200)))
        queued_candidates = list(queued_candidates[:scan_limit])
        active_by_user = _active_generation_user_counts()
        queued_owner_ids = [_generation_owner_id(candidate) for candidate in queued_candidates]
        fair_candidates = []
        for candidate in queued_candidates:
            if not _queue_is_fair_candidate(candidate, active_by_user, queued_owner_ids):
                continue
            fair_candidates.append(candidate)
        queued = _claim_next_generation_for_submission(fair_candidates)

    if queued is not None:
        try:
            queued = _seal_generation_submission(queued.id)
        except (TypeError, ValueError, PromptVersion.DoesNotExist) as exc:
            if _generation_was_canceled(queued):
                return 1
            queued.status = Generation.Status.FAILED
            queued.failure_reason = f"submission readiness: {_sanitize_provider_text(str(exc))}"
            queued.save(update_fields=["status", "failure_reason", "updated_at"])
            queued.batch.recompute_status()
            return 1
        if _generation_was_canceled(queued):
            return 1
        try:
            if isinstance(client, APIMartClient):
                task_id = _submit_generation_under_lock(
                    queued.id,
                    queued.provider_payload["submission"]["fingerprint"],
                    client,
                    [],
                    image_urls=_uploaded_reference_urls(client, storage, queued.reference_snapshot),
                )
            else:
                with storage.reference_paths(queued.reference_snapshot) as image_paths:
                    task_id = _submit_generation_under_lock(
                        queued.id,
                        queued.provider_payload["submission"]["fingerprint"],
                        client,
                        image_paths,
                    )
        except SubmitUnknown as exc:
            if _generation_was_canceled(queued):
                return 1
            queued.status = Generation.Status.SUBMIT_UNKNOWN
            queued.failure_reason = str(exc)
            queued.save(update_fields=["status", "failure_reason", "updated_at"])
            queued.batch.recompute_status()
            return 1
        except Exception as exc:
            if _generation_was_canceled(queued):
                return 1
            queued.status = Generation.Status.FAILED
            queued.failure_reason = str(exc)
            queued.save(update_fields=["status", "failure_reason", "updated_at"])
            queued.batch.recompute_status()
            return 1
        if _generation_was_canceled(queued):
            return 1
        queued.provider_task_id = task_id
        queued.status = Generation.Status.SUBMITTED
        queued.submitted_at = timezone.now()
        queued.save(update_fields=["provider_task_id", "status", "submitted_at", "updated_at"])
        queued.batch.recompute_status()
        return 1

    active_candidates = (
        Generation.objects.select_related("batch", "cluster", "output_slot")
        .filter(status__in=[Generation.Status.SUBMITTED, Generation.Status.PROCESSING])
        .order_by("submitted_at", "created_at", "id")
    )
    active = _claim_next_generation_for_polling(active_candidates)
    if active is None:
        return 0

    payload = client.get_task(active.provider_task_id)
    provider_status = _normalize_provider_status(payload)
    if _generation_was_canceled(active):
        return 1
    if provider_status == Generation.Status.PROCESSING:
        active.status = Generation.Status.PROCESSING
        active.provider_payload = _provider_payload_with(
            active,
            status=payload.get("status"),
        )
        active.save(update_fields=["status", "provider_payload", "updated_at"])
        return 1
    if provider_status == Generation.Status.FAILED:
        active.status = Generation.Status.FAILED
        active.failure_reason = payload.get("error") or payload.get("message") or "provider failed"
        active.provider_payload = _provider_payload_with(
            active,
            **{
                key: payload.get(key)
                for key in ("status", "code", "error_code", "error", "message")
                if payload.get(key) is not None
            },
        )
        active.save(update_fields=["status", "failure_reason", "provider_payload", "updated_at"])
        try:
            _create_simplified_failure_retry(active, client)
        except Exception:
            pass
        active.batch.recompute_status()
        return 1

    active.status = Generation.Status.ARCHIVING
    active.provider_payload = _provider_payload_with(
        active,
        status=payload.get("status"),
    )
    active.save(update_fields=["status", "provider_payload", "updated_at"])
    urls = _image_urls(payload)
    if not urls:
        if _generation_was_canceled(active):
            return 1
        active.status = Generation.Status.FAILED
        active.failure_reason = "provider completed without image URL"
        active.save(update_fields=["status", "failure_reason", "updated_at"])
        active.batch.recompute_status()
        return 1
    for url in urls:
        storage.archive_result(active, url, client.download_image(url))
    if _generation_was_canceled(active):
        return 1
    active.status = Generation.Status.COMPLETED
    active.completed_at = timezone.now()
    active.save(update_fields=["status", "completed_at", "updated_at"])
    active.batch.recompute_status()
    hero_slot = standard_product_hero_slot(active.output_slot.template)
    if hero_slot is not None and active.output_slot_id == hero_slot.id:
        ensure_cluster_generations(active.cluster, active.created_by)
    return 1


def optimize_cluster_prompt(cluster, client=None):
    if cluster.archived_at is not None:
        raise ValueError("Product is archived")
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
    locked = Cluster.objects.select_for_update().select_related("batch").get(id=cluster.id)
    _ensure_cluster_mutable(locked)
    if payload.get("expected_version") != locked.version:
        raise ValueError("Cluster changed; refresh before saving")
    asset_order = payload.get("asset_order")
    ordered_relations = list(locked.cluster_assets.select_for_update().order_by("order", "id"))
    assets_changed = False
    if asset_order is not None:
        if not isinstance(asset_order, list) or any(not isinstance(asset_id, str) for asset_id in asset_order):
            raise TypeError("asset_order must be an array of strings")
        current_ids = [str(relation.asset_id) for relation in ordered_relations]
        if len(asset_order) != len(set(asset_order)) or set(asset_order) != set(current_ids):
            raise ValueError("asset_order must list each current cluster asset exactly once")
        if asset_order != current_ids:
            relations_by_id = {str(relation.asset_id): relation for relation in ordered_relations}
            for relation in ordered_relations:
                relation.order += 100
            ClusterAsset.objects.bulk_update(ordered_relations, ["order"])
            ordered_relations = [relations_by_id[asset_id] for asset_id in asset_order]
            for index, relation in enumerate(ordered_relations, start=1):
                relation.order = index
                relation.role = ClusterAsset.Role.PRIMARY if index == 1 else ClusterAsset.Role.REFERENCE
            ClusterAsset.objects.bulk_update(ordered_relations, ["order", "role"])
            assets_changed = True
    prompts = payload.get("prompts", [])
    if not isinstance(prompts, list):
        raise TypeError("prompts must be an array")
    slots = {}
    if prompts:
        template, _, _ = _effective_cluster_resources(locked.batch, locked)
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
        prepared_prompts.append(
            _manual_prompt_payload(locked, locked.batch, slot, prompt)
        )

    configuration_fields = {"platform_override", "market_override", "seller_tier_override"}
    effective_before = (
        _configuration_signature(locked.batch, locked)
        if configuration_fields.intersection(payload)
        else None
    )
    content_changed = False
    if "name" in payload or "product_name" in payload:
        product_name = str(payload.get("product_name", payload.get("name")) or "").strip()
        if len(product_name) > 200:
            raise ValueError("product name cannot exceed 200 characters")
        if locked.product_name != product_name or (product_name and locked.name != product_name):
            locked.product_name = product_name
            if product_name:
                locked.name = product_name
            analysis = copy.deepcopy(locked.analysis_snapshot)
            analysis["product_name_source"] = "manual"
            locked.analysis_snapshot = analysis
            content_changed = True
    if "relation_type" in payload:
        relation_type = str(payload["relation_type"] or "")
        if relation_type not in Cluster.RelationType.values:
            raise ValueError("invalid relation_type")
        if locked.relation_type != relation_type:
            locked.relation_type = relation_type
            content_changed = True
    for field in ("product_facts", "identity_lock", "prompt_override"):
        if field in payload:
            value = str(payload[field] or "")
            if getattr(locked, field) != value:
                setattr(locked, field, value)
                content_changed = True
    for field, uppercase in (("platform_override", False), ("market_override", True)):
        if field not in payload:
            continue
        value = payload[field]
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{field} must be a string or null")
        value = value.strip() if value is not None else None
        if value == "":
            raise ValueError(f"{field} cannot be empty")
        if uppercase and value is not None:
            value = value.upper()
        if getattr(locked, field) != value:
            setattr(locked, field, value)
    if "seller_tier_override" in payload:
        value = payload["seller_tier_override"]
        if value is not None and not isinstance(value, str):
            raise TypeError("seller_tier_override must be a string or null")
        value = value.strip().lower() if value is not None else None
        if value is not None and value not in Batch.SellerTier.values:
            raise ValueError("seller_tier_override must be general or mall")
        if locked.seller_tier_override != value:
            locked.seller_tier_override = value
    configuration_changed = (
        effective_before is not None
        and effective_before != _configuration_signature(locked.batch, locked)
    )
    if prepared_prompts and (configuration_changed or content_changed or assets_changed):
        raise ValueError("prepare the updated product before editing slot prompts")
    if configuration_changed or assets_changed or (content_changed and not prepared_prompts):
        _invalidate_preparation(locked)
    if configuration_changed or assets_changed or content_changed:
        locked.version += 1
        locked.save(
            update_fields=[
                "name",
                "product_name",
                "product_facts",
                "identity_lock",
                "prompt_override",
                "platform_override",
                "market_override",
                "seller_tier_override",
                "relation_type",
                "preparation_status",
                "preparation_error",
                "preparation_stage",
                "preparation_current",
                "preparation_total",
                "analysis_snapshot",
                "auto_generate",
                "version",
                "updated_at",
            ]
        )
    created_manual_prompts = []
    for item in prepared_prompts:
        _create_gated_prompt_version(
            cluster=locked,
            batch=locked.batch,
            slot=item["slot"],
            user=user,
            node_name=item["node_name"],
            template_version=item["template_version"],
            provider_model=item["provider_model"],
            prompt_text=item["prompt"],
            input_snapshot=item["input_snapshot"],
            structured_output=item["structured_output"],
            source_snapshot=item["source_snapshot"],
            references=item["references"],
            parent_prompt_version=item["parent_prompt"],
            fact_policy="human-reviewed",
        )
        created_manual_prompts.append(item)
    if created_manual_prompts and _all_generation_prompts_ready(locked, locked.batch, template):
        locked.preparation_status = Cluster.PreparationStatus.READY
        locked.preparation_error = ""
        locked.preparation_stage = "N7"
        locked.preparation_current = locked.preparation_total or 7
        locked.save(
            update_fields=[
                "preparation_status",
                "preparation_error",
                "preparation_stage",
                "preparation_current",
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
    Generation.Status.SUBMIT_UNKNOWN,
}
PAUSABLE_PREPARATION_STATUSES = {
    Cluster.PreparationStatus.PENDING,
    Cluster.PreparationStatus.PREPARING,
}
PAUSABLE_GENERATION_STATUSES = {
    Generation.Status.QUEUED,
    Generation.Status.PREPARING,
    Generation.Status.SUBMITTING,
    Generation.Status.SUBMITTED,
    Generation.Status.PROCESSING,
    Generation.Status.ARCHIVING,
}
FOLLOWUP_SOURCE_STATUSES = {
    Generation.Status.COMPLETED,
    Generation.Status.FAILED,
    Generation.Status.CANCELED,
}


@transaction.atomic
def pause_project_work(batch, *, cluster_ids=None, generation_ids=None):
    cluster_ids = [str(value) for value in (cluster_ids or [])]
    generation_ids = [str(value) for value in (generation_ids or [])]
    if not cluster_ids and not generation_ids:
        raise ValueError("cluster_ids or generation_ids is required")

    locked_batch = Batch.objects.select_for_update().get(id=batch.id)
    unique_cluster_ids = list(dict.fromkeys(cluster_ids))
    unique_generation_ids = list(dict.fromkeys(generation_ids))
    clusters = list(
        Cluster.objects.select_for_update().filter(
            batch_id=locked_batch.id,
            id__in=unique_cluster_ids,
            archived_at__isnull=True,
        )
    )
    clusters_by_id = {str(cluster.id): cluster for cluster in clusters}
    cluster_items = []
    for cluster_id in unique_cluster_ids:
        cluster = clusters_by_id.get(cluster_id)
        if cluster is None:
            cluster_items.append({"cluster_id": cluster_id, "status": "not_found"})
            continue
        if cluster.preparation_status in PAUSABLE_PREPARATION_STATUSES:
            snapshot = copy.deepcopy(cluster.analysis_snapshot) if isinstance(cluster.analysis_snapshot, dict) else {}
            snapshot["_preparation_revision"] = _preparation_revision(snapshot) + 1
            cluster.analysis_snapshot = snapshot
            cluster.preparation_status = Cluster.PreparationStatus.DRAFT
            cluster.preparation_error = ""
            cluster.preparation_stage = "draft"
            cluster.preparation_current = 0
            cluster.preparation_total = 7
            cluster.auto_generate = False
            cluster.save(
                update_fields=[
                    "analysis_snapshot",
                    "preparation_status",
                    "preparation_error",
                    "preparation_stage",
                    "preparation_current",
                    "preparation_total",
                    "auto_generate",
                    "updated_at",
                ]
            )
            cluster_items.append({"cluster_id": cluster_id, "status": "paused"})
        else:
            cluster_items.append({"cluster_id": cluster_id, "status": "idle"})

    generation_filter = Q()
    if unique_cluster_ids:
        generation_filter |= Q(cluster_id__in=unique_cluster_ids)
    if unique_generation_ids:
        generation_filter |= Q(id__in=unique_generation_ids)
    generations = list(
        Generation.objects.select_for_update().filter(
            generation_filter,
            batch_id=locked_batch.id,
            cluster__archived_at__isnull=True,
            status__in=PAUSABLE_GENERATION_STATUSES,
        )
    )
    generation_items = []
    for generation in generations:
        generation.status = Generation.Status.CANCELED
        generation.failure_reason = "Paused by operator"
        generation.save(update_fields=["status", "failure_reason", "updated_at"])
        generation_items.append({"generation_id": str(generation.id), "status": generation.status})

    if generation_items:
        locked_batch.recompute_status()
    return {"clusters": cluster_items, "generations": generation_items}


@transaction.atomic
def _create_followup_attempt(source, user, *, prompt_builder=None, **overrides):
    batch = Batch.objects.select_for_update().select_related(
        "output_template",
        "rule_profile",
    ).get(id=source.batch_id)
    cluster = Cluster.objects.select_for_update().get(
        id=source.cluster_id,
        batch_id=batch.id,
    )
    locked_source = Generation.objects.select_for_update().select_related(
        "prompt_version",
        "output_slot__template",
    ).get(
        id=source.id,
        batch_id=batch.id,
        cluster_id=cluster.id,
    )
    if cluster.archived_at is not None:
        raise ValueError("Product is archived")
    if locked_source.status not in FOLLOWUP_SOURCE_STATUSES:
        raise ValueError("A follow-up requires a terminal source generation status")
    try:
        _validate_generation_readiness(locked_source, cluster, batch)
    except ValueError as exc:
        raise ValueError(f"Generation submission readiness: {exc}") from exc
    siblings = Generation.objects.select_for_update().filter(
        cluster_id=cluster.id,
        output_slot_id=locked_source.output_slot_id,
    ).exclude(id=locked_source.id)
    if siblings.filter(attempt__gt=locked_source.attempt).exists() or siblings.filter(
        status__in=ACTIVE_GENERATION_STATUSES
    ).exists():
        raise ValueError("A newer generation attempt already exists")
    next_attempt = (
        Generation.objects.filter(
            cluster_id=cluster.id,
            output_slot_id=locked_source.output_slot_id,
        ).aggregate(value=Max("attempt"))["value"]
        or 0
    ) + 1
    if prompt_builder is not None:
        overrides.update(prompt_builder(locked_source, cluster, batch))
    chosen_prompt = overrides.get("prompt_version", locked_source.prompt_version)
    chosen_text = overrides.get("prompt_text", locked_source.prompt_text)
    chosen_references = copy.deepcopy(
        overrides.get("reference_snapshot", locked_source.reference_snapshot)
    )
    try:
        _validate_prompt_version_readiness(
            chosen_prompt,
            cluster,
            batch,
            locked_source.output_slot,
            references=chosen_references,
            allowed_nodes=(
                {"N4", "N8", "N9"}
                if is_standard_product_hero_slot(locked_source.output_slot)
                else {"N6", "N8", "N9"}
            ),
        )
    except ValueError as exc:
        raise ValueError(f"Generation submission readiness: {exc}") from exc
    if chosen_text != chosen_prompt.prompt_text:
        raise ValueError("Generation submission readiness prompt snapshot is inconsistent")
    reserve_generation_usage(user, 1)
    values = {
        "batch": batch,
        "cluster": cluster,
        "output_slot": locked_source.output_slot,
        "prompt_version": locked_source.prompt_version,
        "created_by": user,
        "attempt": next_attempt,
        "status": Generation.Status.QUEUED,
        "prompt_text": locked_source.prompt_text,
        "size": locked_source.size,
        "resolution": locked_source.resolution,
        "reference_snapshot": copy.deepcopy(locked_source.reference_snapshot),
        "template_snapshot": copy.deepcopy(locked_source.template_snapshot),
        "rule_snapshot": copy.deepcopy(locked_source.rule_snapshot),
    }
    values.update(overrides)
    return Generation.objects.create(**values)


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
    cluster = Cluster.objects.select_for_update().get(id=generation.cluster_id)
    if cluster.archived_at is not None:
        raise ValueError("Product is archived")
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
            prior_prompt = previous.prompt_text
        else:
            input_snapshot = {
                "product_facts": locked.cluster.product_facts,
                "identity_lock": locked.cluster.identity_lock,
                "reference_snapshot": copy.deepcopy(locked.reference_snapshot),
            }
            source_snapshot = copy.deepcopy(input_snapshot)
            prior_prompt = locked.prompt_text
        references = copy.deepcopy(locked.reference_snapshot)
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
        structured_output["modification_director"] = director_output
        structured_output["node_snapshot"] = _node_snapshot(
            "N8",
            settings.APIMART_PROMPT_MODEL,
            director_input,
            director_output,
            slot_id=locked.output_slot_id,
        )
        structured_output["prompt"] = prompt_text
        prompt_version = _create_gated_prompt_version(
            cluster=cluster,
            batch=locked.batch,
            slot=locked.output_slot,
            user=reviewer,
            node_name="N8",
            template_version=_prompt_node_contract("N8")[1],
            provider_model=previous.provider_model if previous else "gpt-image-2",
            prompt_text=prompt_text,
            input_snapshot=input_snapshot,
            structured_output=structured_output,
            source_snapshot=source_snapshot,
            references=references,
            parent_prompt_version=previous,
            size=locked.size,
            resolution=locked.resolution,
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
        return "本张出图状态不确定，请稍后刷新后重试。"
    if generation.status in {Generation.Status.FAILED, Generation.Status.CANCELED}:
        return "本张出图未成功，可直接重试。"
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


def _prompt_slot_metadata(prompt):
    if prompt is None or not isinstance(prompt.structured_output, dict):
        return {}
    structured = prompt.structured_output
    node_output = structured.get("node_output") if isinstance(structured.get("node_output"), dict) else {}
    plan = structured.get("marketing_plan") if isinstance(structured.get("marketing_plan"), dict) else {}
    localized_copy = (
        node_output.get("localized_copy")
        if isinstance(node_output.get("localized_copy"), dict)
        else structured.get("localized_copy")
        if isinstance(structured.get("localized_copy"), dict)
        else {}
    )
    creative_strategy = plan.get("creative_strategy") if isinstance(plan.get("creative_strategy"), dict) else {}
    metadata = {}
    display_prompt = node_output.get("display_prompt") or structured.get("display_prompt")
    if display_prompt:
        metadata["displayPrompt"] = str(display_prompt)
    if plan.get("decision_task"):
        metadata["decisionTask"] = str(plan["decision_task"])
    if plan.get("conversion_goal"):
        metadata["conversionGoal"] = str(plan["conversion_goal"])
    if creative_strategy:
        metadata["creativeStrategy"] = {
            key: value
            for key, value in {
                "mode": creative_strategy.get("mode"),
                "userJob": creative_strategy.get("user_job"),
                "consumerBenefit": creative_strategy.get("consumer_benefit"),
                "mentalSimulation": creative_strategy.get("mental_simulation"),
                "emotionalShift": creative_strategy.get("emotional_shift"),
                "productVoice": creative_strategy.get("product_voice"),
                "identitySignal": creative_strategy.get("identity_signal"),
            }.items()
            if value
        }
    if localized_copy:
        metadata["localizedCopy"] = {
            key: value
            for key, value in {
                "language": localized_copy.get("language"),
                "lines": _string_list(localized_copy.get("lines")),
                "backTranslation": localized_copy.get("back_translation"),
                "strategyMode": localized_copy.get("strategy_mode"),
            }.items()
            if value
        }
    return metadata


def serialize_project(batch):
    assets = list(batch.assets.filter(archived_at__isnull=True).order_by("created_at", "id"))
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
    for cluster in batch.clusters.filter(archived_at__isnull=True).order_by("created_at", "id"):
        cluster_template, _, _ = _effective_cluster_resources(batch, cluster)
        cluster_assets = list(
            cluster.cluster_assets.select_related("asset")
            .filter(asset__archived_at__isnull=True)
            .order_by("order", "id")
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
        prompt_slots = []
        for slot in cluster_template.slots.order_by("order", "id"):
            latest_prompt = latest_prompts.get(slot.id)
            prompt_slots.append({
                "slotOrder": slot.order,
                "slot": slot.name,
                "text": latest_prompt.prompt_text if latest_prompt else "",
                "promptVersionId": str(latest_prompt.id) if latest_prompt else None,
                "readOnly": is_source_product_photo_slot(slot),
                **_prompt_slot_metadata(latest_prompt),
            })
        analysis = cluster.analysis_snapshot if isinstance(cluster.analysis_snapshot, dict) else {}
        public_product_name = "" if cluster.product_name == "名称待确认" or _is_schema_placeholder(cluster.product_name) else cluster.product_name
        public_identity = _strip_schema_placeholders(analysis.get("identity") or {})
        public_analysis = copy.deepcopy(analysis)
        if isinstance(public_analysis, dict):
            public_analysis["identity"] = public_identity
        effective_config = _effective_config(batch, cluster)
        effective_config["resolution"] = effective_config["resolution"].upper()
        sku_assets = [serialized_assets[item.asset_id] for item in cluster_assets]
        skus.append(
            {
                "id": str(cluster.id),
                "sku": cluster.sku or "",
                "name": public_product_name,
                "productName": public_product_name,
                "productNameSource": analysis.get("product_name_source"),
                "version": cluster.version,
                "relationType": cluster.relation_type,
                "preparationStatus": cluster.preparation_status,
                "preparation": {
                    "status": cluster.preparation_status,
                    "stage": cluster.preparation_stage,
                    "current": cluster.preparation_current,
                    "total": cluster.preparation_total,
                    "error": cluster.preparation_error,
                },
                "importStatus": (
                    latest_imports[cluster.sku].status if cluster.sku in latest_imports else "manual"
                ),
                "assetIds": [str(item.asset_id) for item in cluster_assets],
                "assets": sku_assets,
                "facts": cluster.product_facts,
                "productFacts": cluster.product_facts,
                "identityLock": cluster.identity_lock,
                "brief": cluster.product_facts,
                "productStyle": cluster.prompt_override,
                "overrides": {
                    "platform": cluster.platform_override,
                    "market": cluster.market_override,
                    "sellerTier": cluster.seller_tier_override,
                },
                "effectiveConfig": effective_config,
                "identity": public_identity,
                "factLedger": analysis.get("fact_ledger", {}),
                "marketingPlan": analysis.get("marketing_plan", {"plans": []}),
                "analysisSnapshot": public_analysis,
                "prompts": prompt_slots,
                "promptSlots": prompt_slots,
                "generationProgress": {
                    "completed": sum(item["status"] == "completed" for item in outputs),
                    "active": sum(item["status"] in {"queued", "running"} for item in outputs),
                    "failed": sum(item["status"] == "failed" for item in outputs),
                    "total": len(outputs),
                },
                "outputs": outputs,
            }
        )
    preflight = preflight_batch(batch, batch.owner, template)
    default_config = _default_config(batch)
    default_config["resolution"] = default_config["resolution"].upper()
    return {
        "id": str(batch.id),
        "name": batch.name,
        "platform": batch.platform,
        "market": batch.market or batch.site,
        "sellerTier": batch.seller_tier,
        "configurationStatus": "configured" if batch.platform and (batch.market or batch.site) else "required",
        "defaultConfig": default_config,
        "template": template.name,
        "size": batch.size,
        "resolution": (batch.resolution or "1k").upper(),
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


def serialize_project_progress(batch):
    skus = []
    for cluster in batch.clusters.filter(archived_at__isnull=True).order_by("created_at", "id"):
        cluster_template, _, _ = _effective_cluster_resources(batch, cluster)
        latest_prompts = {}
        for prompt in cluster.prompt_versions.select_related("output_slot").order_by(
            "output_slot__order", "-created_at", "-id"
        ):
            if prompt.output_slot_id:
                latest_prompts.setdefault(prompt.output_slot_id, prompt)
        prompt_slots = []
        for slot in cluster_template.slots.order_by("order", "id"):
            latest_prompt = latest_prompts.get(slot.id)
            prompt_slots.append({
                "slotOrder": slot.order,
                "slot": slot.name,
                "text": latest_prompt.prompt_text if latest_prompt else "",
                "promptVersionId": str(latest_prompt.id) if latest_prompt else None,
                "readOnly": is_source_product_photo_slot(slot),
                **_prompt_slot_metadata(latest_prompt),
            })
        outputs = []
        for generation in cluster.generations.select_related("output_slot", "prompt_version").prefetch_related(
            "result_assets"
        ).order_by("output_slot__order", "attempt", "id"):
            result = next(iter(generation.result_assets.all()), None)
            output = {
                "id": str(generation.id),
                "name": generation.output_slot.name,
                "slot": generation.output_slot.name,
                "slotId": str(generation.output_slot_id),
                "slotOrder": generation.output_slot.order,
                "attempt": generation.attempt,
                "version": generation.attempt,
                "status": _generation_status(generation.status),
                "reviewStatus": (
                    Generation.ReviewStatus.CHANGES_REQUESTED
                    if generation.review_status == Generation.ReviewStatus.REJECTED
                    else generation.review_status
                ),
                "prompt": generation.prompt_text,
                "promptVersionId": str(generation.prompt_version_id) if generation.prompt_version_id else None,
            }
            failure_message = generation_failure_message(generation)
            if failure_message:
                output["failureReason"] = failure_message
            if result is not None:
                output["imageUrl"] = reverse("api_result_media", args=[result.id])
            outputs.append(output)
        skus.append({
            "id": str(cluster.id),
            "preparationStatus": cluster.preparation_status,
            "preparation": {
                "status": cluster.preparation_status,
                "stage": cluster.preparation_stage,
                "current": cluster.preparation_current,
                "total": cluster.preparation_total,
                "error": cluster.preparation_error,
            },
            "prompts": prompt_slots,
            "generationProgress": {
                "completed": sum(item["status"] == "completed" for item in outputs),
                "active": sum(item["status"] in {"queued", "running"} for item in outputs),
                "failed": sum(item["status"] == "failed" for item in outputs),
                "total": len(outputs),
            },
            "outputs": outputs,
        })
    return {
        "id": str(batch.id),
        "status": _project_status(batch.status),
        "updatedAt": batch.updated_at.isoformat(),
        "skus": skus,
    }
