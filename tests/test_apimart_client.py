from pathlib import Path
from io import StringIO

import pytest
import requests
from django.core.management import call_command
from django.core.management.base import CommandError
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


class TimeoutSession:
    def post(self, url, **kwargs):
        raise requests.ReadTimeout("read timeout=20")


@override_settings(APIMART_API_KEY="secret-key", APIMART_BASE_URL="https://api.apimart.ai")
def test_upload_image_posts_multipart_file_and_returns_url(tmp_path):
    from platform_app.services import APIMartClient

    image = tmp_path / "ref.png"
    image.write_bytes(b"image-bytes")
    session = Session([Response(200, {"url": "https://upload.apimart.ai/f/image/ref.png"})])
    client = APIMartClient(session=session)

    uploaded_url = client.upload_image(str(image))

    method, url, kwargs = session.calls[0]
    assert uploaded_url == "https://upload.apimart.ai/f/image/ref.png"
    assert method == "POST"
    assert url == "https://api.apimart.ai/v1/uploads/images"
    assert kwargs["headers"] == {"Authorization": "Bearer secret-key"}
    assert "file" in kwargs["files"]


@override_settings(
    APIMART_API_KEY="secret-key",
    APIMART_BASE_URL="https://api.apimart.ai",
    APIMART_IMAGE_MODEL="gpt-image-2",
)
def test_submit_generation_uploads_references_then_posts_image_urls(tmp_path):
    from platform_app.services import APIMartClient

    image = tmp_path / "ref.png"
    image.write_bytes(b"image-bytes")
    session = Session(
        [
            Response(200, {"url": "https://upload.apimart.ai/f/image/ref.png"}),
            Response(200, {"code": 200, "data": [{"task_id": "task_1"}]}),
        ]
    )
    client = APIMartClient(session=session)

    task_id = client.submit_generation("prompt", [str(image)], "1:1", "1k")

    method, url, kwargs = session.calls[1]
    assert task_id == "task_1"
    assert method == "POST"
    assert url == "https://api.apimart.ai/v1/images/generations"
    assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
    assert kwargs["json"]["model"] == "gpt-image-2"
    assert kwargs["json"]["n"] == 1
    assert kwargs["json"]["image_urls"] == ["https://upload.apimart.ai/f/image/ref.png"]


@override_settings(APIMART_API_KEY="secret-key", APIMART_BASE_URL="https://api.apimart.ai/v1")
def test_submit_generation_accepts_versioned_base_url(tmp_path):
    from platform_app.services import APIMartClient

    image = tmp_path / "ref.png"
    image.write_bytes(b"image-bytes")
    session = Session(
        [
            Response(200, {"url": "https://upload.apimart.ai/f/image/ref.png"}),
            Response(200, {"code": 200, "data": [{"task_id": "task_1"}]}),
        ]
    )
    client = APIMartClient(session=session)

    client.submit_generation("prompt", [str(image)], "1:1", "1k")

    assert session.calls[0][1] == "https://api.apimart.ai/v1/uploads/images"
    assert session.calls[1][1] == "https://api.apimart.ai/v1/images/generations"


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
    DEEPSEEK_API_KEY="deepseek-key",
    DEEPSEEK_BASE_URL="https://api.deepseek.com",
    DEEPSEEK_PROMPT_MODEL="deepseek-v4-flash",
    APIMART_BASE_URL="https://api.apimart.ai",
    APIMART_PROMPT_TEMPERATURE=1.6,
    APIMART_PROMPT_TIMEOUT_SECONDS=20,
)
def test_optimize_prompt_posts_official_deepseek_flash_payload():
    from platform_app.services import APIMartClient

    session = Session(
        [
            Response(
                200,
                {
                    "id": "chat_1",
                    "choices": [{"message": {"content": "{\"suggested_prompt\":\"ok\"}"}}],
                },
            )
        ]
    )
    client = APIMartClient(session=session)

    data = client.optimize_prompt(
        {
            "system": "You are the complete production node instruction.",
            "text": "make prompt",
        }
    )

    assert data["output_text"] == "{\"suggested_prompt\":\"ok\"}"
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://api.deepseek.com/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer deepseek-key"
    assert kwargs["json"]["model"] == "deepseek-v4-flash"
    assert kwargs["json"]["stream"] is False
    assert "temperature" not in kwargs["json"]
    assert kwargs["json"]["reasoning_effort"] == "high"
    assert kwargs["json"]["thinking"] == {"type": "enabled"}
    assert kwargs["timeout"] == 20
    assert kwargs["json"]["messages"][0] == {
        "role": "system",
        "content": "You are the complete production node instruction.",
    }


