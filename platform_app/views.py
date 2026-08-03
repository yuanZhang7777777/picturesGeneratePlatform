import csv
import json
import mimetypes
import re
import uuid
import zipfile
from io import BytesIO
from io import StringIO
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.middleware.csrf import get_token
from django.db import connection, transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .forms import ERPAuthenticationForm, FirstPasswordChangeForm
from .models import Asset, AuditEvent, Batch, Cluster, Generation, PromptNodeTemplate, ResultAsset
from .services import (
    CatalogAuthExpired,
    archive_or_delete_cluster,
    cluster_preparation_is_current,
    create_project,
    ensure_cluster_generations,
    generation_failure_message,
    LocalStorage,
    import_skus,
    merge_asset_into_cluster,
    move_asset_to_new_cluster,
    review_generation,
    preflight_batch,
    record_cluster_auto_generate,
    register_uploaded_asset,
    request_cluster_preparation,
    regenerate_generation,
    remove_asset_from_cluster,
    optimize_cluster_prompt,
    pause_project_work,
    request_generation_revision,
    safe_storage_path,
    serialize_project,
    StorageError,
    UploadError,
    update_cluster_content,
    update_project_settings,
)

MAX_EXPORT_RESULT_BYTES = 25 * 1024 * 1024
MAX_EXPORT_TOTAL_BYTES = 500 * 1024 * 1024


def require_owner_or_admin(user, obj):
    owner_id = getattr(obj, "owner_id", None)
    if owner_id is None and hasattr(obj, "batch"):
        owner_id = obj.batch.owner_id
    if user.is_platform_admin or owner_id == user.id:
        return None
    raise Http404()


