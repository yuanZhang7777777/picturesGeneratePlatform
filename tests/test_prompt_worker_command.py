from io import StringIO

from django.core.management import call_command
import pytest


def test_prompt_worker_once_uses_configured_concurrency(monkeypatch):
    from platform_app.management.commands import run_prompt_worker

    calls = []

    def fake_process_once():
        calls.append(1)
        return 1

    monkeypatch.setattr(run_prompt_worker, "process_prompt_once", fake_process_once)
    stdout = StringIO()

    call_command("run_prompt_worker", "--once", "--concurrency", "3", stdout=stdout)

    assert len(calls) == 3
    assert "processed=3" in stdout.getvalue()


@pytest.mark.django_db
def test_prompt_claims_distinct_pending_clusters():
    from django.contrib.auth import get_user_model

    from platform_app.models import Batch, Cluster
    from platform_app.services import _claim_next_prompt_cluster

    user = get_user_model().objects.create_user(
        username="prompt-worker",
        password="long-enough-password",
        must_change_password=False,
    )
    batch = Batch.objects.create(owner=user, name="Parallel prompt")
    first = Cluster.objects.create(batch=batch, name="First", preparation_status=Cluster.PreparationStatus.PENDING)
    second = Cluster.objects.create(batch=batch, name="Second", preparation_status=Cluster.PreparationStatus.PENDING)

    claimed_ids = {_claim_next_prompt_cluster().id, _claim_next_prompt_cluster().id}

    assert claimed_ids == {first.id, second.id}
    assert Cluster.objects.filter(preparation_status=Cluster.PreparationStatus.PREPARING).count() == 2


@pytest.mark.django_db
def test_prompt_claim_locks_cluster_without_related_joins(monkeypatch):
    from django.contrib.auth import get_user_model
    from django.db import connection
    from django.db.models.query import QuerySet

    from platform_app.models import Batch, Cluster
    from platform_app.services import _claim_next_prompt_cluster

    user = get_user_model().objects.create_user(
        username="prompt-worker-join",
        password="long-enough-password",
        must_change_password=False,
    )
    batch = Batch.objects.create(owner=user, name="No nullable lock join", output_template=None)
    cluster = Cluster.objects.create(batch=batch, name="Pending", preparation_status=Cluster.PreparationStatus.PENDING)
    original = QuerySet.select_for_update

    def guarded_select_for_update(self, *args, **kwargs):
        if self.model is Cluster and self.query.select_related:
            raise AssertionError("prompt claim must lock Cluster rows without select_related joins")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(connection.features, "has_select_for_update_skip_locked", True)
    monkeypatch.setattr(QuerySet, "select_for_update", guarded_select_for_update)

    assert _claim_next_prompt_cluster().id == cluster.id
