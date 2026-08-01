import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


pytestmark = pytest.mark.django_db


def make_user(username="configuration-operator"):
    return get_user_model().objects.create_user(
        username=username,
        password="long-enough-password",
        must_change_password=False,
    )


def make_published_configuration():
    from platform_app.models import OutputSlot, OutputTemplate, RuleProfile

    global_template = OutputTemplate.objects.create(
        seed_key="global-marketplace-nine-slot-template",
        platform="global",
        site="",
        name="Global template",
        default_size="1:1",
        default_resolution="1k",
    )
    for order in range(1, 10):
        OutputSlot.objects.create(template=global_template, name=f"Global {order}", order=order)
    global_rules = RuleProfile.objects.create(
        seed_key="global-marketplace-prompt-os-v2-rule",
        platform="global",
        site="",
        name="Global rules",
        status=RuleProfile.Status.PUBLISHED,
    )
    vietnam_template = OutputTemplate.objects.create(
        seed_key="shopee-vn-general-nine-slot-v2-template",
        platform="shopee",
        site="VN",
        name="Shopee VN template",
        default_size="3:4",
        default_resolution="2k",
    )
    for order in range(1, 10):
        OutputSlot.objects.create(template=vietnam_template, name=f"VN {order}", order=order)
    vietnam_rules = RuleProfile.objects.create(
        platform="shopee",
        site="VN",
        name="Shopee VN rules",
        status=RuleProfile.Status.PUBLISHED,
    )
    return global_template, global_rules, vietnam_template, vietnam_rules


def create_cluster(batch, name, **overrides):
    from platform_app.models import Cluster

    return Cluster.objects.create(batch=batch, name=name, **overrides)


