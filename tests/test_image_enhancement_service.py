import base64
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.core.config import Settings
from app.services.image_enhancement_service import ImageEnhancementService


class ImageEnhancementServiceTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _settings(**overrides: object) -> Settings:
        values = {
            "openrouter_api_key": "test-key",
            "openrouter_max_retries": 0,
            "openrouter_image_model": "openai/gpt-image-1-mini",
            "openrouter_image_enhancement_enabled": True,
            **overrides,
        }
        return Settings(_env_file=None, **values)

    async def test_enhance_sends_reference_image_and_decodes_result(self) -> None:
        enhanced = b"enhanced-png"
        response = httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(enhanced).decode("ascii")}]},
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/images"),
        )

        with patch("app.services.image_enhancement_service.httpx.AsyncClient") as client_class:
            client = AsyncMock()
            client.post.return_value = response
            client_class.return_value.__aenter__.return_value = client

            result = await ImageEnhancementService(self._settings()).enhance(
                b"original-jpeg",
                "image/jpeg",
            )

        self.assertEqual(result, (enhanced, "image/png"))
        payload = client.post.await_args.kwargs["json"]
        self.assertEqual(payload["model"], "openai/gpt-image-1-mini")
        self.assertEqual(payload["input_references"][0]["type"], "image_url")
        self.assertTrue(
            payload["input_references"][0]["image_url"]["url"].startswith(
                "data:image/jpeg;base64,",
            )
        )
        self.assertEqual(payload["quality"], "high")

    async def test_enhance_prefers_us_api_key_when_configured(self) -> None:
        enhanced = b"enhanced-png"
        response = httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(enhanced).decode("ascii")}]},
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/images"),
        )

        with patch("app.services.image_enhancement_service.httpx.AsyncClient") as client_class:
            client = AsyncMock()
            client.post.return_value = response
            client_class.return_value.__aenter__.return_value = client

            await ImageEnhancementService(
                self._settings(
                    openrouter_api_key="default-key",
                    openrouter_api_key_us="us-key",
                )
            ).enhance(b"original-jpeg", "image/jpeg")

        headers = client.post.await_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer us-key")

    async def test_disabled_enhancement_returns_original_without_request(self) -> None:
        service = ImageEnhancementService(
            self._settings(openrouter_image_enhancement_enabled=False),
        )

        with patch("app.services.image_enhancement_service.httpx.AsyncClient") as client_class:
            result = await service.enhance_or_original(b"original", "image/webp")

        self.assertEqual(result, (b"original", "image/webp"))
        client_class.assert_not_called()

    async def test_provider_error_falls_back_to_original(self) -> None:
        response = httpx.Response(
            400,
            text="unsupported input",
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/images"),
        )

        with patch("app.services.image_enhancement_service.httpx.AsyncClient") as client_class:
            client = AsyncMock()
            client.post.return_value = response
            client_class.return_value.__aenter__.return_value = client

            result = await ImageEnhancementService(self._settings()).enhance_or_original(
                b"original",
                "image/png",
            )

        self.assertEqual(result, (b"original", "image/png"))


if __name__ == "__main__":
    unittest.main()
