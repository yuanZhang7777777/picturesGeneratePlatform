from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from PIL import Image


pytestmark = pytest.mark.django_db


def make_user(username="operator"):
    return get_user_model().objects.create_user(
        username=username,
        password="long-enough-password",
        daily_generation_limit=100,
    )


def image_bytes():
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
    return buffer.getvalue()


def make_batch_with_images(tmp_path, settings, count=1):
    from platform_app.models import OutputSlot, OutputTemplate
    from platform_app.services import create_batch, register_uploaded_asset

    settings.MEDIA_ROOT = tmp_path
    user = make_user()
    template = OutputTemplate.objects.create(platform="global", site="", name="Test global baseline")
    OutputSlot.objects.create(template=template, name="main", order=1)
    batch = create_batch(user, "Batch 1")
    for index in range(count):
        register_uploaded_asset(batch, f"{index}.png", image_bytes(), "image/png")
    return user, batch


def test_preflight_counts_clusters_and_quota(tmp_path, settings):
    from platform_app.services import preflight_batch

    user, batch = make_batch_with_images(tmp_path, settings, count=2)

    result = preflight_batch(batch, user)

    assert result["cluster_count"] == 2
    assert result["generation_count"] == 2
    assert result["org_remaining"] == 2000
    assert result["user_remaining"] == 100
    assert result["blocking_errors"] == []


@override_settings(USER_DAILY_GENERATION_LIMIT=1, GENERATION_QUOTAS_ENABLED=False)
def test_confirm_generation_ignores_daily_quota_when_business_quota_is_disabled(tmp_path, settings):
    from platform_app.services import confirm_generation

    user, batch = make_batch_with_images(tmp_path, settings, count=2)
    user.daily_generation_limit = 1
    user.save(update_fields=["daily_generation_limit"])

    generations = confirm_generation(batch, user)

    assert len(generations) == 2


def test_confirm_generation_is_idempotent(tmp_path, settings):
    from platform_app.services import confirm_generation

    user, batch = make_batch_with_images(tmp_path, settings, count=2)

    first = confirm_generation(batch, user)
    second = confirm_generation(batch, user)

    assert [item.id for item in second] == [item.id for item in first]
    assert batch.generations.count() == 2


def test_ensure_cluster_generations_creates_detail_slots_only_after_completed_hero(tmp_path, settings):
    from platform_app.models import Generation, OutputSlot, OutputTemplate, PromptVersion, ResultAsset
    from platform_app.services import ensure_cluster_generations

    settings.MEDIA_ROOT = tmp_path
    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    for order in range(2, 10):
        OutputSlot.objects.create(template=template, name=f"Slot {order}", order=order)
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    for slot in template.slots.order_by("order"):
        PromptVersion.objects.create(
            cluster=cluster,
            created_by=user,
            output_slot=slot,
            prompt_text=f"Prompt {slot.order}",
            input_snapshot={"reference_snapshot": [batch.assets.first().storage_path]},
        )

    first = ensure_cluster_generations(cluster, user)
    second = ensure_cluster_generations(cluster, user)

    assert [item.id for item in second] == [item.id for item in first]
    assert list(cluster.generations.values_list("output_slot__order", flat=True)) == [1]

    hero = first[0]
    hero.status = Generation.Status.COMPLETED
    hero.save(update_fields=["status", "updated_at"])
    hero_path = f"results/{batch.id}/{cluster.id}/{hero.output_slot_id}/1/{hero.id}.png"
    ResultAsset.objects.create(
        generation=hero,
        storage_path=hero_path,
        sha256="1" * 64,
        file_size=10,
    )

    all_generations = ensure_cluster_generations(cluster, user)

    assert [item.output_slot.order for item in all_generations] == list(range(1, 10))
    detail_refs = [
        generation.reference_snapshot
        for generation in all_generations
        if generation.output_slot.order == 2
    ][0]
    assert hero_path in detail_refs


def test_archived_product_cannot_create_generations(tmp_path, settings):
    from django.utils import timezone

    from platform_app.services import ensure_cluster_generations

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    cluster.archived_at = timezone.now()
    cluster.save(update_fields=["archived_at"])

    with pytest.raises(ValueError, match="archived"):
        ensure_cluster_generations(cluster, user)


@override_settings(USER_DAILY_GENERATION_LIMIT=1, GENERATION_QUOTAS_ENABLED=True)
def test_confirm_generation_rejects_user_quota(tmp_path, settings):
    from platform_app.services import confirm_generation

    user, batch = make_batch_with_images(tmp_path, settings, count=2)
    user.daily_generation_limit = 1
    user.save(update_fields=["daily_generation_limit"])

    with pytest.raises(ValueError, match="user daily quota"):
        confirm_generation(batch, user)


