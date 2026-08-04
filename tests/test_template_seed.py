import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from platform_app.models import Batch, Cluster, OutputTemplate, PromptNodeTemplate, RuleProfile
from platform_app.services import confirm_generation, create_project


pytestmark = pytest.mark.django_db


REGIONAL_TARGETS = {
    "shopee": {"SG", "MY", "TH", "VN", "PH", "ID", "TW", "BR"},
    "tiktok": {"SG", "MY", "TH", "VN", "PH", "US"},
}
PROMPT_OS_VERSION = "4.1.0"


def test_seed_platform_templates_creates_published_global_baseline_and_drafts():
    call_command("seed_platform_templates")

    template = OutputTemplate.objects.get(platform="global", site="", version="2026.08.04")
    assert template.version == "2026.08.04"
    assert template.status == OutputTemplate.Status.PUBLISHED
    assert template.default_size == "1:1"
    assert template.default_resolution == "1k"
    assert list(template.slots.values_list("name", "purpose")) == [
        (
            "Standard white-background product hero",
            "Complete, accurate product on a pure white background with no promotional text or watermark",
        ),
        ("Key benefit", "Show one verified product selling point"),
        ("Product detail", "Show material, construction, or detail evidence"),
        ("Function", "Show a verified product function"),
        ("Usage", "Show realistic product use"),
        ("Model or scale", "Show model, wearer, user, pet, or real-world scale without unverified claims"),
        ("Size, packaging, or contents", "Show verified size, packaging, or included items without inventing numbers"),
        ("Marketplace conversion", "Show marketplace-ready conversion creative in the target market language"),
    ]

    rule = RuleProfile.objects.get(platform="global", site="", name="Global marketplace baseline")
    assert rule.version == "2026.07"
    assert rule.status == RuleProfile.Status.PUBLISHED
    assert rule.rules == {
        "review_required": False,
        "localized_copy": True,
        "no_unverified_claims": True,
    }

    for platform, sites in REGIONAL_TARGETS.items():
        assert set(OutputTemplate.objects.filter(platform=platform).values_list("site", flat=True)) == sites
        assert set(RuleProfile.objects.filter(platform=platform).exclude(site="").values_list("site", flat=True)) == sites
        for site in sites:
            assert OutputTemplate.objects.get(seed_key=f"{platform}-{site.lower()}-template").status == OutputTemplate.Status.DRAFT
            assert RuleProfile.objects.get(seed_key=f"{platform}-{site.lower()}-rule").status == RuleProfile.Status.DRAFT

    for seeded_template in OutputTemplate.objects.all():
        assert seeded_template.slots.filter(name="Standard white-background product hero").values_list("name", "purpose").get() == (
            "Standard white-background product hero",
            "Complete, accurate product on a pure white background with no promotional text or watermark",
        )


def test_seed_creates_verified_rule_profiles_and_vietnam_general_template():
    call_command("seed_platform_templates")

    baseline = RuleProfile.objects.get(seed_key="global-marketplace-prompt-os-v2-rule")
    assert baseline.status == RuleProfile.Status.PUBLISHED
    assert baseline.rules[0]["severity"] == "INTERNAL_BASELINE"
    assert baseline.rules[0]["verification_status"] == "verified"

    tiktok_us = RuleProfile.objects.get(seed_key="tiktok-us-verified-20260730-rule")
    assert tiktok_us.status == RuleProfile.Status.PUBLISHED
    rule_ids = {rule["rule_id"] for rule in tiktok_us.rules}
    assert "tiktok.us.gallery.no_added_text" in rule_ids
    assert "tiktok.us.gallery.no_digital_rendering" in rule_ids
    expected_fields = {
        "rule_id",
        "market",
        "seller_tier",
        "category_scope",
        "slot_scope",
        "severity",
        "requirement",
        "prompt_directive",
        "source_url",
        "source_date",
        "checked_at",
        "verification_status",
    }
    assert all(set(rule) == expected_fields for rule in tiktok_us.rules)

    vn_template = OutputTemplate.objects.get(seed_key="shopee-vn-general-nine-slot-v2-template")
    assert vn_template.status == OutputTemplate.Status.PUBLISHED
    assert list(vn_template.slots.values_list("order", "name"))[:2] == [
        (1, "Seller original product photo"),
        (2, "Standard white-background product hero"),
    ]
    prompt_nodes = PromptNodeTemplate.objects.filter(version=PROMPT_OS_VERSION, status="published")
    assert set(prompt_nodes.values_list("node_name", flat=True)) == {
        "N1",
        "N2",
        "N3",
        "N4",
        "N8",
        "N9",
        "N5.generic",
        "N6.generic",
        "N7.generic",
        "N5.shopee",
        "N6.shopee",
        "N7.shopee",
        "N5.tiktok",
        "N6.tiktok",
        "N7.tiktok",
    }
    for prompt_node in prompt_nodes:
        assert len(prompt_node.instruction) >= 300
        assert "JSON" in prompt_node.instruction
        assert "只输出" in prompt_node.instruction