@pytest.mark.parametrize("temperature", [-0.1, 2.1])
@override_settings(
    DEEPSEEK_API_KEY="deepseek-key",
    DEEPSEEK_BASE_URL="https://api.deepseek.com",
    DEEPSEEK_PROMPT_MODEL="deepseek-v4-flash",
)
def test_optimize_prompt_rejects_explicit_temperature_outside_range(temperature):
    from platform_app.services import APIMartClient, ProviderError

    client = APIMartClient(session=Session([]))

    with pytest.raises(ProviderError, match="temperature"):
        client.optimize_prompt({"text": "make prompt", "temperature": temperature})


@override_settings(
    DEEPSEEK_API_KEY="deepseek-key",
    DEEPSEEK_BASE_URL="https://api.deepseek.com",
    DEEPSEEK_PROMPT_MODEL="deepseek-v4-flash",
    APIMART_PROMPT_TEMPERATURE=0.4,
)
def test_complete_chat_uses_explicit_temperature():
    from platform_app.services import APIMartClient

    session = Session(
        [
            Response(
                200,
                {"choices": [{"message": {"content": "{\"ok\": true}"}}]},
            )
        ]
    )
    client = APIMartClient(session=session)

    client.complete_chat([{"role": "user", "content": "hello"}], temperature=0.9)

    assert session.calls[0][2]["json"]["temperature"] == 0.9


@override_settings(
    DEEPSEEK_API_KEY="deepseek-key",
    DEEPSEEK_BASE_URL="https://api.deepseek.com",
    DEEPSEEK_PROMPT_MODEL="deepseek-v4-flash",
    APIMART_PROMPT_TEMPERATURE=0.4,
)
def test_complete_chat_reports_timeout_in_chinese():
    from platform_app.services import APIMartClient, ProviderError

    client = APIMartClient(session=TimeoutSession())

    with pytest.raises(ProviderError, match="模型服务响应超时，请重试预备生成"):
        client.complete_chat([{"role": "user", "content": "hello"}])


@override_settings(
    DEEPSEEK_API_KEY="deepseek-key",
    DEEPSEEK_BASE_URL="https://api.deepseek.com",
    DEEPSEEK_PROMPT_MODEL="deepseek-v4-flash",
    APIMART_PROMPT_TEMPERATURE=0.4,
)
def test_complete_chat_extracts_list_message_content():
    from platform_app.services import APIMartClient

    session = Session(
        [
            Response(
                200,
                {"choices": [{"message": {"content": [{"type": "text", "text": "节点返回文本"}]}}]},
            )
        ]
    )
    client = APIMartClient(session=session)

    data = client.complete_chat([{"role": "user", "content": "hello"}])

    assert data["output_text"] == "节点返回文本"


@override_settings(
    DEEPSEEK_API_KEY="deepseek-key",
    DEEPSEEK_BASE_URL="https://api.deepseek.com",
    DEEPSEEK_PROMPT_MODEL="deepseek-v4-flash",
    APIMART_PROMPT_TEMPERATURE=0.4,
)
def test_complete_chat_extracts_deepseek_reasoning_content_when_content_is_empty():
    from platform_app.services import APIMartClient

    session = Session(
        [
            Response(
                200,
                {"choices": [{"message": {"content": "", "reasoning_content": "节点返回文本"}}]},
            )
        ]
    )
    client = APIMartClient(session=session)

    data = client.complete_chat([{"role": "user", "content": "hello"}])

    assert data["output_text"] == "节点返回文本"


@override_settings(
    DEEPSEEK_API_KEY="deepseek-key",
    DEEPSEEK_BASE_URL="https://api.deepseek.com",
    DEEPSEEK_PROMPT_MODEL="deepseek-v4-flash",
    APIMART_PROMPT_TEMPERATURE=0.4,
)
def test_complete_chat_retries_once_when_deepseek_returns_empty_content():
    from platform_app.services import APIMartClient

    session = Session(
        [
            Response(200, {"choices": [{"message": {"content": ""}}]}),
            Response(200, {"choices": [{"message": {"content": "第二次返回文本"}}]}),
        ]
    )
    client = APIMartClient(session=session)

    data = client.complete_chat([{"role": "user", "content": "hello"}])

    assert data["output_text"] == "第二次返回文本"
    assert len(session.calls) == 2


@override_settings(DEEPSEEK_API_KEY="")
def test_complete_chat_reports_missing_deepseek_key_in_chinese():
    from platform_app.services import APIMartClient, ProviderError

    client = APIMartClient(session=Session([]))

    with pytest.raises(ProviderError, match="DeepSeek 官方接口密钥未配置"):
        client.complete_chat([{"role": "user", "content": "hello"}])


