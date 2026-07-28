from pathlib import Path

import pytest
from django.test import override_settings


class Response:
    def __init__(self, status_code, payload=None, content=b"", headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)


@override_settings(APIMART_API_KEY="secret-key", APIMART_BASE_URL="https://api.apimart.ai")
def test_submit_generation_posts_official_payload(tmp_path):
    from platform_app.services import APIMartClient

    image = tmp_path / "ref.png"
    image.write_bytes(b"image-bytes")
    session = Session([Response(200, {"code": 200, "data": [{"task_id": "task_1"}]})])
    client = APIMartClient(session=session)

    task_id = client.submit_generation("prompt", [str(image)], "1:1", "1k")

    method, url, kwargs = session.calls[0]
    assert task_id == "task_1"
    assert method == "POST"
    assert url == "https://api.apimart.ai/v1/images/generations"
    assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
    assert kwargs["json"]["model"] == "gpt-image-2"
    assert kwargs["json"]["n"] == 1
    assert kwargs["json"]["image_urls"][0].startswith("data:image/png;base64,")


@override_settings(APIMART_API_KEY="secret-key", APIMART_BASE_URL="https://api.apimart.ai")
def test_get_task_returns_data_object():
    from platform_app.services import APIMartClient

    session = Session([Response(200, {"code": 200, "data": {"status": "completed"}})])
    client = APIMartClient(session=session)

    data = client.get_task("task_1")

    assert data == {"status": "completed"}
    assert session.calls[0][1] == "https://api.apimart.ai/v1/tasks/task_1"
    assert session.calls[0][2]["params"] == {"language": "zh"}


@override_settings(APIMART_API_KEY="secret-key", APIMART_BASE_URL="https://api.apimart.ai")
def test_rate_limit_error_is_sanitized():
    from platform_app.services import APIMartClient, RateLimited

    session = Session([Response(429, {"error": {"message": "slow down secret-key"}})])
    client = APIMartClient(session=session)

    with pytest.raises(RateLimited) as excinfo:
        client.get_task("task_1")

    assert "secret-key" not in str(excinfo.value)


@override_settings(
    APIMART_API_KEY="secret-key",
    APIMART_BASE_URL="https://api.apimart.ai",
    APIMART_PROMPT_MODEL="gpt-5.2-pro",
)
def test_optimize_prompt_posts_responses_payload():
    from platform_app.services import APIMartClient

    session = Session([Response(200, {"id": "resp_1", "output_text": "{\"ok\": true}"})])
    client = APIMartClient(session=session)

    data = client.optimize_prompt({"text": "make prompt"})

    assert data["id"] == "resp_1"
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://api.apimart.ai/v1/responses"
    assert kwargs["json"]["model"] == "gpt-5.2-pro"
    assert kwargs["json"]["input"][0]["content"][0]["type"] == "input_text"


@override_settings(APIMART_API_KEY="secret-key")
def test_download_image_returns_raw_bytes():
    from platform_app.services import APIMartClient

    session = Session([Response(200, content=b"png-bytes")])
    client = APIMartClient(session=session)

    assert client.download_image("https://example.test/result.png") == b"png-bytes"
