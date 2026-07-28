import json

from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.db import connection
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .forms import BatchForm, FirstPasswordChangeForm
from .models import Batch, Generation
from .services import (
    confirm_generation,
    merge_asset_into_cluster,
    move_asset_to_new_cluster,
    preflight_batch,
    register_uploaded_asset,
    optimize_cluster_prompt,
)


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
    batch = get_object_or_404(Batch.objects.select_related("owner"), id=batch_id)
    require_owner_or_admin(user, batch)
    return batch


def _generation_for_user(user, generation_id):
    generation = get_object_or_404(
        Generation.objects.select_related("batch", "cluster", "output_slot"),
        id=generation_id,
    )
    require_owner_or_admin(user, generation.batch)
    return generation


@login_required
@password_change_required
@require_POST
def api_upload_assets(request, batch_id):
    batch = _batch_for_user(request.user, batch_id)
    assets = []
    for uploaded in request.FILES.getlist("files"):
        try:
            assets.append(
                register_uploaded_asset(batch, uploaded.name, uploaded.read(), uploaded.content_type)
            )
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"asset_count": len(assets)})


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
                        "failure_reason": generation.failure_reason,
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
    return JsonResponse(preflight_batch(batch, request.user))


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