@pytest.mark.parametrize("temperature", [-0.1, 2.1])
@override_settings(
    DEEPSEEK_API_KEY="deepseek-key",
    DEEPSEEK_BASE_URL="https://api.deepseek.com",
    DEEPSEEK_PROMPT_MODEL="deepseek-v4-flash",
    APIMART_PROMPT_TEMPERATURE=0.4,
)
def test_complete_chat_rejects_explicit_temperature_outside_range(temperature):
    from platform_app.services import APIMartClient, ProviderError

    client = APIMartClient(session=Session([]))

    with pytest.raises(ProviderError, match="temperature"):
        client.complete_chat([{"role": "user", "content": "hello"}], temperature=temperature)


@override_settings(
    APIMART_API_KEY="secret-key",
    APIMART_BASE_URL="https://api.apimart.ai/v1",
    APIMART_VISION_MODEL="gpt-5-nano-2025-08-07",
)
def test_observe_images_uploads_images_and_posts_responses_payload(tmp_path):
    from platform_app.services import APIMartClient

    image = tmp_path / "source.png"
    image.write_bytes(b"image-bytes")
    session = Session(
        [
            Response(200, {"url": "https://upload.apimart.ai/f/image/source.png"}),
            Response(200, {"id": "resp_1", "output_text": "{\"category\":\"cup\"}"}),
        ]
    )
    client = APIMartClient(session=session)

    data = client.observe_images("Return strict JSON.", [str(image)])

    assert data["output_text"] == "{\"category\":\"cup\"}"
    method, url, kwargs = session.calls[1]
    assert method == "POST"
    assert url == "https://api.apimart.ai/v1/responses"
    assert kwargs["json"]["model"] == "gpt-5-nano-2025-08-07"
    assert kwargs["json"]["input"][0]["content"] == [
        {"type": "input_text", "text": "Return strict JSON."},
        {"type": "input_image", "image_url": "https://upload.apimart.ai/f/image/source.png"},
    ]


@override_settings(
    APIMART_API_KEY="secret-key",
    APIMART_BASE_URL="https://api.apimart.ai/v1",
    APIMART_VISION_MODEL="gpt-5-nano-2025-08-07",
)
def test_observe_images_extracts_nested_responses_text(tmp_path):
    from platform_app.services import APIMartClient

    image = tmp_path / "source.png"
    image.write_bytes(b"image-bytes")
    session = Session(
        [
            Response(200, {"url": "https://upload.apimart.ai/f/image/source.png"}),
            Response(
                200,
                {
                    "id": "resp_1",
                    "output": [
                        {"type": "reasoning", "content": []},
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "{\"visible_object\":\"red block\"}",
                                }
                            ],
                        },
                    ],
                },
            ),
        ]
    )
    client = APIMartClient(session=session)

    data = client.observe_images("Return strict JSON.", [str(image)])

    assert data["output_text"] == "{\"visible_object\":\"red block\"}"


@override_settings(APIMART_API_KEY="secret-key")
def test_download_image_returns_raw_bytes():
    from platform_app.services import APIMartClient

    session = Session([Response(200, content=b"png-bytes")])
    client = APIMartClient(session=session)

    assert client.download_image("https://example.test/result.png") == b"png-bytes"


def test_provider_status_normalizes_real_apimart_pending_and_cancelled():
    from platform_app.models import Generation
    from platform_app.services import _normalize_provider_status

    assert _normalize_provider_status({"status": "pending"}) == Generation.Status.PROCESSING
    assert _normalize_provider_status({"status": "cancelled"}) == Generation.Status.FAILED


def test_image_urls_normalizes_strings_objects_and_nested_results():
    from platform_app.services import _image_urls

    assert _image_urls({"image_urls": ["https://example.test/a.png"]}) == [
        "https://example.test/a.png"
    ]
    assert _image_urls({"image_urls": [{"url": "https://example.test/b.png"}]}) == [
        "https://example.test/b.png"
    ]
    assert _image_urls(
        {"result": {"images": [{"url": ["https://example.test/c.png"]}]}}
    ) == ["https://example.test/c.png"]


@override_settings(APIMART_FAKE_MODE=True, APIMART_API_KEY="secret-key")
def test_smoke_apimart_nodes_fake_mode_outputs_only_sanitized_statuses():
    stdout = StringIO()

    call_command("smoke_apimart_nodes", stdout=stdout)

    output = stdout.getvalue()
    assert "vision status=ok" in output
    assert "prompt status=ok" in output
    assert "image status=completed" in output
    assert "sha256=" in output
    assert "secret-key" not in output
    assert "fake://" not in output
    assert "{\"" not in output


@override_settings(APIMART_FAKE_MODE=False, APIMART_API_KEY="fake-test-key-123")
def test_smoke_apimart_nodes_rejects_fake_key_without_leaking_it():
    stdout = StringIO()

    with pytest.raises(CommandError, match="invalid APIMart API key"):
        call_command("smoke_apimart_nodes", stdout=stdout)

    assert "fake-test-key-123" not in stdout.getvalue()
