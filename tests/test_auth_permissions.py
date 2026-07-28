import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


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
