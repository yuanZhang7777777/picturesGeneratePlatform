from io import StringIO

from django.core.management import call_command


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
