import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

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
    response = client.get(reverse("batch_list"))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))


def test_first_login_user_must_change_password_before_batch_list(client):
    user = make_user("first-login", must_change_password=True)
    client.force_login(user)

    response = client.get(reverse("batch_list"))

    assert response.status_code == 302
    assert response["Location"] == reverse("password_change")


def test_operator_cannot_read_other_users_batch(client):
    from platform_app.models import Batch

    owner = make_user("owner")
    other = make_user("other")
    batch = Batch.objects.create(owner=owner, name="Private batch")
    client.force_login(other)

    response = client.get(reverse("batch_detail", args=[batch.id]))

    assert response.status_code == 404


def test_admin_can_read_other_users_batch(client):
    from platform_app.models import Batch

    owner = make_user("owner")
    admin = make_user("admin", role="admin")
    batch = Batch.objects.create(owner=owner, name="Private batch")
    client.force_login(admin)

    response = client.get(reverse("batch_detail", args=[batch.id]))

    assert response.status_code == 200
    assert b"Private batch" in response.content


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
