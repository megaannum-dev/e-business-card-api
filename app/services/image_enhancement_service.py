import asyncio
import base64
import binascii
import logging

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import OpenRouterError, OpenRouterTimeoutError

logger = logging.getLogger(__name__)


ENHANCEMENT_PROMPT = """\
Tightly crop out thin background borders and desk edges around the card.
Mildly improve lighting only so printed text is clearer.
 
CRITICAL:
- Preserve the original business-card aspect ratio (approx 85:54 / ~3:2 landscape).
- Do NOT output a square image.
- Do NOT stretch, squeeze, or pad to 1:1.
- Keep the same orientation as the input (landscape stays landscape).
 
Do not redraw or alter any text, logos, or colors.
Return only the cleaned cropped card image.
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

        response: httpx.Response | None = None
        max_attempts = self._settings.openrouter_max_retries + 1
        for attempt in range(1, max_attempts + 1):
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
                if attempt >= max_attempts:
                    raise OpenRouterTimeoutError("Image enhancement timed out") from exc
                await asyncio.sleep(min(0.5 * attempt, 2.0))
                continue
            except httpx.RequestError as exc:
                if attempt >= max_attempts:
                    raise OpenRouterError(
                        f"Image enhancement network error: {exc}",
                    ) from exc
                await asyncio.sleep(min(0.5 * attempt, 2.0))
                continue

            if response.status_code not in {429, 500, 502, 503, 504}:
                break
            if attempt >= max_attempts:
                break
            await asyncio.sleep(min(0.5 * attempt, 2.0))

        if response is None:
            raise OpenRouterError("Image enhancement request failed")
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
        if not self._settings.openrouter_image_enhancement_enabled:
            return image_bytes, content_type

        try:
            return await self.enhance(image_bytes, content_type)
        except OpenRouterError as exc:
            # Image cleanup is cosmetic. A provider outage must not prevent the
            # card and its original scan from being saved.
            logger.warning("Image enhancement failed; storing original scan: %s", exc)
            return image_bytes, content_type