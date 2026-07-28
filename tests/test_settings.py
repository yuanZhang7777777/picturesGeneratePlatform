from pathlib import Path


def test_settings_import_in_fake_mode(monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "test-secret-key-for-settings")
    monkeypatch.setenv("USE_SQLITE_FOR_TESTS", "1")
    monkeypatch.setenv("APIMART_FAKE_MODE", "1")

    from image_platform import settings

    assert settings.APIMART_FAKE_MODE is True
    assert settings.STORAGE_BACKEND == "local"
    assert settings.ORG_DAILY_GENERATION_LIMIT == 2000


def test_env_example_contains_no_live_secret():
    content = Path(".env.example").read_text(encoding="utf-8")

    forbidden = ["sk-", "AccessKey Secret", "LTAI", "oss-cn-shanghai.aliyuncs.com"]
    assert not any(marker in content for marker in forbidden)
    assert "APIMART_API_KEY=" in content
