import asyncio
import base64
import binascii
import logging

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import OpenRouterError, OpenRouterTimeoutError

logger = logging.getLogger(__name__)


ENHANCEMENT_PROMPT = """\
Extract the physical business card using geometric cropping and perspective correction only.

CROP:
- Detect the four physical card edges.
- Use the outer edge of the card material as the crop boundary, never a printed line or design element.
- Crop edge-to-edge so the card touches all four output edges.
- Remove every pixel outside the card, including desk, table, fingers, shadows, and surroundings.
- Inspect the bottom edge especially carefully. Remove any thin exterior strip, shadow, table edge, or scanner border beyond the physical card, even when it is perfectly straight and parallel to the card.
- A straight line touching or near the bottom of the image is not automatically part of the design. Keep it only if it is clearly printed inside the physical card boundary.
- Do not add padding, margins, borders, or background fill.

GEOMETRY:
- Correct tilt and keystone distortion so the card appears flat and top-down.
- Make opposite card edges parallel.
- Preserve the card’s physical corners; do not redraw or reshape them.
- Preserve the original landscape orientation and physical business-card proportions.

CONTENT PRESERVATION — HIGHEST PRIORITY:
- Treat everything inside the card boundary as immutable.
- Preserve every character, digit, logo, underline, color, and design element exactly.
- Do not redraw, retype, reconstruct, sharpen, replace, or reinterpret content.
- Do not invent missing details.
- If text is blurry, leave it blurry.
- Do not add underlines or other marks.

APPEARANCE PRESERVATION:
- Preserve the original scanned appearance exactly, including paper tone, background color, exposure, white balance, texture, grain, and flat scanner lighting.
- Do not relight, color-correct, white-balance, denoise, enhance contrast, add texture, or make the card look like a phone photograph.
- Do not change ink, paper, logo, or background colors, even slightly.
- Keep the original paper/background color exactly.

Return only the cropped and perspective-corrected card image.
Never output a square image.
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