def test_worker_archives_result_and_marks_completed(tmp_path, settings):
    from platform_app.models import Generation, ResultAsset
    from platform_app.services import FakeAPIMartClient, LocalStorage, confirm_generation, process_generation_once

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    generation = confirm_generation(batch, user)[0]

    assert process_generation_once(FakeAPIMartClient(), LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()
    assert generation.status == Generation.Status.SUBMITTED

    assert process_generation_once(FakeAPIMartClient(), LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()
    assert generation.status == Generation.Status.COMPLETED
    assert ResultAsset.objects.filter(generation=generation).count() == 1


def test_worker_enqueues_detail_slots_after_hero_completion(tmp_path, settings):
    from platform_app.models import (
        Generation,
        OutputSlot,
        OutputTemplate,
        PromptNodeTemplate,
        PromptVersion,
    )
    from platform_app.services import FakeAPIMartClient, LocalStorage, ensure_cluster_generations, process_generation_once

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    for order in range(2, 10):
        OutputSlot.objects.create(template=template, name=f"Slot {order}", order=order)
    batch.output_template = template
    batch.save(update_fields=["output_template"])
    for slot in template.slots.order_by("order"):
        PromptVersion.objects.create(
            cluster=cluster,
            created_by=user,
            output_slot=slot,
            prompt_text=f"Prompt {slot.order}",
            input_snapshot={"reference_snapshot": [batch.assets.first().storage_path]},
        )

    ensure_cluster_generations(cluster, user)
    assert list(cluster.generations.values_list("output_slot__order", flat=True)) == [1]

    assert process_generation_once(FakeAPIMartClient(), LocalStorage(tmp_path)) == 1
    assert process_generation_once(FakeAPIMartClient(), LocalStorage(tmp_path)) == 1

    orders = list(cluster.generations.order_by("output_slot__order").values_list("output_slot__order", flat=True))
    statuses = list(cluster.generations.order_by("output_slot__order").values_list("status", flat=True))
    assert orders == list(range(1, 10))
    assert statuses == [Generation.Status.COMPLETED, *[Generation.Status.QUEUED] * 8]


def test_shopee_vn_preserves_source_then_generates_white_hero_and_seven_marketing_images(
    tmp_path, settings
):
    from django.core.management import call_command

    from platform_app.models import Generation
    from platform_app.services import (
        FakeAPIMartClient,
        LocalStorage,
        create_project,
        ensure_cluster_generations,
        preflight_batch,
        process_generation_once,
        register_uploaded_asset,
    )

    settings.MEDIA_ROOT = tmp_path
    call_command("seed_platform_templates")
    user = make_user("vn-operator")
    batch = create_project(
        owner=user,
        name="Shopee VN",
        platform="shopee",
        market="VN",
        seller_tier="general",
    )
    source_bytes = image_bytes()
    asset = register_uploaded_asset(batch, "seller-photo.png", source_bytes, "image/png")
    cluster = asset.clusters.get()

    assert preflight_batch(batch, user)["slot_count"] == 9
    assert preflight_batch(batch, user)["generation_count"] == 8

    initial = ensure_cluster_generations(cluster, user)

    assert [item.output_slot.order for item in initial] == [1, 2]
    source, hero = initial
    assert source.status == Generation.Status.COMPLETED
    assert source.prompt_version.provider_model == "none"
    source_result = source.result_assets.get()
    assert source_result.storage_path.startswith(f"results/{batch.id}/{cluster.id}/{source.output_slot_id}/1/")
    assert LocalStorage(tmp_path).read(source_result.storage_path) == source_bytes
    assert hero.status == Generation.Status.QUEUED

    assert process_generation_once(FakeAPIMartClient(), LocalStorage(tmp_path)) == 1
    assert process_generation_once(FakeAPIMartClient(), LocalStorage(tmp_path)) == 1

    generations = list(cluster.generations.order_by("output_slot__order"))
    assert [item.output_slot.order for item in generations] == list(range(1, 10))
    assert generations[1].status == Generation.Status.COMPLETED
    assert all(item.status == Generation.Status.QUEUED for item in generations[2:])
    white_result_path = generations[1].result_assets.get().storage_path
    assert all(white_result_path in item.reference_snapshot for item in generations[2:])


def test_submit_unknown_is_not_reposted_automatically(tmp_path, settings):
    from platform_app.models import Generation
    from platform_app.services import LocalStorage, SubmitUnknown, confirm_generation, process_generation_once

    class UnknownClient:
        def submit_generation(self, prompt, image_paths, size, resolution):
            raise SubmitUnknown("network timeout")

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    generation = confirm_generation(batch, user)[0]

    assert process_generation_once(UnknownClient(), LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()
    assert generation.status == Generation.Status.SUBMIT_UNKNOWN

    assert process_generation_once(UnknownClient(), LocalStorage(tmp_path)) == 0
    assert Generation.objects.count() == 1


def test_prompt_complexity_failure_creates_one_shorter_n9_retry(tmp_path, settings):
    import json

    from platform_app.models import (
        Generation,
        OutputSlot,
        OutputTemplate,
        PromptNodeTemplate,
        PromptVersion,
    )
    from platform_app.services import LocalStorage, process_generation_once

    class ComplexityClient:
        def submit_generation(self, prompt, image_paths, size, resolution):
            return "complex-task"

        def get_task(self, task_id):
            return {"status": "failed", "error_code": "prompt_complexity", "error": "prompt too complex"}

        def optimize_prompt(self, payload):
            assert "NODE N9" in payload["text"]
            return {
                "output_text": json.dumps(
                    {
                        "decision": "retry_with_simplified_prompt",
                        "simplified_prompt": "Keep the product unchanged in one clean scene.",
                        "visible_text_lines": [],
                    }
                ),
                "raw": {},
            }

    settings.MEDIA_ROOT = tmp_path
    PromptNodeTemplate.objects.create(
        node_name="N9",
        version="2.1.0",
        status=PromptNodeTemplate.Status.PUBLISHED,
        instruction="Complete failure simplifier instruction.",
    )
    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    detail = OutputSlot.objects.create(template=template, name="Detail", order=2)
    long_prompt = "Keep the exact product identity. " + ("Use a clean studio detail. " * 20)
    prompt = PromptVersion.objects.create(
        cluster=cluster,
        output_slot=detail,
        created_by=user,
        prompt_text=long_prompt,
        input_snapshot={"reference_snapshot": [batch.assets.first().storage_path]},
    )
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=detail,
        prompt_version=prompt,
        created_by=user,
        status=Generation.Status.SUBMITTED,
        provider_task_id="complex-task",
        prompt_text=long_prompt,
        reference_snapshot=[batch.assets.first().storage_path],
    )

    assert process_generation_once(ComplexityClient(), LocalStorage(tmp_path)) == 1

    generation.refresh_from_db()
    retry = Generation.objects.exclude(id=generation.id).get()
    assert generation.status == Generation.Status.FAILED
    assert retry.status == Generation.Status.QUEUED
    assert retry.prompt_version.node_name == "N9"
    assert retry.prompt_version.template_version == "2.1.0"
    assert len(retry.prompt_text) < len(generation.prompt_text)


def test_worker_rewrites_a_legacy_first_slot_prompt_before_paid_submission(tmp_path, settings):
    from platform_app.models import Generation, OutputTemplate, PromptVersion
    from platform_app.services import LocalStorage, process_generation_once

    class CapturingClient:
        def __init__(self):
            self.prompt = ""

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.prompt = prompt
            return "legacy-policy-task"

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    first_slot = template.slots.get(order=1)
    legacy_prompt = PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        prompt_text="Legacy queued prompt",
        input_snapshot={"reference_snapshot": [batch.assets.first().storage_path]},
    )
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=first_slot,
        prompt_version=legacy_prompt,
        created_by=user,
        status=Generation.Status.QUEUED,
        prompt_text=legacy_prompt.prompt_text,
        reference_snapshot=[batch.assets.first().storage_path],
    )

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()

    assert "Hero restrictions: no promotional text" in client.prompt
    assert generation.prompt_text == client.prompt
    assert generation.prompt_version_id != legacy_prompt.id
    assert generation.prompt_version.prompt_text == client.prompt


