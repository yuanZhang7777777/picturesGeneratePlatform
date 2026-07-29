import json
import mimetypes
import zipfile
from pathlib import Path, PurePosixPath
from tempfile import TemporaryFile

from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.middleware.csrf import get_token
from django.db import connection
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .forms import BatchForm, FirstPasswordChangeForm
from .models import Asset, AuditEvent, Batch, Generation, ResultAsset
from .services import (
    confirm_generation,
    create_project,
    generation_failure_message,
    import_skus,
    merge_asset_into_cluster,
    move_asset_to_new_cluster,
    review_generation,
    preflight_batch,
    register_uploaded_asset,
    optimize_cluster_prompt,
    safe_storage_path,
    serialize_project,
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
    authentication_form = AuthenticationForm


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
            return redirect("batch_list")
    else:
        form = FirstPasswordChangeForm(request.user)
    return render(request, "platform_app/password_change.html", {"form": form})


@login_required
@password_change_required
def batch_list(request):
    if request.user.is_platform_admin:
        batches = Batch.objects.select_related("owner").order_by("-created_at")
    else:
        batches = request.user.batches.order_by("-created_at")
    return render(request, "platform_app/batch_list.html", {"batches": batches})


@login_required
@password_change_required
@require_http_methods(["GET", "POST"])
def batch_new(request):
    if request.method == "POST":
        form = BatchForm(request.POST)
        if form.is_valid():
            batch = form.save(commit=False)
            batch.owner = request.user
            batch.save()
            return redirect("batch_detail", batch_id=batch.id)
    else:
        form = BatchForm()
    return render(request, "platform_app/batch_form.html", {"form": form})


@login_required
@password_change_required
def batch_detail(request, batch_id):
    batch = get_object_or_404(Batch.objects.select_related("owner"), id=batch_id)
    require_owner_or_admin(request.user, batch)
    clusters = batch.clusters.prefetch_related("cluster_assets__asset", "generations__output_slot")
    return render(request, "platform_app/batch_detail.html", {"batch": batch, "clusters": clusters})


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
        for cluster_id, version in batch.clusters.values_list("id", "version")
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
@require_POST
def api_project_create(request):
    try:
        payload = json.loads(request.body or "{}")
        batch = create_project(
            request.user,
            name=payload.get("name"),
            platform=payload.get("platform", "shopee"),
            market=payload.get("market", "SG"),
            template=payload.get("template"),
            rule_profile=payload.get("rule_profile"),
            size=payload.get("size", ""),
            resolution=payload.get("resolution", ""),
            global_prompt=payload.get("global_prompt", ""),
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
    return JsonResponse({"projects": [_serialize_project(batch) for batch in queryset]})


@login_required
@password_change_required
@require_http_methods(["GET"])
def api_project_snapshot(request, batch_id):
    return JsonResponse(_serialize_project(_batch_for_user(request.user, batch_id)))


@login_required
@password_change_required
@require_POST
def api_upload_assets(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    uploads = request.FILES.getlist("files")
    relative_paths = request.POST.getlist("relative_paths")
    try:
        filenames = [
            _relative_upload_path(relative_paths[index]) if index < len(relative_paths) else uploaded.name
            for index, uploaded in enumerate(uploads)
        ]
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    assets = []
    for uploaded, filename in zip(uploads, filenames):
        try:
            assets.append(
                register_uploaded_asset(batch, filename, uploaded.read(), uploaded.content_type)
            )
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"asset_count": len(assets)})


@login_required
@password_change_required
@require_POST
def api_sku_import(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    try:
        payload = json.loads(request.body or "{}")
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        return JsonResponse(import_skus(batch, payload.get("skus")))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@login_required
@password_change_required
@require_POST
def api_update_cluster(request, cluster_id):
    from .models import Cluster

    cluster = get_object_or_404(Cluster, id=cluster_id)
    require_owner_or_admin(request.user, cluster.batch)
    payload = json.loads(request.body or "{}")
    if payload.get("expected_version") != cluster.version:
        return JsonResponse({"error": "Cluster changed; refresh before saving"}, status=409)
    for field in ["product_name", "product_facts", "identity_lock", "prompt_override"]:
        if field in payload:
            setattr(cluster, field, payload[field])
    cluster.version += 1
    cluster.save(
        update_fields=[
            "product_name",
            "product_facts",
            "identity_lock",
            "prompt_override",
            "version",
            "updated_at",
        ]
    )
    return JsonResponse({"id": str(cluster.id), "version": cluster.version})


@login_required
@password_change_required
@require_POST
def api_optimize_prompt(request, cluster_id):
    from .models import Cluster

    cluster = get_object_or_404(Cluster.objects.select_related("batch"), id=cluster_id)
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

    cluster = get_object_or_404(Cluster, id=cluster_id)
    require_owner_or_admin(request.user, cluster.batch)
    payload = json.loads(request.body or "{}")
    asset = get_object_or_404(Asset, id=payload.get("asset_id"), batch=cluster.batch)
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

    asset = get_object_or_404(Asset, id=asset_id)
    require_owner_or_admin(request.user, asset.batch)
    cluster = move_asset_to_new_cluster(asset)
    return JsonResponse({"cluster_id": str(cluster.id), "asset_id": str(asset.id)})


@login_required
@password_change_required
@require_http_methods(["GET"])
def api_batch_snapshot(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    clusters = []
    for cluster in batch.clusters.prefetch_related("cluster_assets__asset", "generations__output_slot"):
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
                    for item in cluster.cluster_assets.select_related("asset").order_by("order", "id")
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
    batch = _batch_for_user(request.user, batch_id)
    try:
        generations = confirm_generation(batch, request.user)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"generation_count": len(generations)})


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
@require_http_methods(["GET"])
def api_asset_media(request, asset_id):
    asset = get_object_or_404(Asset.objects.select_related("batch"), id=asset_id)
    require_owner_or_admin(request.user, asset.batch)
    try:
        path = safe_storage_path(asset.storage_path, f"originals/{asset.batch_id}")
    except ValueError:
        raise Http404()
    return FileResponse(path.open("rb"), content_type=asset.content_type)


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
        path = safe_storage_path(
            result.storage_path,
            (
                f"results/{generation.batch_id}/{generation.cluster_id}/"
                f"{generation.output_slot_id}/{generation.attempt}"
            ),
        )
    except ValueError:
        raise Http404()
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path.open("rb"), content_type=content_type)


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
@require_http_methods(["GET"])
def api_project_export(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    latest = {}
    generations = (
        batch.generations.select_related("cluster", "output_slot")
        .prefetch_related("result_assets")
        .order_by("cluster_id", "output_slot_id", "-attempt", "-id")
    )
    for generation in generations:
        latest.setdefault((generation.cluster_id, generation.output_slot_id), generation)

    entries = []
    total_size = 0
    for generation in latest.values():
        if (
            generation.status != Generation.Status.COMPLETED
            or generation.review_status != Generation.ReviewStatus.ACCEPTED
        ):
            continue
        results = list(generation.result_assets.all())
        for result in results:
            try:
                path = safe_storage_path(
                    result.storage_path,
                    (
                        f"results/{generation.batch_id}/{generation.cluster_id}/"
                        f"{generation.output_slot_id}/{generation.attempt}"
                    ),
                )
            except ValueError:
                continue
            result_size = path.stat().st_size
            if result_size > MAX_EXPORT_RESULT_BYTES:
                return JsonResponse({"error": "An accepted result is too large to export"}, status=400)
            total_size += result_size
            if total_size > MAX_EXPORT_TOTAL_BYTES:
                return JsonResponse({"error": "The requested export is too large"}, status=400)
            suffix = Path(path.name).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                suffix = ".bin"
            result_suffix = f"-result-{result.id}" if len(results) > 1 else ""
            entries.append(
                (
                    path,
                    (
                        f"project-{batch.id}/cluster-{generation.cluster_id}/"
                        f"slot-{generation.output_slot_id}/attempt-{generation.attempt}"
                        f"{result_suffix}{suffix}"
                    ),
                )
            )
    if not entries:
        return JsonResponse({"error": "No accepted images are available to export"}, status=400)

    temporary = TemporaryFile()
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, archive_name in entries:
            archive.write(path, archive_name)
    AuditEvent.objects.create(
        actor=request.user,
        action="project.export",
        object_type="batch",
        object_id=str(batch.id),
        metadata={"file_count": len(entries)},
    )
    temporary.seek(0)
    return FileResponse(
        temporary,
        as_attachment=True,
        filename=f"project-{batch.id}.zip",
        content_type="application/zip",
    )
