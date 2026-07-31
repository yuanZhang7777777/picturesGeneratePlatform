from io import BytesIO
import json
from pathlib import Path
import uuid

import pytest
import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError
from django.test import Client
from django.urls import reverse
from PIL import Image

from platform_app.models import Asset, Batch, Cluster, Generation, OutputSlot, SkuImportItem
from platform_app.services import (
    CatalogAuthExpired,
    CatalogClient,
    CatalogError,
    ErpAuthClient,
    ErpAuthError,
    LocalStorage,
    StorageError,
    download_catalog_image,
    import_skus,
)


pytestmark = pytest.mark.django_db


def image_bytes(width=8, height=6):
    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, "PNG")
    return buffer.getvalue()


class FakeCatalogClient:
    def fetch_products(self, skus):
        return {
            "OK-1": {"sku": "OK-1", "productName": "Travel mug", "pic": "https://8.8.8.8/mug.png"},
        }


class MissingCatalogClient:
    def fetch_products(self, skus):
        return {}


class MultiCatalogClient:
    def fetch_products(self, skus):
        return {
            sku: {"sku": sku, "productName": sku, "pic": f"https://8.8.8.8/{sku}.png"}
            for sku in skus
        }


class FakeResponse:
    def __init__(self, status_code, headers=None, chunks=()):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = list(chunks)
        self.closed = False

    @property
    def is_redirect(self):
        return self.status_code in {301, 302, 303, 307, 308}

    def iter_content(self, size):
        return iter(self._chunks)

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)

    def get(self, *args, **kwargs):
        return next(self.responses)


class FakeCatalogResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeCatalogSession:
    def __init__(self, responses):
        self.responses = iter(responses)

    def post(self, *args, **kwargs):
        return next(self.responses)


def make_batch():
    call_command("seed_platform_templates")
    user = get_user_model().objects.create_user(
        username="sku-operator", password="long-enough-password", must_change_password=False
    )
    return user, Batch.objects.create(owner=user, name="SKU import")


