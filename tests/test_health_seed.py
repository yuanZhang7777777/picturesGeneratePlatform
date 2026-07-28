import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse


pytestmark = pytest.mark.django_db


def test_health_endpoints(client):
    live = client.get(reverse("health_live"))
    ready = client.get(reverse("health_ready"))

    assert live.status_code == 200
    assert live.content == b"ok"
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"


def test_seed_admin_is_idempotent(capsys):
    call_command("seed_admin", username="admin", password="long-enough-password")
    call_command("seed_admin", username="admin", password="another-long-password")

    user = get_user_model().objects.get(username="admin")
    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.role == "admin"
    assert user.must_change_password is False
    assert get_user_model().objects.filter(username="admin").count() == 1
    assert "long-enough-password" not in capsys.readouterr().out
