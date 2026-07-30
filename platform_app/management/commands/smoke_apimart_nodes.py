import hashlib
import json
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image

from platform_app.services import APIMartClient, FakeAPIMartClient, ProviderError, _image_urls


class Command(BaseCommand):
    help = "Smoke test APIMart vision, prompt, and image nodes without printing secrets or raw responses."

    def handle(self, *args, **options):
        if not settings.APIMART_FAKE_MODE and _looks_fake_key(settings.APIMART_API_KEY):
            raise CommandError("invalid APIMart API key")

        client = FakeAPIMartClient() if settings.APIMART_FAKE_MODE else APIMartClient()
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "smoke-product.png"
            Image.new("RGB", (32, 32), "white").save(image_path, "PNG")
            try:
                self._smoke_vision(client, image_path)
                self._smoke_prompt(client)
                self._smoke_image(client, image_path)
            except ProviderError as exc:
                raise CommandError(str(exc)) from exc

    def _smoke_vision(self, client, image_path):
        started = time.monotonic()
        result = client.observe_images("Return strict JSON describing this plain test product image.", [str(image_path)])
        self.stdout.write(
            _line("vision", "ok", started, result.get("output_text", ""))
        )

    def _smoke_prompt(self, client):
        started = time.monotonic()
        result = client.optimize_prompt({"text": "Return JSON for a plain white ecommerce product image."})
        self.stdout.write(
            _line("prompt", "ok", started, result.get("output_text", ""))
        )

    def _smoke_image(self, client, image_path):
        started = time.monotonic()
        task_id = client.submit_generation(
            "Plain product photo on a pure white background. No text, no watermark.",
            [str(image_path)],
            "1:1",
            "1k",
        )
        payload = client.get_task(task_id)
        status = str(payload.get("status") or "unknown")
        digest_source = json.dumps({"status": status}, sort_keys=True)
        urls = _image_urls(payload)
        if urls:
            digest_source = client.download_image(urls[0])
        self.stdout.write(_line("image", status, started, digest_source))


def _line(node, status, started, value):
    data = value if isinstance(value, bytes) else str(value).encode("utf-8", errors="replace")
    return f"{node} status={status} elapsed_ms={int((time.monotonic() - started) * 1000)} sha256={hashlib.sha256(data).hexdigest()}"


def _looks_fake_key(value):
    key = str(value or "").strip().lower()
    return not key or key.startswith(("fake", "test", "dummy", "replace-with"))