def test_sku_import_keeps_success_when_another_sku_fails_and_exposes_plan_snapshot(
    client, tmp_path, settings, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    user, batch = make_batch()
    monkeypatch.setattr(
        "platform_app.views.import_skus",
        lambda target, skus, erp_token=None, mode="organize": import_skus(
            target, skus, catalog_client=FakeCatalogClient(), image_downloader=lambda url: (image_bytes(), "image/png")
        ),
    )
    client.force_login(user)
    session = client.session
    session["erp_access_token"] = "user-token"
    session.save()

    response = client.post(
        reverse("api_sku_import", args=[batch.id]),
        data='{"skus": ["OK-1", "MISSING"]}',
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["imported"] == 1
    assert response.json()["failed"] == 1
    imported_item, failed_item = response.json()["items"]
    assert imported_item == {
        "sku": "OK-1",
        "productName": "Travel mug",
        "status": "imported",
        "clusterId": imported_item["clusterId"],
        "errorCode": None,
    }
    assert failed_item == {
        "sku": "MISSING",
        "productName": "",
        "status": "failed",
        "clusterId": None,
        "errorCode": "sku_not_found",
    }
    cluster = Cluster.objects.get(batch=batch, sku="OK-1")
    assert cluster.product_name == "Travel mug"
    assert cluster.analysis_snapshot["product_name_source"] == "erp"
    assert cluster.preparation_status == Cluster.PreparationStatus.DRAFT
    from platform_app.services import _claim_prompt_cluster
    assert _claim_prompt_cluster(cluster) is None
    assert cluster.assets.count() == 1
    assert list(SkuImportItem.objects.filter(batch=batch).values_list("sku", "status")) == [
        ("OK-1", SkuImportItem.Status.IMPORTED),
        ("MISSING", SkuImportItem.Status.FAILED),
    ]

    snapshot = client.get(reverse("api_project_snapshot", args=[batch.id])).json()
    sku = snapshot["skus"][0]
    assert sku["sku"] == "OK-1"
    assert sku["productName"] == "Travel mug"
    assert sku["productNameSource"] == "erp"
    assert sku["importStatus"] == "imported"
    assert snapshot["skuImports"] == [failed_item, imported_item]
    assert len(snapshot["templateSlots"]) == 9
    assert snapshot["preflight"]["generation_count"] == 9


def test_sku_import_records_mode_and_can_append_after_generation_history(client, tmp_path, settings, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    user, batch = make_batch()
    old_cluster = Cluster.objects.create(batch=batch, name="Existing")
    Generation.objects.create(
        batch=batch,
        cluster=old_cluster,
        output_slot=OutputSlot.objects.order_by("order").first(),
    )
    monkeypatch.setattr(
        "platform_app.views.import_skus",
        lambda target, skus, erp_token=None, mode="organize": import_skus(
            target,
            skus,
            catalog_client=FakeCatalogClient(),
            image_downloader=lambda url: (image_bytes(), "image/png"),
            mode=mode,
        ),
    )
    client.force_login(user)
    session = client.session
    session["erp_access_token"] = "user-token"
    session.save()

    response = client.post(
        reverse("api_sku_import", args=[batch.id]),
        data='{"skus": ["OK-1"], "mode": "auto"}',
        content_type="application/json",
    )

    assert response.status_code == 200
    batch.refresh_from_db()
    cluster = Cluster.objects.get(batch=batch, sku="OK-1")
    assert batch.last_import_mode == "auto"
    assert cluster.auto_generate is True
    assert cluster.preparation_status == "pending"


def test_sku_import_requires_erp_session_token(client):
    user, batch = make_batch()
    client.force_login(user)

    response = client.post(
        reverse("api_sku_import", args=[batch.id]),
        data='{"skus": ["OK-1"]}',
        content_type="application/json",
    )

    assert response.status_code == 401
    assert response.json()["error"] == "ERP login expired"
    assert not SkuImportItem.objects.filter(batch=batch).exists()


def test_sku_import_passes_session_token_to_catalog(client, monkeypatch):
    user, batch = make_batch()
    client.force_login(user)
    session = client.session
    session["erp_access_token"] = "user-token"
    session.save()
    captured = {}

    def fake_import_skus(target, skus, *, erp_token=None, mode="organize"):
        captured["batch"] = target
        captured["skus"] = skus
        captured["token"] = erp_token
        captured["mode"] = mode
        return {"imported": 0, "failed": 0, "items": []}

    monkeypatch.setattr("platform_app.views.import_skus", fake_import_skus)

    response = client.post(
        reverse("api_sku_import", args=[batch.id]),
        data='{"skus": ["OK-1"]}',
        content_type="application/json",
    )

    assert response.status_code == 200
    assert captured == {"batch": batch, "skus": ["OK-1"], "token": "user-token", "mode": None}


def test_sku_import_propagates_expired_erp_token_without_audit_rows(client, monkeypatch):
    user, batch = make_batch()
    client.force_login(user)
    session = client.session
    session["erp_access_token"] = "expired-token"
    session.save()

    def fake_import_skus(target, skus, *, erp_token=None, mode="organize"):
        raise CatalogAuthExpired("ERP login expired")

    monkeypatch.setattr("platform_app.views.import_skus", fake_import_skus)

    response = client.post(
        reverse("api_sku_import", args=[batch.id]),
        data='{"skus": ["OK-1"]}',
        content_type="application/json",
    )

    assert response.status_code == 401
    assert response.json()["error"] == "ERP login expired"
    assert not SkuImportItem.objects.filter(batch=batch).exists()


def test_catalog_client_uses_supplied_erp_token_for_query(settings):
    settings.CATALOG_QUERY_URL = "https://catalog.test/query"
    session = FakeCatalogSession(
        [
            FakeCatalogResponse(
                {
                    "success": True,
                    "data": [{"sku": "OK-1", "productName": "Travel mug", "pic": "https://8.8.8.8/mug.png"}],
                }
            )
        ]
    )
    seen = {}
    original_post = session.post

    def recording_post(*args, **kwargs):
        seen.update(kwargs)
        return original_post(*args, **kwargs)

    session.post = recording_post

    products = CatalogClient(token="user-token", session=session).fetch_products(["OK-1"])

    assert products["OK-1"]["productName"] == "Travel mug"
    assert seen["headers"] == {"Authorization": "user-token"}


def test_catalog_client_raises_auth_expired_for_unauthorized_query(settings):
    settings.CATALOG_QUERY_URL = "https://catalog.test/query"
    session = FakeCatalogSession([FakeCatalogResponse({}, status_code=401)])

    with pytest.raises(CatalogAuthExpired):
        CatalogClient(token="expired-token", session=session).fetch_products(["OK-1"])


def test_sku_reimport_does_not_duplicate_cluster_or_image(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    _, batch = make_batch()

    import_skus(
        batch,
        ["OK-1"],
        catalog_client=FakeCatalogClient(),
        image_downloader=lambda url: (image_bytes(), "image/png"),
    )
    cluster = Cluster.objects.get(batch=batch, sku="OK-1")
    cluster.product_name = "Employee name"
    cluster.analysis_snapshot = {"product_name_source": "manual"}
    cluster.save(update_fields=["product_name", "analysis_snapshot"])
    import_skus(
        batch,
        ["OK-1"],
        catalog_client=FakeCatalogClient(),
        image_downloader=lambda url: (image_bytes(), "image/png"),
    )

    assert Cluster.objects.filter(batch=batch, sku="OK-1").count() == 1
    cluster.refresh_from_db()
    assert cluster.product_name == "Employee name"
    assert cluster.analysis_snapshot["product_name_source"] == "manual"
    assert Asset.objects.filter(batch=batch).count() == 1
    assert SkuImportItem.objects.filter(batch=batch, sku="OK-1").count() == 2


def test_sku_reimport_does_not_reactivate_an_archived_product(tmp_path, settings):
    from django.utils import timezone

    settings.MEDIA_ROOT = tmp_path
    _, batch = make_batch()
    cluster = Cluster.objects.create(
        batch=batch,
        sku="OK-1",
        name="Archived product",
        archived_at=timezone.now(),
        preparation_status=Cluster.PreparationStatus.READY,
    )

    result = import_skus(
        batch,
        ["OK-1"],
        catalog_client=FakeCatalogClient(),
        image_downloader=lambda url: pytest.fail("archived SKU must not download another image"),
        mode=Batch.ImportMode.AUTO,
    )

    cluster.refresh_from_db()
    assert result["imported"] == 0
    assert result["failed"] == 1
    assert result["items"][0]["errorCode"] == "import_failed"
    assert cluster.archived_at is not None
    assert cluster.preparation_status == Cluster.PreparationStatus.READY
    assert cluster.auto_generate is False
    assert Asset.objects.filter(batch=batch).count() == 0


def test_snapshot_uses_attempt_order_when_reimports_share_created_at(client, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    user, batch = make_batch()
    import_skus(
        batch,
        ["OK-1"],
        catalog_client=FakeCatalogClient(),
        image_downloader=lambda url: (image_bytes(), "image/png"),
    )
    import_skus(batch, ["OK-1"], catalog_client=MissingCatalogClient())
    imported = SkuImportItem.objects.get(batch=batch, sku="OK-1", status=SkuImportItem.Status.IMPORTED)
    failed = SkuImportItem.objects.get(batch=batch, sku="OK-1", status=SkuImportItem.Status.FAILED)
    SkuImportItem.objects.filter(pk=imported.pk).update(created_at=imported.created_at, id=uuid.UUID(int=2))
    SkuImportItem.objects.filter(pk=failed.pk).update(created_at=imported.created_at, id=uuid.UUID(int=1))
    client.force_login(user)

    snapshot = client.get(reverse("api_project_snapshot", args=[batch.id])).json()

    assert snapshot["skuImports"] == [
        {
            "sku": "OK-1",
            "productName": "",
            "status": "failed",
            "clusterId": None,
            "errorCode": "sku_not_found",
        }
    ]
    assert snapshot["skus"][0]["importStatus"] == "failed"
    assert list(
        SkuImportItem.objects.filter(batch=batch, sku="OK-1")
        .order_by("attempt")
        .values_list("attempt", flat=True)
    ) == [1, 2]


def test_sku_import_rejects_disallowed_catalog_image_url_without_storing_it(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    _, batch = make_batch()

    class UnsafeCatalogClient:
        def fetch_products(self, skus):
            return {"BAD-1": {"sku": "BAD-1", "productName": "Unsafe", "pic": "https://127.0.0.1/private.png"}}

    result = import_skus(batch, ["BAD-1"], catalog_client=UnsafeCatalogClient())

    assert {key: result[key] for key in ("imported", "failed")} == {"imported": 0, "failed": 1}
    item = SkuImportItem.objects.get(batch=batch, sku="BAD-1")
    assert item.status == SkuImportItem.Status.FAILED
    assert "127.0.0.1" not in item.error_message
    assert not Asset.objects.filter(batch=batch).exists()


def test_oversized_pixel_image_is_audited_and_does_not_stop_later_sku(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    settings.CATALOG_MAX_IMAGE_PIXELS = 10
    _, batch = make_batch()

    def download(url):
        return (image_bytes(), "image/png") if "PIXEL" in url else (image_bytes(1, 1), "image/png")

    result = import_skus(batch, ["PIXEL", "GOOD"], catalog_client=MultiCatalogClient(), image_downloader=download)

    assert {key: result[key] for key in ("imported", "failed")} == {"imported": 1, "failed": 1}
    assert list(SkuImportItem.objects.filter(batch=batch).values_list("sku", "status")) == [
        ("PIXEL", SkuImportItem.Status.FAILED),
        ("GOOD", SkuImportItem.Status.IMPORTED),
    ]


def test_bad_image_does_not_stop_later_valid_sku(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    _, batch = make_batch()

    def download(url):
        return (b"not an image", "image/png") if "BAD" in url else (image_bytes(), "image/png")

    result = import_skus(batch, ["BAD", "GOOD"], catalog_client=MultiCatalogClient(), image_downloader=download)

    assert {key: result[key] for key in ("imported", "failed")} == {"imported": 1, "failed": 1}
    assert Cluster.objects.filter(batch=batch, sku="GOOD").exists()
    assert SkuImportItem.objects.get(batch=batch, sku="BAD").status == SkuImportItem.Status.FAILED


def test_storage_error_marks_current_sku_failed_without_asset(tmp_path, settings, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    _, batch = make_batch()

    def fail_save(self, storage_path, data):
        raise StorageError("boom")

    monkeypatch.setattr(LocalStorage, "save", fail_save)

    result = import_skus(
        batch,
        ["OK-1"],
        catalog_client=FakeCatalogClient(),
        image_downloader=lambda url: (image_bytes(), "image/png"),
    )

    assert {key: result[key] for key in ("imported", "failed")} == {"imported": 0, "failed": 1}
    item = SkuImportItem.objects.get(batch=batch, sku="OK-1")
    assert item.error_message == "Catalog image could not be archived"
    assert result["items"][0]["errorCode"] == "archive_failed"
    assert not Asset.objects.filter(batch=batch).exists()


@pytest.mark.parametrize(
    "response, expected",
    [
        (FakeResponse(200, {"Content-Type": "text/plain"}), "not supported"),
        (FakeResponse(200, {"Content-Type": "image/png", "Content-Length": "99"}), "too large"),
        (FakeResponse(200, {"Content-Type": "image/png"}, [b"x" * 20]), "too large"),
    ],
)
def test_catalog_download_rejects_unsafe_content_and_closes_response(settings, response, expected):
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    settings.CATALOG_MAX_IMAGE_BYTES = 10

    with pytest.raises(CatalogError, match=expected):
        download_catalog_image("https://8.8.8.8/product.png", session=FakeSession([response]))

    assert response.closed


def test_catalog_download_rechecks_redirect_host_and_closes_each_response(settings):
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    redirect = FakeResponse(302, {"Location": "https://127.0.0.1/private.png"})

    with pytest.raises(CatalogError, match="not allowed"):
        download_catalog_image("https://8.8.8.8/product.png", session=FakeSession([redirect]))

    assert redirect.closed


def test_sku_import_requires_csrf_and_owner_permission(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    owner, batch = make_batch()
    other = get_user_model().objects.create_user(
        username="sku-other", password="long-enough-password", must_change_password=False
    )
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)

    assert client.post(reverse("api_sku_import", args=[batch.id]), data="{}", content_type="application/json").status_code == 403
    token = client.get(reverse("api_csrf")).json()["csrf_token"]
    session = client.session
    session["erp_access_token"] = "user-token"
    session.save()
    assert client.post(
        reverse("api_sku_import", args=[batch.id]),
        data='{"skus": ["MISSING"]}',
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    ).status_code == 200

    client.force_login(other)
    assert client.post(
        reverse("api_sku_import", args=[batch.id]),
        data='{"skus": ["MISSING"]}',
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    ).status_code == 404


def test_catalog_download_allows_http_for_a_whitelisted_public_ip(settings):
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    response = FakeResponse(200, {"Content-Type": "image/png"}, [b"image"])

    data, content_type = download_catalog_image(
        "http://8.8.8.8/product.png", session=FakeSession([response])
    )

    assert (data, content_type) == (b"image", "image/png")
    assert response.closed


def test_catalog_download_allows_https_for_a_whitelisted_public_ipv6(settings):
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("2606:4700:4700::1111",)
    response = FakeResponse(200, {"Content-Type": "image/png"}, [b"image"])

    data, content_type = download_catalog_image(
        "https://[2606:4700:4700::1111]/product.png",
        session=FakeSession([response]),
    )

    assert (data, content_type) == (b"image", "image/png")
    assert response.closed


def test_catalog_download_rejects_allowlisted_hostname(settings, monkeypatch):
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("images.example.test",)
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("8.8.8.8", 0))],
    )

    with pytest.raises(CatalogError, match="not allowed"):
        download_catalog_image("http://images.example.test/private.png", session=FakeSession([]))


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "127.0.0.1",
        "192.0.2.1",
        "224.0.0.1",
        "::1",
        "2001:db8::1",
        "ff02::1",
    ],
)
def test_catalog_download_rejects_non_public_ip_even_if_allowlisted(settings, address):
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = (address,)
    host = f"[{address}]" if ":" in address else address

    with pytest.raises(CatalogError, match="not allowed"):
        download_catalog_image(f"http://{host}/private.png", session=FakeSession([]))


def test_catalog_download_rechecks_exact_ip_allowlist_on_redirect(settings):
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    redirect = FakeResponse(302, {"Location": "https://1.1.1.1/product.png"})

    with pytest.raises(CatalogError, match="not allowed"):
        download_catalog_image("https://8.8.8.8/product.png", session=FakeSession([redirect]))

    assert redirect.closed


def test_sku_import_rejects_more_than_configured_request_limit(client, settings):
    settings.CATALOG_MAX_SKUS_PER_REQUEST = 50
    user, batch = make_batch()
    client.force_login(user)

    response = client.post(
        reverse("api_sku_import", args=[batch.id]),
        data=json.dumps({"skus": [f"SKU-{index}" for index in range(51)]}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json() == {"error": "at most 50 SKUs are allowed"}
    assert not SkuImportItem.objects.filter(batch=batch).exists()


def test_sku_import_persists_each_item_before_downloading_the_next(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    _, batch = make_batch()

    def download(url):
        if url.endswith("/SECOND.png"):
            assert Cluster.objects.filter(batch=batch, sku="FIRST").exists()
        return image_bytes(), "image/png"

    result = import_skus(
        batch,
        ["FIRST", "SECOND"],
        catalog_client=MultiCatalogClient(),
        image_downloader=download,
    )

    assert {key: result[key] for key in ("imported", "failed")} == {"imported": 2, "failed": 0}


def test_sku_import_after_generation_history_appends_new_product(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    _, batch = make_batch()
    old_cluster = Cluster.objects.create(batch=batch, name="Existing")
    Generation.objects.create(
        batch=batch,
        cluster=old_cluster,
        output_slot=OutputSlot.objects.order_by("order").first(),
    )

    result = import_skus(
        batch,
        ["OK-1"],
        catalog_client=FakeCatalogClient(),
        image_downloader=lambda url: (image_bytes(), "image/png"),
    )

    assert result["imported"] == 1
    assert set(batch.clusters.values_list("name", flat=True)) == {"Existing", "Travel mug"}
    assert Asset.objects.filter(batch=batch).count() == 1


def test_sku_import_ignores_legacy_generation_key_after_download(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    _, batch = make_batch()

    def download(url):
        Batch.objects.filter(pk=batch.pk).update(confirmed_generation_key=uuid.uuid4())
        return image_bytes(), "image/png"

    result = import_skus(
        batch,
        ["RACING"],
        catalog_client=MultiCatalogClient(),
        image_downloader=download,
    )

    assert result["items"][0]["errorCode"] is None
    assert Cluster.objects.filter(batch=batch, sku="RACING").exists()
    assert Asset.objects.filter(batch=batch).exists()


def test_catalog_failure_is_audited_as_unavailable_without_leaking_details(
    client, tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path
    user, batch = make_batch()

    class BrokenCatalogClient:
        def fetch_products(self, skus):
            raise CatalogError(
                "https://catalog.internal/query token=raw-secret provider-response"
            )

    result = import_skus(
        batch,
        ["ONE", "TWO"],
        catalog_client=BrokenCatalogClient(),
    )
    client.force_login(user)
    snapshot = client.get(reverse("api_project_snapshot", args=[batch.id])).json()
    serialized = json.dumps({"result": result, "snapshot": snapshot})

    assert [item["errorCode"] for item in result["items"]] == [
        "catalog_unavailable",
        "catalog_unavailable",
    ]
    assert set(
        SkuImportItem.objects.filter(batch=batch).values_list("error_message", flat=True)
    ) == {"Catalog service is unavailable"}
    assert "catalog.internal" not in serialized
    assert "raw-secret" not in serialized
    assert "provider-response" not in serialized


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"success": False, "data": {"accessToken": "secret"}},
        {"success": True, "code": 500, "data": {"accessToken": "secret"}},
        {"success": True, "status": "error", "data": {"accessToken": "secret"}},
        {"success": True, "data": []},
        {"success": True, "data": {"accessToken": "   "}},
        {"success": True, "data": {"accessToken": 123}},
        {"success": True, "data": {"token": {"value": "secret"}}},
    ],
)
def test_erp_auth_client_rejects_invalid_login_envelopes(settings, payload):
    settings.ERP_LOGIN_URL = "https://catalog.test/login"
    auth = ErpAuthClient(session=FakeCatalogSession([FakeCatalogResponse(payload)]))

    with pytest.raises(ErpAuthError):
        auth.login("operator", "secret")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"success": False, "data": []},
        {"success": True, "code": 500, "data": []},
        {"success": True, "status": "error", "data": []},
        {"success": True, "data": {}},
    ],
)
def test_catalog_client_rejects_invalid_query_envelopes(settings, payload):
    settings.CATALOG_QUERY_URL = "https://catalog.test/query"
    catalog = CatalogClient(
        token="runtime-token",
        session=FakeCatalogSession(
            [
                FakeCatalogResponse(payload),
            ]
        )
    )

    with pytest.raises(CatalogError):
        catalog.fetch_products(["SKU-1"])


def test_catalog_client_http_error_is_catalog_unavailable_for_import(
    tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_QUERY_URL = "https://catalog.test/query"
    _, batch = make_batch()
    catalog = CatalogClient(
        token="runtime-token",
        session=FakeCatalogSession(
            [
                FakeCatalogResponse({}, status_code=503),
            ]
        )
    )

    result = import_skus(batch, ["SKU-1"], catalog_client=catalog)

    assert result["items"][0]["errorCode"] == "catalog_unavailable"


def test_catalog_client_explicit_success_with_empty_data_is_sku_not_found(
    tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_QUERY_URL = "https://catalog.test/query"
    _, batch = make_batch()
    catalog = CatalogClient(
        token="runtime-token",
        session=FakeCatalogSession(
            [
                FakeCatalogResponse({"success": True, "data": []}),
            ]
        )
    )

    result = import_skus(batch, ["SKU-1"], catalog_client=catalog)

    assert result["items"][0]["errorCode"] == "sku_not_found"


@pytest.mark.parametrize("payload", [["malformed"], [{"productName": "Missing SKU"}]])
def test_catalog_client_malformed_product_data_is_catalog_unavailable(
    tmp_path, settings, payload
):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_QUERY_URL = "https://catalog.test/query"
    _, batch = make_batch()
    catalog = CatalogClient(
        token="runtime-token",
        session=FakeCatalogSession(
            [
                FakeCatalogResponse({"success": True, "data": payload}),
            ]
        )
    )

    result = import_skus(batch, ["SKU-1"], catalog_client=catalog)

    assert result["items"][0]["errorCode"] == "catalog_unavailable"


def test_local_archive_oserror_fails_only_current_sku_and_removes_partial_file(
    tmp_path, settings, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    _, batch = make_batch()
    failing_image = image_bytes(8, 6)
    succeeding_image = image_bytes(1, 1)
    write_bytes = Path.write_bytes

    def flaky_write(target, data):
        written = write_bytes(target, data)
        if data == failing_image:
            raise OSError("disk write failed after creating the file")
        return written

    monkeypatch.setattr(Path, "write_bytes", flaky_write)

    result = import_skus(
        batch,
        ["BAD", "GOOD"],
        catalog_client=MultiCatalogClient(),
        image_downloader=lambda url: (
            (failing_image, "image/png")
            if url.endswith("/BAD.png")
            else (succeeding_image, "image/png")
        ),
    )

    assert [item["errorCode"] for item in result["items"]] == [
        "archive_failed",
        None,
    ]
    assert Cluster.objects.filter(batch=batch, sku="GOOD").exists()
    assert not Cluster.objects.filter(batch=batch, sku="BAD").exists()
    assert sorted(path.name for path in tmp_path.rglob("*.png")) == [
        Path(Asset.objects.get(batch=batch).storage_path).name
    ]


def test_persistence_failure_after_archive_removes_orphan_file(
    tmp_path, settings, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path
    settings.CATALOG_ALLOWED_IMAGE_HOSTS = ("8.8.8.8",)
    _, batch = make_batch()
    create_cluster = Cluster.objects.create

    def fail_after_archive(**kwargs):
        if kwargs.get("sku") == "FAIL":
            raise DatabaseError("database write failed")
        return create_cluster(**kwargs)

    monkeypatch.setattr(Cluster.objects, "create", fail_after_archive)

    result = import_skus(
        batch,
        ["FAIL", "GOOD"],
        catalog_client=MultiCatalogClient(),
        image_downloader=lambda url: (image_bytes(), "image/png"),
    )

    assert [item["errorCode"] for item in result["items"]] == ["archive_failed", None]
    assert not Cluster.objects.filter(batch=batch, sku="FAIL").exists()
    assert Cluster.objects.filter(batch=batch, sku="GOOD").exists()
    assert sorted(path.name for path in tmp_path.rglob("*.png")) == [
        Path(Asset.objects.get(batch=batch).storage_path).name
    ]


@pytest.mark.parametrize("payload", ["[]", "null"])
def test_sku_import_rejects_non_object_json(client, payload):
    user, batch = make_batch()
    client.force_login(user)

    response = client.post(
        reverse("api_sku_import", args=[batch.id]), data=payload, content_type="application/json"
    )

    assert response.status_code == 400
