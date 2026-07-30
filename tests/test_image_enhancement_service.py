import base64
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.core.config import Settings
from app.core.exceptions import OpenRouterError
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

    async def test_gemini_request_is_pinned_to_vertex_without_fallback(self) -> None:
        response = httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(b"enhanced").decode("ascii")}]},
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/images"),
        )
        with patch("app.services.image_enhancement_service.httpx.AsyncClient") as client_class:
            client = AsyncMock()
            client.post.return_value = response
            client_class.return_value.__aenter__.return_value = client
            await ImageEnhancementService(
                self._settings(
                    openrouter_image_model="google/gemini-3.1-flash-image",
                    openrouter_image_provider="google-vertex/global",
                )
            ).enhance(b"original", "image/jpeg")

        payload = client.post.await_args.kwargs["json"]
        self.assertEqual(
            payload["provider"],
            {"only": ["google-vertex/global"], "allow_fallbacks": False},
        )

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

            result = await ImageEnhancementService(
                self._settings(openrouter_image_max_attempts=1),
            ).enhance_or_original(
                b"original",
                "image/png",
            )

        self.assertEqual(result, (b"original", "image/png"))
        self.assertEqual(client.post.await_count, 1)

    async def test_transient_errors_use_only_image_attempt_budget(self) -> None:
        responses = [
            httpx.Response(
                503,
                text="busy",
                request=httpx.Request("POST", "https://openrouter.ai/api/v1/images"),
            ),
            httpx.Response(
                503,
                text="busy",
                request=httpx.Request("POST", "https://openrouter.ai/api/v1/images"),
            ),
            httpx.Response(
                200,
                json={
                    "data": [
                        {"b64_json": base64.b64encode(b"enhanced").decode("ascii")}
                    ]
                },
                request=httpx.Request("POST", "https://openrouter.ai/api/v1/images"),
            ),
        ]
        with (
            patch("app.services.image_enhancement_service.httpx.AsyncClient") as client_class,
            patch(
                "app.services.image_enhancement_service.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            client = AsyncMock()
            client.post.side_effect = responses
            client_class.return_value.__aenter__.return_value = client
            result = await ImageEnhancementService(
                self._settings(
                    openrouter_max_retries=10,
                    openrouter_image_max_attempts=3,
                )
            ).enhance_or_original_with_status(b"original", "image/jpeg")

        self.assertEqual(result, (b"enhanced", "image/png", True))
        self.assertEqual(client.post.await_count, 3)

    async def test_gemini_no_image_response_is_retried(self) -> None:
        service = ImageEnhancementService(
            self._settings(openrouter_image_max_attempts=2),
        )
        service.enhance = AsyncMock(
            side_effect=[
                OpenRouterError(
                    "Gemini returned no image data (finish_reason: STOP)",
                    status_code=400,
                ),
                (b"enhanced", "image/png"),
            ]
        )
        with patch(
            "app.services.image_enhancement_service.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await service.enhance_or_original_with_status(
                b"original",
                "image/jpeg",
            )

        self.assertEqual(result, (b"enhanced", "image/png", True))
        self.assertEqual(service.enhance.await_count, 2)

    async def test_status_reports_successful_enhancement(self) -> None:
        service = ImageEnhancementService(self._settings())
        service.enhance = AsyncMock(return_value=(b"enhanced", "image/png"))

        result = await service.enhance_or_original_with_status(
            b"original",
            "image/jpeg",
        )

        self.assertEqual(result, (b"enhanced", "image/png", True))

    async def test_retries_before_falling_back_to_original(self) -> None:
        service = ImageEnhancementService(
            self._settings(openrouter_image_max_attempts=3),
        )
        service.enhance = AsyncMock(
            side_effect=[
                OpenRouterError("fail 1"),
                OpenRouterError("fail 2"),
                (b"enhanced", "image/png"),
            ]
        )

        with patch(
            "app.services.image_enhancement_service.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await service.enhance_or_original_with_status(
                b"original",
                "image/jpeg",
            )

        self.assertEqual(result, (b"enhanced", "image/png", True))
        self.assertEqual(service.enhance.await_count, 3)

    async def test_rejects_unchanged_image_and_retries(self) -> None:
        service = ImageEnhancementService(
            self._settings(openrouter_image_max_attempts=2),
        )
        service.enhance = AsyncMock(
            side_effect=[
                (b"original", "image/png"),
                (b"enhanced", "image/png"),
            ]
        )

        with patch(
            "app.services.image_enhancement_service.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await service.enhance_or_original_with_status(
                b"original",
                "image/png",
            )

        self.assertEqual(result, (b"enhanced", "image/png", True))
        self.assertEqual(service.enhance.await_count, 2)


if __name__ == "__main__":
    unittest.main()
