import pytest
from django.core.management import call_command

from platform_app.models import OutputTemplate, RuleProfile


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
