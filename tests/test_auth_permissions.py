import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory
from django.urls import reverse

from platform_app.models import DailyGenerationUsage, OutputTemplate, PromptNodeTemplate, RuleProfile
from platform_app.services import ErpAuthError


pytestmark = pytest.mark.django_db


def make_user(username, role="operator", must_change_password=False, is_superuser=False):
    user = get_user_model().objects.create_user(
        username=username,
        password="long-enough-password",
        role=role,
        must_change_password=must_change_password,
        is_superuser=is_superuser,
    )
    return user


def test_anonymous_user_is_redirected_to_login(client):
    response = client.get("/batches/")

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))


def test_login_page_explains_erp_shadow_account(client):
    response = client.get(reverse("login"))

    content = response.content.decode()
    assert response.status_code == 200
    assert "ERP 账号登录" in content
    assert "首次 ERP 登录成功会自动创建平台账号" in content
    assert "登录 ERP 并进入平台" in content


def test_logout_clears_django_and_erp_session_state(client):
    user = make_user("logout-user")
    client.force_login(user)
    session = client.session
    session["erp_access_token"] = "erp-token"
    session.save()

    response = client.post(reverse("logout"))

    assert response.status_code == 302
    assert response["Location"] == reverse("login")
    assert "_auth_user_id" not in client.session
    assert "erp_access_token" not in client.session


def test_first_login_user_must_change_password_before_batch_list(client):
    user = make_user("first-login", must_change_password=True)
    client.force_login(user)

    response = client.get("/batches/")

    assert response.status_code == 302
    assert response["Location"] == reverse("password_change")


def test_operator_cannot_read_other_users_batch(client):
    from platform_app.models import Batch

    owner = make_user("owner")
    other = make_user("other")
    batch = Batch.objects.create(owner=owner, name="Private batch")
    client.force_login(other)

    response = client.get(f"/batches/{batch.id}/")

    assert response.status_code == 404


def test_admin_can_read_other_users_batch(client):
    from platform_app.models import Batch

    owner = make_user("owner")
    admin = make_user("admin", role="admin")
    batch = Batch.objects.create(owner=owner, name="Private batch")
    client.force_login(admin)

    response = client.get(f"/batches/{batch.id}/")

    assert response.status_code == 302
    assert response["Location"] == f"/projects/{batch.id}"


def test_erp_login_creates_shadow_user_and_stores_session_token(client, monkeypatch, settings):
    settings.PLATFORM_ADMIN_ERP_USERS = ("liuxuecheng",)

    def fake_authenticate(username, password):
        assert username == "liuxuecheng"
        assert password == "dummy-password"
        user = get_user_model().objects.create_user(
            username=username,
            role=get_user_model().Role.ADMIN,
            must_change_password=False,
        )
        user.set_unusable_password()
        user.save()
        return user, "erp-token"

    monkeypatch.setattr("platform_app.forms.authenticate_erp_user", fake_authenticate)

    response = client.post(
        reverse("login"),
        {"username": "liuxuecheng", "password": "dummy-password"},
    )

    assert response.status_code == 302
    assert response["Location"] == "/"
    assert client.session["erp_access_token"] == "erp-token"
    user = get_user_model().objects.get(username="liuxuecheng")
    assert user.role == get_user_model().Role.ADMIN
    assert user.must_change_password is False
    assert not user.has_usable_password()


def test_erp_login_failure_does_not_create_session(client, monkeypatch):
    def fake_authenticate(username, password):
        raise ErpAuthError("ERP login failed")

    monkeypatch.setattr("platform_app.forms.authenticate_erp_user", fake_authenticate)

    response = client.post(reverse("login"), {"username": "bad", "password": "wrong"})

    assert response.status_code == 200
    assert "_auth_user_id" not in client.session
    assert "erp_access_token" not in client.session


def test_authenticate_erp_user_creates_operator_shadow_user(settings):
    from platform_app.services import authenticate_erp_user

    class FakeErpClient:
        def login(self, username, password):
            assert username == "operator"
            assert password == "secret"
            return "operator-token"

    user, token = authenticate_erp_user("operator", "secret", client=FakeErpClient())

    assert token == "operator-token"
    assert user.username == "operator"
    assert user.role == get_user_model().Role.OPERATOR
    assert user.is_staff is False
    assert user.must_change_password is False
    assert not user.has_usable_password()


def test_authenticate_erp_user_marks_configured_admin(settings):
    from platform_app.services import authenticate_erp_user

    settings.PLATFORM_ADMIN_ERP_USERS = ("liuxuecheng", "\u5218\u5b66\u57ce")

    class FakeErpClient:
        def login(self, username, password):
            return "admin-token"

    user, token = authenticate_erp_user("liuxuecheng", "secret", client=FakeErpClient())

    assert token == "admin-token"
    assert user.role == get_user_model().Role.ADMIN
    assert user.is_staff is True


def test_operator_staff_cannot_see_model_or_queue_configuration_in_admin():
    operator = make_user("operator-staff", is_superuser=False)
    operator.is_staff = True
    operator.save(update_fields=["is_staff"])
    content_types = ContentType.objects.get_for_models(PromptNodeTemplate, DailyGenerationUsage)
    operator.user_permissions.add(
        *Permission.objects.filter(content_type__in=content_types.values(), codename__startswith="view_")
    )
    request = RequestFactory().get("/admin/")
    request.user = operator

    assert admin.site._registry[PromptNodeTemplate].has_module_permission(request) is False
    assert admin.site._registry[DailyGenerationUsage].has_module_permission(request) is False


def test_liuxuecheng_admin_can_manage_templates_and_rules_without_manual_permissions():
    admin_user = make_user("liuxuecheng", role=get_user_model().Role.ADMIN)
    admin_user.is_staff = True
    admin_user.save(update_fields=["is_staff"])
    request = RequestFactory().get("/admin/")
    request.user = admin_user

    assert admin.site._registry[OutputTemplate].has_change_permission(request) is True
    assert admin.site._registry[RuleProfile].has_change_permission(request) is True


def test_admin_rule_publish_requires_official_source_metadata():
    admin_user = make_user("liuxuecheng", role=get_user_model().Role.ADMIN)
    admin_user.is_staff = True
    admin_user.save(update_fields=["is_staff"])
    request = RequestFactory().post("/admin/platform_app/ruleprofile/add/")
    request.user = admin_user
    form_class = admin.site._registry[RuleProfile].get_form(request)

    form = form_class(
        data={
            "platform": "shopee",
            "site": "SG",
            "name": "Shopee SG rules",
            "version": "2026.07",
            "status": RuleProfile.Status.PUBLISHED,
            "source_url": "",
            "checked_at": "",
            "rules": "{}",
        }
    )

    assert form.is_valid() is False
    assert "source_url" in form.errors
    assert "checked_at" in form.errors