def test_seed_publishes_new_prompt_version_without_overwriting_old_version():
    old = PromptNodeTemplate.objects.create(
        node_name="N1",
        version="3.0.0",
        status=PromptNodeTemplate.Status.PUBLISHED,
        instruction="administrator preserved instruction",
        output_schema={"type": "object"},
    )

    call_command("seed_platform_templates")

    old.refresh_from_db()
    current = PromptNodeTemplate.objects.get(node_name="N1", version=PROMPT_OS_VERSION)
    assert old.status == PromptNodeTemplate.Status.RETIRED
    assert old.instruction == "administrator preserved instruction"
    assert current.status == PromptNodeTemplate.Status.PUBLISHED


def test_new_projects_select_verified_rules_or_internal_baseline():
    call_command("seed_platform_templates")
    user = get_user_model().objects.create_user(username="rule-defaults", password="long-enough-password")

    verified = create_project(owner=user, name="TikTok US", platform="tiktok", market="US")
    unknown = create_project(owner=user, name="Unknown", platform="shopee", market="AE")
    vietnam = create_project(
        owner=user,
        name="Vietnam general",
        platform="shopee",
        market="VN",
        seller_tier="general",
    )

    assert verified.rule_profile.seed_key == "tiktok-us-verified-20260730-rule"
    assert unknown.rule_profile.seed_key == "shopee-official-baseline-20260730-rule"
    assert vietnam.output_template.seed_key == "shopee-vn-general-nine-slot-v2-template"


def test_seed_platform_templates_is_idempotent_and_preserves_existing_edits():
    call_command("seed_platform_templates")
    global_rule = RuleProfile.objects.get(platform="global", site="", name="Global marketplace baseline")
    global_rule.rules = {"admin_edit": True}
    global_rule.save(update_fields=["rules"])

    call_command("seed_platform_templates")

    assert OutputTemplate.objects.count() == 16
    assert RuleProfile.objects.count() == 21
    assert OutputTemplate.objects.get(platform="global", site="", version="2026.08.04").slots.count() == 8
    global_rule.refresh_from_db()
    assert global_rule.rules == {"admin_edit": True}


def test_seed_uses_stable_identity_without_overwriting_admin_edits():
    call_command("seed_platform_templates")
    template = OutputTemplate.objects.get(platform="global", site="", version="2026.08.04")
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

    assert OutputTemplate.objects.count() == 17
    assert RuleProfile.objects.count() == 21
    template.refresh_from_db()
    rule.refresh_from_db()
    assert template.name == "Administrator renamed template"
    assert template.status == OutputTemplate.Status.DRAFT
    assert template.version == "admin-version"
    assert template.slots.count() == 8
    assert rule.name == "Administrator renamed rule"
    assert rule.status == RuleProfile.Status.DRAFT
    assert rule.version == "admin-version"
    assert rule.rules == {"admin_edit": True}


def test_unselected_projects_and_legacy_confirm_use_global_baseline():
    call_command("seed_platform_templates")
    baseline = OutputTemplate.objects.get(platform="global", site="", version="2026.08.04")
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
    assert list(
        OutputTemplate.objects.filter(
            platform__in=("shopee", "tiktok"), status=OutputTemplate.Status.PUBLISHED
        ).values_list("seed_key", flat=True)
    ) == ["shopee-vn-general-nine-slot-v2-template"]


def test_seed_creates_new_eight_slot_version_without_rewriting_existing_template_or_batch():
    legacy = OutputTemplate.objects.create(
        seed_key="global-marketplace-baseline-template",
        platform="global",
        site="",
        name="Legacy global baseline",
        version="2026.07",
        status=OutputTemplate.Status.PUBLISHED,
    )
    legacy_slot = legacy.slots.create(name="Legacy main", order=1, purpose="legacy")
    user = get_user_model().objects.create_user(username="legacy-owner", password="long-enough-password")
    batch = Batch.objects.create(owner=user, name="Legacy project", output_template=legacy)

    call_command("seed_platform_templates")

    legacy.refresh_from_db()
    batch.refresh_from_db()
    assert legacy.version == "2026.07"
    assert list(legacy.slots.values_list("id", "name", "purpose")) == [
        (legacy_slot.id, "Legacy main", "legacy")
    ]
    assert batch.output_template_id == legacy.id


