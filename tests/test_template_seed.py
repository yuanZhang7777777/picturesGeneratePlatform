import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from platform_app.models import Batch, Cluster, OutputTemplate, RuleProfile
from platform_app.services import confirm_generation, create_project


pytestmark = pytest.mark.django_db


REGIONAL_TARGETS = {
    "shopee": {"SG", "MY", "TH", "VN", "PH", "ID", "TW", "BR"},
    "tiktok": {"SG", "MY", "TH", "VN", "PH"},
}


def test_seed_platform_templates_creates_published_global_baseline_and_drafts():
    call_command("seed_platform_templates")

    template = OutputTemplate.objects.get(platform="global", site="", name="Global marketplace baseline")
    assert template.version == "2026.07"
    assert template.status == OutputTemplate.Status.PUBLISHED
    assert template.default_size == "1:1"
    assert template.default_resolution == "1k"
    assert list(template.slots.values_list("name", "purpose")) == [
        ("Main listing", "Primary marketplace listing image"),
        ("Benefit scene", "Product benefit in context"),
        ("Detail/quality", "Material, detail, or quality evidence"),
        ("Usage scene", "Product use in a realistic scene"),
    ]

    rule = RuleProfile.objects.get(platform="global", site="", name="Global marketplace baseline")
    assert rule.version == "2026.07"
    assert rule.status == RuleProfile.Status.PUBLISHED
    assert rule.rules == {
        "review_required": True,
        "localized_copy": True,
        "no_unverified_claims": True,
    }

    for platform, sites in REGIONAL_TARGETS.items():
        assert set(OutputTemplate.objects.filter(platform=platform).values_list("site", flat=True)) == sites
        assert set(RuleProfile.objects.filter(platform=platform).values_list("site", flat=True)) == sites
        assert not OutputTemplate.objects.filter(platform=platform).exclude(status=OutputTemplate.Status.DRAFT).exists()
        assert not RuleProfile.objects.filter(platform=platform).exclude(status=RuleProfile.Status.DRAFT).exists()


def test_seed_platform_templates_is_idempotent_and_preserves_existing_edits():
    call_command("seed_platform_templates")
    global_rule = RuleProfile.objects.get(platform="global", site="", name="Global marketplace baseline")
    global_rule.rules = {"admin_edit": True}
    global_rule.save(update_fields=["rules"])

    call_command("seed_platform_templates")

    assert OutputTemplate.objects.count() == 14
    assert RuleProfile.objects.count() == 14
    assert OutputTemplate.objects.get(platform="global", site="").slots.count() == 4
    global_rule.refresh_from_db()
    assert global_rule.rules == {"admin_edit": True}


def test_seed_uses_stable_identity_without_overwriting_admin_edits():
    call_command("seed_platform_templates")
    template = OutputTemplate.objects.get(seed_key="global-marketplace-baseline-template")
    rule = RuleProfile.objects.get(seed_key="global-marketplace-baseline-rule")
    template.name = "Administrator renamed template"
    template.status = OutputTemplate.Status.DRAFT
    template.version = "admin-version"
    template.save(update_fields=["name", "status", "version"])
    rule.name = "Administrator renamed rule"
    rule.status = RuleProfile.Status.DRAFT
    rule.version = "admin-version"
    rule.rules = {"admin_edit": True}
    rule.save(update_fields=["name", "status", "version", "rules"])

    call_command("seed_platform_templates")

    assert OutputTemplate.objects.count() == 14
    assert RuleProfile.objects.count() == 14
    template.refresh_from_db()
    rule.refresh_from_db()
    assert template.name == "Administrator renamed template"
    assert template.status == OutputTemplate.Status.DRAFT
    assert template.version == "admin-version"
    assert template.slots.count() == 4
    assert rule.name == "Administrator renamed rule"
    assert rule.status == RuleProfile.Status.DRAFT
    assert rule.version == "admin-version"
    assert rule.rules == {"admin_edit": True}


def test_unselected_projects_and_legacy_confirm_use_global_baseline():
    call_command("seed_platform_templates")
    baseline = OutputTemplate.objects.get(seed_key="global-marketplace-baseline-template")
    user = get_user_model().objects.create_user(username="seed-user", password="long-enough-password")

    shopee = create_project(owner=user, name="Shopee project", platform="shopee", market="SG")
    tiktok = create_project(owner=user, name="TikTok project", platform="tiktok", market="TH")
    legacy = Batch.objects.create(owner=user, name="Legacy project", platform="shopee", site="SG", market="SG")
    Cluster.objects.create(batch=legacy, name="Legacy SKU")
    confirm_generation(legacy, user)
    legacy.refresh_from_db()

    assert shopee.output_template == baseline
    assert tiktok.output_template == baseline
    assert legacy.output_template == baseline
    assert not OutputTemplate.objects.filter(
        platform__in=("shopee", "tiktok"), status=OutputTemplate.Status.PUBLISHED
    ).exists()
