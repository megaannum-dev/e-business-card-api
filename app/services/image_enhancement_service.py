import asyncio
import base64
import binascii
import logging

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import OpenRouterError, OpenRouterTimeoutError

logger = logging.getLogger(__name__)


ENHANCEMENT_PROMPT = """\
Task: Complete strict geometric image cropping and perspective translation on the provided image. Do not generate or paint new artistic textures.

GEOMETRIC BOUNDARIES:
- Detect the 4 outermost physical edges of the card material.
- Crop edge-to-edge so the card touches all four output borders. 
- Delete 100% of pixels outside the card boundary (remove tables, scanner edges, hands, shadows).
- Correct all tilt and keystone distortion to force a flat, top-down rectangular plane.
- Maintain original horizontal landscape proportions. Never output a square image.

PIXEL INVARIANCE (COLOR PROTECTION MATRIX):
- Treat the RGB pixel values inside the card as mathematical constants. 
- Do not add lighting filters, contrast changes, denoising, or style enhancements.
- The output background color must map 1:1 identically to the input image background. If the input background is white, the output must remain pure #FFFFFF white. Do not warm the tint or add cream, off-white, or beige hues.
- Do not reconstruct, sharpen, or repaint logos, lines, or characters.

CRITICAL NEGATIVE CONSTRAINTS (DO NOT INCLUDE IN OUTPUT):
[NEGATIVE_PROMPT: beige, cream, off-white, warm tones, studio lighting, yellow tint, gradient background, paper texture, ambient occlusion shadows, background bleeding]

"""


class ImageEnhancementService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def _image_api_key(self) -> str:
        return self._settings.openrouter_api_key_us or self._settings.openrouter_api_key

    async def enhance(
        self,
        image_bytes: bytes,
        content_type: str,
    ) -> tuple[bytes, str]:
        api_key = self._image_api_key()
        if not api_key:
            raise OpenRouterError("OpenRouter image API key is not configured")
        if not image_bytes:
            raise OpenRouterError("Image enhancement input is empty")

        encoded = base64.b64encode(image_bytes).decode("ascii")

        payload = {
            "model": self._settings.openrouter_image_model,
            "prompt": ENHANCEMENT_PROMPT,
            "input_references": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{content_type};base64,{encoded}",
                    },
                },
            ],
            "n": 1,
        }
        if (
            self._settings.openrouter_image_provider
            and self._settings.openrouter_image_model.startswith("google/gemini")
        ):
            payload["provider"] = {
                "only": [self._settings.openrouter_image_provider],
                "allow_fallbacks": False,
            }
        # OpenAI image models support these controls. Omitting them keeps the
        # request compatible if another OpenRouter image model is configured.
        if self._settings.openrouter_image_model.startswith("openai/"):
            payload["quality"] = self._settings.openrouter_image_quality
            payload["aspect_ratio"] = "auto"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://e-business-card.local",
            "X-Title": self._settings.app_name,
        }

        try:
            async with httpx.AsyncClient(
                base_url=self._settings.openrouter_base_url,
                timeout=httpx.Timeout(self._settings.openrouter_image_timeout_seconds),
            ) as client:
                response = await client.post(
                    "/images",
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise OpenRouterTimeoutError("Image enhancement timed out") from exc
        except httpx.RequestError as exc:
            raise OpenRouterError(
                f"Image enhancement network error: {exc}",
            ) from exc

        if response.status_code >= 400:
            raise OpenRouterError(
                f"Image enhancement failed: {response.text[:300]}",
                status_code=response.status_code,
            )

        try:
            result = response.json()
            image_data = result["data"][0]
            enhanced_base64 = image_data["b64_json"]
            if enhanced_base64.startswith("data:") and "," in enhanced_base64:
                enhanced_base64 = enhanced_base64.split(",", 1)[1]
            enhanced_bytes = base64.b64decode(enhanced_base64, validate=True)
            if not enhanced_bytes:
                raise ValueError("empty image")
        except (KeyError, IndexError, binascii.Error, ValueError, TypeError) as exc:
            raise OpenRouterError(
                "OpenRouter returned invalid image data",
            ) from exc

        return enhanced_bytes, "image/png"

    async def enhance_or_original(
        self,
        image_bytes: bytes,
        content_type: str,
    ) -> tuple[bytes, str]:
        enhanced_bytes, enhanced_content_type, _ = await self.enhance_or_original_with_status(
            image_bytes,
            content_type,
        )
        return enhanced_bytes, enhanced_content_type

    async def enhance_or_original_with_status(
        self,
        image_bytes: bytes,
        content_type: str,
    ) -> tuple[bytes, str, bool]:
        if not self._settings.openrouter_image_enhancement_enabled:
            return image_bytes, content_type, False

        max_attempts = max(1, self._settings.openrouter_image_max_attempts)
        last_error: OpenRouterError | None = None
        attempts_made = 0
        for attempt in range(1, max_attempts + 1):
            attempts_made = attempt
            try:
                enhanced_bytes, enhanced_content_type = await self.enhance(
                    image_bytes,
                    content_type,
                )
                if enhanced_bytes == image_bytes:
                    raise OpenRouterError(
                        "Image enhancement returned an unchanged image",
                    )
                return enhanced_bytes, enhanced_content_type, True
            except OpenRouterError as exc:
                last_error = exc
                logger.warning(
                    "Image enhancement attempt %s/%s failed: %s",
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts and self._is_retryable(exc):
                    await asyncio.sleep(min(0.5 * attempt, 2.0))
                    continue
                break

        # Cosmetic cleanup must not block saving the card + original scan.
        logger.warning(
            "Image enhancement failed after %s attempts; storing original scan: %s",
            attempts_made,
            last_error,
        )
        return image_bytes, content_type, False

    @staticmethod
    def _is_retryable(exc: OpenRouterError) -> bool:
        if exc.status_code is None or exc.status_code in {408, 429, 500, 502, 503, 504}:
            return True
        # Gemini occasionally completes a generation without emitting image bytes.
        # The request itself is valid, so a fresh bounded generation can succeed.
        return exc.status_code == 400 and "returned no image data" in str(exc).lower()