def password_change_required(view_func):
    def wrapped(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.must_change_password:
            return redirect("password_change")
        return view_func(request, *args, **kwargs)

    return wrapped


class PlatformLoginView(LoginView):
    template_name = "platform_app/login.html"
    authentication_form = ERPAuthenticationForm

    def form_valid(self, form):
        self.request.session["erp_access_token"] = form.erp_token
        return super().form_valid(form)


@login_required
@require_http_methods(["GET", "POST"])
def password_change(request):
    if request.method == "POST":
        form = FirstPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            form.save()
            request.user.must_change_password = False
            request.user.save(update_fields=["must_change_password"])
            update_session_auth_hash(request, request.user)
            return redirect("/")
    else:
        form = FirstPasswordChangeForm(request.user)
    return render(request, "platform_app/password_change.html", {"form": form})


@login_required
@password_change_required
def legacy_batch_list_redirect(request):
    return redirect("/")


@login_required
@password_change_required
@require_http_methods(["GET"])
def legacy_batch_new_redirect(request):
    return redirect("/projects/new")


@login_required
@password_change_required
def legacy_batch_detail_redirect(request, batch_id):
    batch = get_object_or_404(Batch.objects.select_related("owner"), id=batch_id)
    require_owner_or_admin(request.user, batch)
    return redirect(f"/projects/{batch.id}")


def health_live(request):
    return HttpResponse("ok", content_type="text/plain")


def health_ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        return JsonResponse({"database": "error", "error": str(exc)}, status=503)
    return JsonResponse({"database": "ok"})


def _batch_for_user(user, batch_id):
    batch = get_object_or_404(
        Batch.objects.select_related("owner", "output_template", "rule_profile"),
        id=batch_id,
    )
    require_owner_or_admin(user, batch)
    return batch


def _generation_for_user(user, generation_id):
    generation = get_object_or_404(
        Generation.objects.select_related("batch", "cluster", "output_slot"),
        id=generation_id,
    )
    require_owner_or_admin(user, generation.batch)
    return generation


def _serialize_project(batch):
    payload = serialize_project(batch)
    versions = {
        str(cluster_id): version
        for cluster_id, version in batch.clusters.filter(archived_at__isnull=True).values_list("id", "version")
    }
    for sku in payload["skus"]:
        sku["version"] = versions[sku["id"]]
    return payload


def _relative_upload_path(value):
    if not value or "\\" in value:
        raise ValueError("relative_paths must be non-empty POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.name:
        raise ValueError("relative_paths must be safe relative POSIX paths")
    return value


@login_required
@password_change_required
@require_http_methods(["GET"])
def api_csrf(request):
    return JsonResponse({"csrf_token": get_token(request)})


@login_required
@password_change_required
@require_http_methods(["GET"])
def api_current_user(request):
    return JsonResponse({"role": "admin" if request.user.is_platform_admin else "operator"})


@login_required
@password_change_required
@require_POST
def api_project_create(request):
    try:
        payload = json.loads(request.body or "{}")
        if not isinstance(payload, dict):
            raise TypeError("request body must be an object")
        batch = create_project(
            request.user,
            name=payload.get("name"),
        )
    except (ValueError, TypeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(_serialize_project(batch), status=201)


@login_required
@password_change_required
@require_http_methods(["GET"])
def api_workspace_snapshot(request):
    queryset = Batch.objects.select_related("output_template").order_by("-updated_at", "-id")
    if not request.user.is_platform_admin:
        queryset = queryset.filter(owner=request.user)
    return JsonResponse(
        {
            "currentUser": {
                "role": "admin" if request.user.is_platform_admin else "operator"
            },
            "projects": [_serialize_project(batch) for batch in queryset],
        }
    )


@login_required
@password_change_required
@require_http_methods(["GET"])
def api_project_snapshot(request, batch_id):
    return JsonResponse(_serialize_project(_batch_for_user(request.user, batch_id)))


@login_required
@password_change_required
@require_http_methods(["PATCH"])
def api_project_settings(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    try:
        payload = json.loads(request.body or "{}")
        batch = update_project_settings(batch, payload)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(_serialize_project(batch))


@login_required
@password_change_required
@require_POST
def api_project_prepare(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    try:
        payload = json.loads(request.body or "{}")
        cluster_ids = payload.get("cluster_ids") if isinstance(payload, dict) else None
        if not isinstance(cluster_ids, list) or not cluster_ids:
            raise ValueError("cluster_ids must be a non-empty array")
        if any(not isinstance(cluster_id, str) for cluster_id in cluster_ids):
            raise ValueError("cluster_ids must contain strings")
    except (ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    clusters = {
        str(cluster.id): cluster
        for cluster in batch.clusters.filter(
            id__in=cluster_ids,
            archived_at__isnull=True,
        )
    }
    items = []
    for cluster_id in dict.fromkeys(cluster_ids):
        cluster = clusters.get(cluster_id)
        if cluster is None:
            items.append(
                {
                    "cluster_id": cluster_id,
                    "status": "blocked",
                    "stage": "blocked",
                    "code": "cluster_not_found",
                }
            )
        elif cluster_preparation_is_current(cluster):
            items.append(
                {
                    "cluster_id": cluster_id,
                    "status": "already_ready",
                    "stage": "ready",
                }
            )
        else:
            if cluster.preparation_status != Cluster.PreparationStatus.PREPARING:
                cluster = request_cluster_preparation(cluster, auto_generate=False)
            items.append(
                {
                    "cluster_id": cluster_id,
                    "status": "queued",
                    "stage": cluster.preparation_stage,
                }
            )
    return JsonResponse({"items": items})


@login_required
@password_change_required
@require_POST
def api_project_pause(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    try:
        payload = json.loads(request.body or "{}")
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        cluster_ids = payload.get("cluster_ids") or []
        generation_ids = payload.get("generation_ids") or []
        if not isinstance(cluster_ids, list) or not isinstance(generation_ids, list):
            raise ValueError("cluster_ids and generation_ids must be arrays")
        if any(not isinstance(cluster_id, str) for cluster_id in cluster_ids):
            raise ValueError("cluster_ids must contain strings")
        if any(not isinstance(generation_id, str) for generation_id in generation_ids):
            raise ValueError("generation_ids must contain strings")
        result = pause_project_work(
            batch,
            cluster_ids=cluster_ids,
            generation_ids=generation_ids,
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(result)


@login_required
@password_change_required
@require_POST
def api_upload_assets(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    uploads = request.FILES.getlist("files")
    relative_paths = request.POST.getlist("relative_paths")
    mode = request.POST.get("mode") or batch.last_import_mode
    entries = []
    rejected = []
    for index, uploaded in enumerate(uploads):
        raw_filename = relative_paths[index] if index < len(relative_paths) else uploaded.name
        try:
            filename = _relative_upload_path(raw_filename)
        except ValueError as exc:
            rejected.append(
                {
                    "filename": raw_filename or uploaded.name,
                    "code": "unsafe_path",
                    "message": str(exc),
                }
            )
            continue
        entries.append((uploaded, filename))

    entries.sort(key=lambda item: (Path(item[1]).suffix.lower() != ".txt", item[1].casefold()))
    image_count = 0
    txt_count = 0
    imported = []
    for uploaded, filename in entries:
        is_txt = Path(filename).suffix.lower() == ".txt"
        image_count += not is_txt
        txt_count += is_txt
        if image_count > 100 or txt_count > 20:
            rejected.append(
                {
                    "filename": filename,
                    "code": "too_many_files",
                    "message": "单次最多上传 100 张图片和 20 个 TXT",
                }
            )
            continue
        try:
            asset = register_uploaded_asset(batch, filename, uploaded.read(), uploaded.content_type, mode=mode)
        except UploadError as exc:
            rejected.append({"filename": filename, "code": exc.code, "message": str(exc)})
            continue
        except (OSError, StorageError):
            rejected.append(
                {
                    "filename": filename,
                    "code": "storage_unavailable",
                    "message": "素材存储暂时不可用，请稍后重试该文件",
                }
            )
            continue
        cluster = asset.clusters.first()
        imported.append(
            {
                "filename": filename,
                "asset_id": str(asset.id),
                "cluster_id": str(cluster.id) if cluster else None,
            }
        )
    return JsonResponse({"asset_count": len(imported), "imported": imported, "rejected": rejected})


@login_required
@password_change_required
@require_POST
def api_sku_import(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    try:
        payload = json.loads(request.body or "{}")
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        erp_token = request.session.get("erp_access_token")
        return JsonResponse(import_skus(batch, payload.get("skus"), erp_token=erp_token, mode=payload.get("mode")))
    except CatalogAuthExpired as exc:
        request.session.pop("erp_access_token", None)
        return JsonResponse({"error": str(exc)}, status=401)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@login_required
@password_change_required
@require_http_methods(["POST", "PATCH", "DELETE"])
def api_update_cluster(request, cluster_id):
    from .models import Cluster

    cluster = get_object_or_404(Cluster, id=cluster_id, archived_at__isnull=True)
    require_owner_or_admin(request.user, cluster.batch)
    if request.method == "DELETE":
        try:
            return JsonResponse({"status": archive_or_delete_cluster(cluster)})
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=409)
    try:
        payload = json.loads(request.body or "{}")
        if not isinstance(payload, dict):
            raise TypeError("request body must be an object")
        cluster = update_cluster_content(cluster, request.user, payload)
    except json.JSONDecodeError:
        return JsonResponse({"error": "request body must be valid JSON"}, status=400)
    except TypeError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except ValueError as exc:
        status = 409 if "changed; refresh" in str(exc) else 400
        return JsonResponse({"error": str(exc)}, status=status)
    return JsonResponse({"id": str(cluster.id), "version": cluster.version})


def _serialize_prompt_node(template):
    node_family, _, suffix = template.node_name.partition(".")
    return {
        "id": str(template.id),
        "node_name": template.node_name,
        "version": template.version,
        "status": template.status,
        "instruction": template.instruction,
        "user_message_template": template.user_message_template,
        "output_schema": template.output_schema,
        "model": (
            settings.APIMART_VISION_MODEL
            if node_family == "N1"
            else settings.APIMART_PROMPT_MODEL
        ),
        "platform_scope": suffix if suffix in {"generic", "shopee", "tiktok"} else "shared",
        "created_at": template.created_at.isoformat(),
        "updated_at": template.updated_at.isoformat(),
    }


@login_required
@password_change_required
@require_http_methods(["GET", "POST"])
def api_admin_prompt_nodes(request):
    if not request.user.is_platform_admin:
        return JsonResponse({"error": "platform administrator required"}, status=403)
    if request.method == "GET":
        templates = PromptNodeTemplate.objects.order_by("node_name", "-updated_at", "-id")
        return JsonResponse({"nodes": [_serialize_prompt_node(item) for item in templates]})
    try:
        payload = json.loads(request.body or "{}")
        if not isinstance(payload, dict):
            raise TypeError("request body must be an object")
        node_name = str(payload.get("node_name") or "").strip()
        version = str(payload.get("version") or "").strip()
        instruction = str(payload.get("instruction") or "").strip()
        user_message_template = payload.get("user_message_template", "")
        output_schema = payload.get("output_schema")
        if not node_name or not version or not instruction:
            raise ValueError("node_name, version, and instruction are required")
        if not isinstance(user_message_template, str):
            raise TypeError("user_message_template must be a string")
        if not isinstance(output_schema, dict):
            raise TypeError("output_schema must be an object")
        template = PromptNodeTemplate.objects.create(
            node_name=node_name,
            version=version,
            instruction=instruction,
            user_message_template=user_message_template,
            output_schema=output_schema,
            status=PromptNodeTemplate.Status.DRAFT,
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(_serialize_prompt_node(template), status=201)


@login_required
@password_change_required
@require_POST
@transaction.atomic
def api_admin_prompt_nodes_publish(request):
    if not request.user.is_platform_admin:
        return JsonResponse({"error": "platform administrator required"}, status=403)
    try:
        payload = json.loads(request.body or "{}")
        if not isinstance(payload, dict):
            raise TypeError("request body must be an object")
        node_name = str(payload.get("node_name") or "").strip()
        version = str(payload.get("version") or "").strip()
        template = PromptNodeTemplate.objects.select_for_update().get(
            node_name=node_name,
            version=version,
        )
        PromptNodeTemplate.objects.filter(
            node_name=node_name,
            status=PromptNodeTemplate.Status.PUBLISHED,
        ).exclude(id=template.id).update(status=PromptNodeTemplate.Status.RETIRED)
        template.status = PromptNodeTemplate.Status.PUBLISHED
        template.save(update_fields=["status", "updated_at"])
    except PromptNodeTemplate.DoesNotExist:
        return JsonResponse({"error": "prompt node version not found"}, status=404)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(_serialize_prompt_node(template))


@login_required
@password_change_required
@require_http_methods(["DELETE"])
def api_delete_asset(request, asset_id):
    asset = get_object_or_404(
        Asset.objects.select_related("batch"),
        id=asset_id,
        archived_at__isnull=True,
    )
    require_owner_or_admin(request.user, asset.batch)
    try:
        return JsonResponse({"status": remove_asset_from_cluster(asset)})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=409)


@login_required
@password_change_required
@require_POST
def api_optimize_prompt(request, cluster_id):
    from .models import Cluster

    cluster = get_object_or_404(
        Cluster.objects.select_related("batch"),
        id=cluster_id,
        archived_at__isnull=True,
    )
    require_owner_or_admin(request.user, cluster.batch)
    try:
        return JsonResponse(optimize_cluster_prompt(cluster))
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=502)


@login_required
@password_change_required
@require_POST
def api_merge_asset(request, cluster_id):
    from .models import Asset, Cluster

    cluster = get_object_or_404(Cluster, id=cluster_id, archived_at__isnull=True)
    require_owner_or_admin(request.user, cluster.batch)
    payload = json.loads(request.body or "{}")
    asset = get_object_or_404(
        Asset,
        id=payload.get("asset_id"),
        batch=cluster.batch,
        archived_at__isnull=True,
    )
    try:
        relation = merge_asset_into_cluster(asset, cluster, expected_version=payload.get("expected_version"))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({"cluster_id": str(relation.cluster_id), "asset_id": str(relation.asset_id)})


@login_required
@password_change_required
@require_POST
def api_split_asset(request, asset_id):
    from .models import Asset

    asset = get_object_or_404(Asset, id=asset_id, archived_at__isnull=True)
    require_owner_or_admin(request.user, asset.batch)
    cluster = move_asset_to_new_cluster(asset)
    return JsonResponse({"cluster_id": str(cluster.id), "asset_id": str(asset.id)})


@login_required
@password_change_required
@require_http_methods(["GET"])
def api_batch_snapshot(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    clusters = []
    for cluster in batch.clusters.filter(archived_at__isnull=True).prefetch_related(
        "cluster_assets__asset", "generations__output_slot"
    ):
        clusters.append(
            {
                "id": str(cluster.id),
                "name": cluster.name,
                "version": cluster.version,
                "prompt_override": cluster.prompt_override,
                "assets": [
                    {
                        "id": str(item.asset.id),
                        "filename": item.asset.original_filename,
                        "role": item.role,
                        "order": item.order,
                    }
                    for item in cluster.cluster_assets.select_related("asset")
                    .filter(asset__archived_at__isnull=True)
                    .order_by("order", "id")
                ],
                "generations": [
                    {
                        "id": str(generation.id),
                        "slot": generation.output_slot.name,
                        "attempt": generation.attempt,
                        "status": generation.status,
                        "review_status": generation.review_status,
                        "failure_reason": generation_failure_message(generation),
                        "result_count": generation.result_assets.count(),
                    }
                    for generation in cluster.generations.select_related("output_slot").order_by(
                        "output_slot__order", "attempt"
                    )
                ],
            }
        )
    return JsonResponse(
        {
            "batch": {
                "id": str(batch.id),
                "name": batch.name,
                "status": batch.status,
                "platform": batch.platform,
                "site": batch.site,
            },
            "clusters": clusters,
        }
    )


@login_required
@password_change_required
@require_POST
def api_preflight(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    preflight = preflight_batch(batch, request.user)
    return JsonResponse(
        {
            key: preflight[key]
            for key in (
                "cluster_count",
                "slot_count",
                "generation_count",
                "blocking_errors",
                "template",
                "rule_profile",
            )
        }
    )


@login_required
@password_change_required
@require_POST
def api_confirm_generation(request, batch_id):
    return api_project_generate(request, batch_id)


@login_required
@password_change_required
@require_POST
def api_project_generate(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    try:
        payload = json.loads(request.body or "{}")
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        cluster_ids = payload.get("cluster_ids") or []
        slot_orders = payload.get("slot_orders") or None
        if cluster_ids:
            clusters = batch.clusters.filter(id__in=cluster_ids, archived_at__isnull=True)
        else:
            clusters = batch.clusters.filter(archived_at__isnull=True)
        generation_count = 0
        items = []
        for cluster in clusters:
            try:
                if cluster.preparation_status in {
                    Cluster.PreparationStatus.PENDING,
                    Cluster.PreparationStatus.PREPARING,
                }:
                    record_cluster_auto_generate(cluster)
                    items.append(
                        {
                            "cluster_id": str(cluster.id),
                            "status": "preparing",
                            "code": "preparation_in_progress",
                            "message": "Product preparation will queue generation automatically.",
                        }
                    )
                    continue
                if cluster.preparation_status in {
                    Cluster.PreparationStatus.BLOCKED,
                    Cluster.PreparationStatus.FAILED,
                }:
                    request_cluster_preparation(cluster, auto_generate=True)
                    items.append(
                        {
                            "cluster_id": str(cluster.id),
                            "status": "preparing",
                            "code": "prompt_preparation_started",
                            "message": "Product preparation will queue generation automatically.",
                        }
                    )
                    continue
                if cluster.preparation_status != Cluster.PreparationStatus.READY or not cluster_preparation_is_current(cluster):
                    request_cluster_preparation(cluster, auto_generate=True)
                    items.append(
                        {
                            "cluster_id": str(cluster.id),
                            "status": "preparing",
                            "code": "prompt_preparation_started",
                            "message": "Product preparation will queue generation automatically.",
                        }
                    )
                    continue
                generations, created_ids = ensure_cluster_generations(
                    cluster,
                    request.user,
                    slot_orders=slot_orders,
                    include_created=True,
                )
                generation_count += len(created_ids)
                items.append({"cluster_id": str(cluster.id), "status": "queued"})
            except Exception as exc:
                items.append(
                    {
                        "cluster_id": str(cluster.id),
                        "status": "blocked",
                        "code": (
                            "prompt_not_ready"
                            if isinstance(exc, (ValueError, TypeError))
                            else "generation_request_failed"
                        ),
                        "message": str(exc),
                    }
                )
                continue
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(
        {"generation_count": generation_count, "items": items},
        status=202,
    )


@login_required
@password_change_required
@require_POST
def api_generation_retry(request, generation_id):
    generation = _generation_for_user(request.user, generation_id)
    try:
        retry = generation.retry_failed(request.user)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"id": str(retry.id), "attempt": retry.attempt, "status": retry.status})


@login_required
@password_change_required
@require_POST
def api_generation_regenerate(request, generation_id):
    generation = _generation_for_user(request.user, generation_id)
    try:
        regenerated = regenerate_generation(generation, request.user)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"id": str(regenerated.id), "attempt": regenerated.attempt, "status": regenerated.status})


@login_required
@password_change_required
@require_http_methods(["GET"])
def api_asset_media(request, asset_id):
    asset = get_object_or_404(Asset.objects.select_related("batch"), id=asset_id)
    require_owner_or_admin(request.user, asset.batch)
    try:
        storage_path = safe_storage_path(asset.storage_path, f"originals/{asset.batch_id}")
        data = LocalStorage().read(storage_path)
    except (ValueError, FileNotFoundError):
        raise Http404()
    response = FileResponse(BytesIO(data), content_type=asset.content_type)
    response["Cache-Control"] = "private, max-age=86400"
    response["ETag"] = f'"asset-{asset.id}-{asset.sha256}"'
    return response


@login_required
@password_change_required
@require_http_methods(["GET"])
def api_result_media(request, result_id):
    result = get_object_or_404(
        ResultAsset.objects.select_related(
            "generation__batch",
            "generation__cluster",
            "generation__output_slot",
        ),
        id=result_id,
    )
    generation = result.generation
    require_owner_or_admin(request.user, generation.batch)
    try:
        storage_path = safe_storage_path(
            result.storage_path,
            (
                f"results/{generation.batch_id}/{generation.cluster_id}/"
                f"{generation.output_slot_id}/{generation.attempt}"
            ),
        )
        data = LocalStorage().read(storage_path)
    except (ValueError, FileNotFoundError):
        raise Http404()
    content_type = mimetypes.guess_type(storage_path)[0] or "application/octet-stream"
    response = FileResponse(BytesIO(data), content_type=content_type)
    response["Cache-Control"] = "private, max-age=86400"
    response["ETag"] = f'"result-{result.id}-{result.created_at.timestamp():.0f}"'
    return response


@login_required
@password_change_required
@require_POST
def api_generation_review(request, generation_id):
    generation = _generation_for_user(request.user, generation_id)
    try:
        payload = json.loads(request.body or "{}")
        feedback, revision = review_generation(
            generation,
            request.user,
            decision=payload.get("decision"),
            issue_tags=payload.get("issue_tags", []),
            description=payload.get("description", ""),
            annotations=payload.get("annotations", []),
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(
        {
            "review": {
                "id": str(feedback.id),
                "decision": feedback.decision,
            },
            "generation": (
                {
                    "id": str(revision.id),
                    "attempt": revision.attempt,
                    "status": revision.status,
                    "review_status": revision.review_status,
                }
                if revision
                else {
                    "id": str(generation.id),
                    "attempt": generation.attempt,
                    "status": generation.status,
                    "review_status": Generation.ReviewStatus.ACCEPTED,
                }
            ),
        }
    )


@login_required
@password_change_required
@require_POST
def api_generation_revise(request, generation_id):
    generation = _generation_for_user(request.user, generation_id)
    try:
        payload = json.loads(request.body or "{}")
        revision = request_generation_revision(
            generation,
            request.user,
            issue_tags=payload.get("issue_tags", []),
            description=payload.get("description", ""),
            annotations=payload.get("annotations", []),
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(
        {
            "generation": {
                "id": str(revision.id),
                "attempt": revision.attempt,
                "status": revision.status,
                "review_status": revision.review_status,
            }
        }
    )


def _archive_name_part(value, fallback):
    value = str(value or fallback).strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or fallback


def _selected_export_generations(batch, generation_ids):
    queryset = batch.generations.filter(
        cluster__archived_at__isnull=True
    ).select_related("cluster", "output_slot").prefetch_related("result_assets")
    if generation_ids:
        requested = [uuid.UUID(str(value)) for value in generation_ids]
        return list(
            queryset.filter(
                id__in=requested,
                status=Generation.Status.COMPLETED,
            ).order_by("cluster__name", "output_slot__order", "-attempt")
        )
    latest = {}
    for generation in queryset.filter(
        status=Generation.Status.COMPLETED,
    ).order_by(
        "cluster_id", "output_slot_id", "-attempt", "-id"
    ):
        latest.setdefault((generation.cluster_id, generation.output_slot_id), generation)
    return list(latest.values())


@login_required
@password_change_required
@require_POST
def api_project_export(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    try:
        payload = json.loads(request.body or "{}")
        generation_ids = payload.get("generation_ids", [])
        if not isinstance(generation_ids, list):
            raise ValueError("generation_ids must be a list")
        generations = _selected_export_generations(batch, generation_ids)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    storage = LocalStorage()
    entries = []
    manifest = [["generation_id", "product", "sku", "slot_order", "slot_name", "attempt", "filename"]]
    total_size = 0
    root_name = _archive_name_part(f"{batch.name}_{timezone.localdate():%Y%m%d}", "project")
    seen_names = {}
    for generation in generations:
        results = list(generation.result_assets.all())
        for result in results:
            try:
                storage_path = safe_storage_path(
                    result.storage_path,
                    (
                        f"results/{generation.batch_id}/{generation.cluster_id}/"
                        f"{generation.output_slot_id}/{generation.attempt}"
                    ),
                )
            except (ValueError, FileNotFoundError):
                continue
            try:
                data = storage.read(storage_path)
            except FileNotFoundError:
                continue
            result_size = len(data)
            if result_size > MAX_EXPORT_RESULT_BYTES:
                return JsonResponse({"error": "A completed result is too large to export"}, status=400)
            total_size += result_size
            if total_size > MAX_EXPORT_TOTAL_BYTES:
                return JsonResponse({"error": "The requested export is too large"}, status=400)
            suffix = Path(storage_path).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                suffix = ".bin"
            product = _archive_name_part(generation.cluster.product_name or generation.cluster.name, "product")
            sku = _archive_name_part(generation.cluster.sku, "") if generation.cluster.sku else ""
            folder = f"{product}__{sku}" if sku else product
            slot_name = _archive_name_part(generation.output_slot.name, f"slot-{generation.output_slot.order}")
            archive_name = f"{root_name}/{folder}/{generation.output_slot.order:02d}_{slot_name}{suffix}"
            if archive_name in seen_names:
                seen_names[archive_name] += 1
                stem = archive_name[: -len(suffix)]
                archive_name = f"{stem}_{seen_names[archive_name]}{suffix}"
            else:
                seen_names[archive_name] = 1
            entries.append((data, archive_name))
            manifest.append(
                [
                    str(generation.id),
                    product,
                    sku,
                    str(generation.output_slot.order),
                    generation.output_slot.name,
                    str(generation.attempt),
                    archive_name,
                ]
            )
    if not entries:
        return JsonResponse({"error": "No completed images are available to export"}, status=400)

    export_data = BytesIO()
    with zipfile.ZipFile(export_data, "w", zipfile.ZIP_DEFLATED) as archive:
        for data, archive_name in entries:
            archive.writestr(archive_name, data)
        csv_buffer = StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerows(manifest)
        archive.writestr(f"{root_name}/导出清单.csv", "\ufeff" + csv_buffer.getvalue())
    AuditEvent.objects.create(
        actor=request.user,
        action="project.export",
        object_type="batch",
        object_id=str(batch.id),
        metadata={"file_count": len(entries)},
    )
    export_data.seek(0)
    return FileResponse(
        BytesIO(export_data.getvalue()),
        as_attachment=True,
        filename=f"{root_name}.zip",
        content_type="application/zip",
    )