def test_worker_restores_the_prompt_version_as_the_canonical_hero_prompt(tmp_path, settings):
    from platform_app.models import Generation, OutputTemplate, PromptVersion
    from platform_app.services import LocalStorage, process_generation_once
    from platform_app.template_policy import STANDARD_PRODUCT_HERO_PROMPT_LINES

    class CapturingClient:
        def __init__(self):
            self.prompt = ""

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.prompt = prompt
            return "canonical-policy-task"

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    first_slot = OutputTemplate.objects.get(platform="global", site="").slots.get(order=1)
    canonical_prompt = "\n".join(("Canonical product prompt", *STANDARD_PRODUCT_HERO_PROMPT_LINES))
    prompt_version = PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        prompt_text=canonical_prompt,
        input_snapshot={"standard_product_hero": True},
    )
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=first_slot,
        prompt_version=prompt_version,
        created_by=user,
        status=Generation.Status.QUEUED,
        prompt_text="Lifestyle campaign with a sale headline",
    )

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()

    assert client.prompt == canonical_prompt
    assert "Lifestyle campaign" not in client.prompt
    assert generation.prompt_text == canonical_prompt
    assert generation.prompt_version_id == prompt_version.id


def test_generation_cannot_be_reassigned_away_from_its_hero_before_submission(tmp_path, settings):
    from django.core.exceptions import ValidationError

    from platform_app.models import Generation, OutputSlot, OutputTemplate, PromptVersion
    from platform_app.services import LocalStorage, process_generation_once
    from platform_app.template_policy import STANDARD_PRODUCT_HERO_PROMPT_LINES

    class CapturingClient:
        def __init__(self):
            self.prompt = ""

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.prompt = prompt
            return "locked-hero-task"

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    hero_slot = template.slots.get(order=1)
    detail_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    canonical_prompt = "\n".join(("Canonical product prompt", *STANDARD_PRODUCT_HERO_PROMPT_LINES))
    hero_prompt = PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        prompt_text=canonical_prompt,
        input_snapshot={"standard_product_hero": True},
    )
    detail_prompt = PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        prompt_text="Lifestyle campaign with a sale headline",
    )
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=hero_slot,
        prompt_version=hero_prompt,
        created_by=user,
        status=Generation.Status.QUEUED,
        prompt_text=canonical_prompt,
    )

    generation.output_slot = detail_slot
    with pytest.raises(ValidationError, match="immutable"):
        generation.save()

    generation.refresh_from_db()
    generation.prompt_version = detail_prompt
    with pytest.raises(ValidationError, match="immutable"):
        generation.save()

    with pytest.raises(ValidationError, match="immutable"):
        Generation.objects.filter(id=generation.id).update(output_slot=detail_slot)

    with pytest.raises(ValidationError, match="immutable"):
        Generation.objects.filter(id=generation.id).update(prompt_version=detail_prompt)

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()

    assert client.prompt == canonical_prompt
    assert generation.output_slot_id == hero_slot.id
    assert generation.prompt_version_id == hero_prompt.id