def test_name_only_project_uses_global_fallback_and_serializes_required_configuration(client):
    global_template, global_rules, _, _ = make_published_configuration()
    user = make_user()
    client.force_login(user)

    response = client.post(
        reverse("api_project_create"),
        data=json.dumps(
            {
                "name": "Name only",
                "platform": "shopee",
                "market": "VN",
                "seller_tier": "mall",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["configurationStatus"] == "configured"
    assert body["defaultConfig"] == {
        "platform": "shopee",
        "market": "SEA",
        "sellerTier": "general",
        "size": "1:1",
        "resolution": "1K",
        "globalPrompt": "",
    }
    from platform_app.models import Batch

    batch = Batch.objects.get(id=body["id"])
    assert (batch.platform, batch.site, batch.market) == ("shopee", "SEA", "SEA")
    assert batch.output_template_id == global_template.id
    assert batch.rule_profile_id == global_rules.id


def test_project_settings_only_requeues_products_with_changed_effective_configuration(client):
    from platform_app.models import Batch, Cluster, PromptVersion

    global_template, global_rules, vietnam_template, vietnam_rules = make_published_configuration()
    user = make_user()
    batch = Batch.objects.create(
        owner=user,
        name="Configurable",
        output_template=global_template,
        rule_profile=global_rules,
        size="1:1",
        resolution="1k",
    )
    inherited = create_cluster(batch, "Inherited")
    matching_override = create_cluster(
        batch,
        "Already VN",
        platform_override="shopee",
        market_override="VN",
        seller_tier_override="general",
    )
    for cluster in (inherited, matching_override):
        cluster.preparation_status = Cluster.PreparationStatus.READY
        cluster.auto_generate = True
        cluster.save(update_fields=["preparation_status", "auto_generate"])
    PromptVersion.objects.create(cluster=inherited, created_by=user, prompt_text="Keep history")
    client.force_login(user)

    response = client.patch(
        reverse("api_project_settings", args=[batch.id]),
        data=json.dumps(
            {
                "platform": " shopee ",
                "market": " vn ",
                "seller_tier": "general",
                "size": "",
                "resolution": "",
                "global_prompt": "",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["configurationStatus"] == "configured"
    assert body["defaultConfig"] == {
        "platform": "shopee",
        "market": "VN",
        "sellerTier": "general",
        "size": "1:1",
        "resolution": "1K",
        "globalPrompt": "",
    }
    assert body["template"] == vietnam_template.name
    batch.refresh_from_db()
    inherited.refresh_from_db()
    matching_override.refresh_from_db()
    assert batch.rule_profile_id == vietnam_rules.id
    assert inherited.preparation_status == Cluster.PreparationStatus.DRAFT
    assert inherited.auto_generate is False
    assert matching_override.preparation_status == Cluster.PreparationStatus.READY
    assert matching_override.auto_generate is True
    assert PromptVersion.objects.filter(cluster=inherited).count() == 1


def test_cluster_configuration_overrides_normalize_and_null_restores_inheritance(client):
    from platform_app.models import Batch

    global_template, global_rules, _, _ = make_published_configuration()
    user = make_user()
    batch = Batch.objects.create(
        owner=user,
        name="Override project",
        platform="",
        site="",
        market="",
        output_template=global_template,
        rule_profile=global_rules,
    )
    cluster = create_cluster(batch, "Product")
    client.force_login(user)

    set_response = client.post(
        reverse("api_update_cluster", args=[cluster.id]),
        data=json.dumps(
            {
                "expected_version": cluster.version,
                "platform_override": " shopee ",
                "market_override": " vn ",
                "seller_tier_override": "mall",
            }
        ),
        content_type="application/json",
    )

    assert set_response.status_code == 200
    cluster.refresh_from_db()
    assert (cluster.platform_override, cluster.market_override, cluster.seller_tier_override) == (
        "shopee",
        "VN",
        "mall",
    )
    clear_response = client.post(
        reverse("api_update_cluster", args=[cluster.id]),
        data=json.dumps(
            {
                "expected_version": cluster.version,
                "platform_override": None,
                "market_override": None,
                "seller_tier_override": None,
            }
        ),
        content_type="application/json",
    )

    assert clear_response.status_code == 200
    snapshot = client.get(reverse("api_project_snapshot", args=[batch.id])).json()
    product = snapshot["skus"][0]
    assert product["overrides"] == {"platform": None, "market": None, "sellerTier": None}
    assert product["effectiveConfig"] == {
        "platform": "global",
        "market": "",
        "sellerTier": "general",
        "size": "1:1",
        "resolution": "1K",
        "globalPrompt": "",
    }


def test_project_settings_rejects_missing_platform_or_market(client):
    global_template, global_rules, _, _ = make_published_configuration()
    user = make_user()
    from platform_app.models import Batch

    batch = Batch.objects.create(
        owner=user,
        name="Validation",
        output_template=global_template,
        rule_profile=global_rules,
    )
    client.force_login(user)

    response = client.patch(
        reverse("api_project_settings", args=[batch.id]),
        data=json.dumps(
            {
                "platform": "",
                "market": "VN",
                "seller_tier": "general",
                "size": "1:1",
                "resolution": "1k",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "platform" in response.json()["error"]


def test_project_creation_rejects_a_non_object_payload(client):
    user = make_user()
    client.force_login(user)

    response = client.post(
        reverse("api_project_create"),
        data=json.dumps([]),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "object" in response.json()["error"]


def test_cluster_override_for_non_shopee_effectively_uses_general_tier(client):
    from platform_app.models import Batch

    global_template, global_rules, _, _ = make_published_configuration()
    user = make_user()
    batch = Batch.objects.create(
        owner=user,
        name="Non-Shopee override",
        platform="",
        site="",
        market="",
        output_template=global_template,
        rule_profile=global_rules,
    )
    cluster = create_cluster(batch, "Product")
    client.force_login(user)

    response = client.post(
        reverse("api_update_cluster", args=[cluster.id]),
        data=json.dumps(
            {
                "expected_version": cluster.version,
                "platform_override": "tiktok",
                "market_override": "TH",
                "seller_tier_override": "mall",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    snapshot = client.get(reverse("api_project_snapshot", args=[batch.id])).json()
    assert snapshot["skus"][0]["effectiveConfig"]["sellerTier"] == "general"


def test_cluster_update_rejects_a_blank_configuration_override(client):
    from platform_app.models import Batch

    global_template, global_rules, _, _ = make_published_configuration()
    user = make_user()
    batch = Batch.objects.create(
        owner=user,
        name="Blank override",
        output_template=global_template,
        rule_profile=global_rules,
    )
    cluster = create_cluster(batch, "Product")
    client.force_login(user)

    response = client.post(
        reverse("api_update_cluster", args=[cluster.id]),
        data=json.dumps({"expected_version": cluster.version, "platform_override": "  "}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "platform_override" in response.json()["error"]


def test_site_only_project_uses_legacy_market_for_configuration_snapshot(client):
    from platform_app.models import Batch

    global_template, global_rules, _, _ = make_published_configuration()
    user = make_user()
    batch = Batch.objects.create(
        owner=user,
        name="Legacy site project",
        platform="shopee",
        site="SG",
        market="",
        output_template=global_template,
        rule_profile=global_rules,
    )
    client.force_login(user)

    response = client.get(reverse("api_project_snapshot", args=[batch.id]))

    assert response.status_code == 200
    assert response.json()["configurationStatus"] == "configured"
    assert response.json()["defaultConfig"]["market"] == "SG"


@pytest.mark.parametrize(
    ("field", "value"),
    [("platform", "lazada"), ("size", "16:9"), ("resolution", "4k")],
)
def test_project_settings_rejects_unsupported_verified_values(client, field, value):
    from platform_app.models import Batch

    global_template, global_rules, _, _ = make_published_configuration()
    user = make_user()
    batch = Batch.objects.create(
        owner=user,
        name="Validated",
        output_template=global_template,
        rule_profile=global_rules,
    )
    payload = {
        "platform": "shopee",
        "market": "VN",
        "seller_tier": "general",
        "size": "1:1",
        "resolution": "1k",
    }
    payload[field] = value
    client.force_login(user)

    response = client.patch(
        reverse("api_project_settings", args=[batch.id]),
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert field in response.json()["error"]


def test_settings_requeues_when_resolving_template_or_rule_changes(client):
    from platform_app.models import Batch, Cluster

    _, _, _, _ = make_published_configuration()
    user = make_user()
    batch = Batch.objects.create(
        owner=user,
        name="Unbound configuration",
        platform="shopee",
        site="VN",
        market="VN",
        seller_tier="general",
        size="1:1",
        resolution="1k",
    )
    cluster = create_cluster(batch, "Product")
    cluster.preparation_status = Cluster.PreparationStatus.READY
    cluster.save(update_fields=["preparation_status"])
    client.force_login(user)

    response = client.patch(
        reverse("api_project_settings", args=[batch.id]),
        data=json.dumps(
            {
                "platform": "shopee",
                "market": "VN",
                "seller_tier": "general",
                "size": "1:1",
                "resolution": "1k",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    cluster.refresh_from_db()
    assert cluster.preparation_status == Cluster.PreparationStatus.DRAFT
    assert cluster.auto_generate is False