def test_seeded_eight_slot_template_beats_custom_template_and_preserves_old_batch():
    legacy = OutputTemplate.objects.create(
        seed_key="global-marketplace-baseline-template",
        platform="global",
        site="",
        name="Legacy baseline",
        version="2026.07",
        status=OutputTemplate.Status.PUBLISHED,
    )
    legacy.slots.create(name="Legacy main", order=1)
    custom = OutputTemplate.objects.create(
        platform="global", site="", name="Custom nine", version="zzzz", status=OutputTemplate.Status.PUBLISHED
    )
    for order in range(1, 10):
        custom.slots.create(name=f"Custom {order}", order=order)
    user = get_user_model().objects.create_user(username="seed-priority", password="long-enough-password")
    old_batch = Batch.objects.create(owner=user, name="Old", output_template=legacy)

    call_command("seed_platform_templates")
    fresh = create_project(owner=user, name="Fresh")
    from platform_app.services import preflight_batch

    assert fresh.output_template.seed_key == "global-marketplace-eight-slot-template"
    assert preflight_batch(fresh, user)["slot_count"] == 8
    assert old_batch.output_template_id == legacy.id


def test_new_install_baseline_seed_beats_custom_nine_slot_template():
    call_command("seed_platform_templates")
    custom = OutputTemplate.objects.create(
        platform="global", site="", name="Custom nine", version="zzzz", status=OutputTemplate.Status.PUBLISHED
    )
    for order in range(1, 10):
        custom.slots.create(name=f"Custom {order}", order=order)
    user = get_user_model().objects.create_user(username="baseline-priority", password="long-enough-password")

    project = create_project(owner=user, name="Fresh")

    assert project.output_template.seed_key == "global-marketplace-baseline-template"
    assert project.output_template.slots.count() == 8


def test_upgrade_seed_beats_legacy_baseline_that_already_has_eight_slots():
    user = get_user_model().objects.create_user(username="upgrade-priority", password="long-enough-password")
    legacy = OutputTemplate.objects.create(
        seed_key="global-marketplace-baseline-template",
        platform="global",
        site="",
        name="Legacy eight",
        version="2026.07",
        status=OutputTemplate.Status.PUBLISHED,
    )
    for order in range(1, 9):
        legacy.slots.create(name=f"Legacy {order}", order=order)
    old_batch = Batch.objects.create(owner=user, name="Old", output_template=legacy)
    old_cluster = Cluster.objects.create(batch=old_batch, name="Old SKU")
    old_generations = confirm_generation(old_batch, user)

    call_command("seed_platform_templates")
    from platform_app.services import preflight_batch

    fresh = create_project(owner=user, name="Fresh")
    pending = Batch.objects.create(owner=user, name="Pending")
    Cluster.objects.create(batch=pending, name="Pending SKU")
    preflight = preflight_batch(pending, user)
    confirmed = confirm_generation(pending, user)
    upgrade = OutputTemplate.objects.get(seed_key="global-marketplace-eight-slot-template")
    old_batch.refresh_from_db()
    pending.refresh_from_db()

    assert fresh.output_template_id == upgrade.id
    assert preflight["template"]["id"] == str(upgrade.id)
    assert pending.output_template_id == upgrade.id
    assert len(confirmed) == 8
    assert old_batch.output_template_id == legacy.id
    assert list(old_batch.generations.values_list("id", flat=True)) == [generation.id for generation in old_generations]
    assert old_cluster.generations.count() == 8


def test_seed_creates_eight_slot_upgrade_without_rewriting_generated_legacy_template():
    user = get_user_model().objects.create_user(username="eight-slot-owner", password="long-enough-password")
    legacy = OutputTemplate.objects.create(
        seed_key="global-marketplace-baseline-template",
        platform="global",
        site="",
        name="Legacy eight",
        version="2026.07.8",
        status=OutputTemplate.Status.PUBLISHED,
    )
    for order in range(1, 9):
        legacy.slots.create(name=f"Legacy {order}", order=order)
    old_batch = Batch.objects.create(owner=user, name="Old", output_template=legacy)
    Cluster.objects.create(batch=old_batch, name="Old SKU")
    old_generations = confirm_generation(old_batch, user)

    call_command("seed_platform_templates")
    fresh = create_project(owner=user, name="Fresh")

    old_batch.refresh_from_db()
    assert fresh.output_template.seed_key == "global-marketplace-eight-slot-template"
    assert fresh.output_template.slots.count() == 8
    assert old_batch.output_template_id == legacy.id
    assert list(old_batch.generations.values_list("id", flat=True)) == [item.id for item in old_generations]
