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


def test_confirm_generation_is_idempotent(tmp_path, settings):
    from platform_app.services import confirm_generation

    user, batch = make_batch_with_images(tmp_path, settings, count=2)

    first = confirm_generation(batch, user)
    second = confirm_generation(batch, user)

    assert [item.id for item in second] == [item.id for item in first]
    assert batch.generations.count() == 2


@override_settings(USER_DAILY_GENERATION_LIMIT=1)
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