def test_used_prompt_version_cannot_be_mutated_before_hero_submission(tmp_path, settings):
    from django.core.exceptions import ValidationError

    from platform_app.models import Generation, OutputTemplate, PromptVersion
    from platform_app.services import LocalStorage, process_generation_once
    from platform_app.template_policy import STANDARD_PRODUCT_HERO_PROMPT_LINES

    class CapturingClient:
        def __init__(self):
            self.prompt = ""

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.prompt = prompt
            return "immutable-prompt-task"

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    hero_slot = OutputTemplate.objects.get(platform="global", site="").slots.get(order=1)
    canonical_prompt = "\n".join(("Canonical product prompt", *STANDARD_PRODUCT_HERO_PROMPT_LINES))
    prompt_version = PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        prompt_text=canonical_prompt,
        input_snapshot={"standard_product_hero": True},
    )
    unused_prompt = PromptVersion.objects.create(
        cluster=cluster,
        created_by=user,
        prompt_text="Editable draft",
    )
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=hero_slot,
        prompt_version=prompt_version,
        created_by=user,
        status=Generation.Status.QUEUED,
        prompt_text=canonical_prompt,
    )

    with pytest.raises(ValidationError, match="immutable"):
        PromptVersion.objects.filter(id=prompt_version.id).update(prompt_text="Lifestyle campaign")
    with pytest.raises(ValidationError, match="immutable"):
        PromptVersion.objects.filter(id=prompt_version.id).update(input_snapshot={"sale": True})

    PromptVersion.objects.filter(id=unused_prompt.id).update(prompt_text="Editable revision")
    unused_prompt.refresh_from_db()
    assert unused_prompt.prompt_text == "Editable revision"

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()

    assert client.prompt == canonical_prompt
    assert generation.prompt_version_id == prompt_version.id


def test_worker_rejects_a_queued_detail_image_without_its_required_hero(tmp_path, settings):
    from platform_app.models import Generation, OutputSlot, OutputTemplate
    from platform_app.services import LocalStorage, process_generation_once

    class CapturingClient:
        def __init__(self):
            self.calls = 0

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.calls += 1
            return "unexpected-detail-task"

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    detail_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    generation = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=detail_slot,
        created_by=user,
        status=Generation.Status.QUEUED,
        prompt_text="A direct detail image",
    )

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    generation.refresh_from_db()

    assert client.calls == 0
    assert generation.status == Generation.Status.FAILED
    assert "standard product hero" in generation.failure_reason.lower()


