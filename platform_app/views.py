from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .forms import BatchForm, FirstPasswordChangeForm
from .models import Batch


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
    return render(request, "platform_app/batch_detail.html", {"batch": batch})


def health_live(request):
    return render(request, "platform_app/health.txt", {"status": "ok"}, content_type="text/plain")