def test_worker_requires_a_completed_hero_from_the_same_template_before_detail_submission(tmp_path, settings):
    from platform_app.models import Generation, OutputSlot, OutputTemplate
    from platform_app.services import LocalStorage, process_generation_once

    class CapturingClient:
        def __init__(self):
            self.calls = 0

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.calls += 1
            return f"detail-task-{self.calls}"

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    hero_slot = template.slots.get(order=1)
    detail_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    other_template = OutputTemplate.objects.create(platform="global", name="Other template")
    other_hero_slot = OutputSlot.objects.create(template=other_template, name="Hero", order=1)
    client = CapturingClient()

    Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=other_hero_slot,
        created_by=user,
        status=Generation.Status.COMPLETED,
    )
    wrong_template_detail = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=detail_slot,
        created_by=user,
        status=Generation.Status.QUEUED,
        prompt_text="Detail after wrong-template hero",
    )
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    wrong_template_detail.refresh_from_db()
    assert client.calls == 0
    assert wrong_template_detail.status == Generation.Status.FAILED

    Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=hero_slot,
        created_by=user,
        status=Generation.Status.COMPLETED,
    )
    completed_hero_detail = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=detail_slot,
        created_by=user,
        attempt=2,
        status=Generation.Status.QUEUED,
        prompt_text="Detail after completed hero",
    )
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    completed_hero_detail.refresh_from_db()
    assert client.calls == 1
    assert completed_hero_detail.status == Generation.Status.SUBMITTED


@pytest.mark.parametrize("hero_status", ["failed", "canceled"])
def test_worker_keeps_detail_queued_until_a_failed_or_canceled_hero_is_redone(tmp_path, settings, hero_status):
    from platform_app.models import Generation, OutputSlot, OutputTemplate
    from platform_app.services import LocalStorage, process_generation_once

    class CapturingClient:
        def __init__(self):
            self.calls = 0

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.calls += 1
            return f"detail-task-{self.calls}"

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    hero_slot = template.slots.get(order=1)
    detail_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    client = CapturingClient()
    hero = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=hero_slot,
        created_by=user,
        status=hero_status,
    )
    detail = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=detail_slot,
        created_by=user,
        status=Generation.Status.QUEUED,
        prompt_text="Detail after hero needs a redo",
    )

    assert process_generation_once(client, LocalStorage(tmp_path)) == 0
    detail.refresh_from_db()
    assert client.calls == 0
    assert detail.status == Generation.Status.QUEUED

    retry = hero.retry_failed(user)
    assert retry.attempt == 2
    assert retry.status == Generation.Status.QUEUED
    retry.status = Generation.Status.COMPLETED
    retry.save(update_fields=["status", "updated_at"])
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    detail.refresh_from_db()
    assert client.calls == 1
    assert detail.status == Generation.Status.SUBMITTED


def test_worker_defers_detail_until_hero_completes_without_blocking_hero_or_polling(tmp_path, settings):
    from platform_app.models import Generation, OutputSlot, OutputTemplate
    from platform_app.services import LocalStorage, process_generation_once

    class CapturingClient:
        def __init__(self):
            self.submitted_prompts = []
            self.polls = 0

        def submit_generation(self, prompt, image_paths, size, resolution):
            self.submitted_prompts.append(prompt)
            return "hero-task"

        def get_task(self, task_id):
            self.polls += 1
            return {"status": "processing"}

    user, batch = make_batch_with_images(tmp_path, settings, count=1)
    cluster = batch.clusters.get()
    template = OutputTemplate.objects.get(platform="global", site="")
    hero_slot = template.slots.get(order=1)
    detail_slot = OutputSlot.objects.create(template=template, name="Detail", order=2)
    detail = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=detail_slot,
        created_by=user,
        status=Generation.Status.QUEUED,
        prompt_text="DETAIL PROMPT",
    )
    hero = Generation.objects.create(
        batch=batch,
        cluster=cluster,
        output_slot=hero_slot,
        created_by=user,
        status=Generation.Status.QUEUED,
        prompt_text="HERO PROMPT",
    )

    client = CapturingClient()
    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    detail.refresh_from_db()
    hero.refresh_from_db()
    assert detail.status == Generation.Status.QUEUED
    assert hero.status == Generation.Status.SUBMITTED
    assert client.submitted_prompts[0].startswith("HERO PROMPT")

    assert process_generation_once(client, LocalStorage(tmp_path)) == 1
    detail.refresh_from_db()
    hero.refresh_from_db()
    assert detail.status == Generation.Status.QUEUED
    assert hero.status == Generation.Status.PROCESSING
    assert client.polls == 1
