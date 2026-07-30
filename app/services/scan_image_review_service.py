import asyncio
from dataclasses import dataclass

from app.core.exceptions import CardPersistenceError
from app.services.image_enhancement_service import ImageEnhancementService
from app.services.scan_image_service import ScanImageService


@dataclass
class ScanPreviewResult:
    updates: dict
    replaced_pending_ids: list[str]


class ScanImageReviewService:
    """Stages AI-cleaned scans without replacing the accepted image."""

    def __init__(
        self,
        scan_images: ScanImageService,
        image_enhancer: ImageEnhancementService,
    ) -> None:
        self._scan_images = scan_images
        self._image_enhancer = image_enhancer

    async def generate_preview(
        self,
        *,
        document: dict,
        owner_user_id: str,
        card_id: str,
    ) -> ScanPreviewResult:
        sources = {
            "front": document.get("scan_image_front_original_id")
            or document.get("scan_image_front_id")
            or document.get("scan_image_id"),
            "back": document.get("scan_image_back_original_id")
            or document.get("scan_image_back_id"),
        }
        if not sources["front"]:
            raise CardPersistenceError("Card has no scan image to enhance")

        async def enhance_face(face: str, file_id: str) -> tuple[str, str | None]:
            image_bytes, content_type = await self._scan_images.read(file_id)
            enhanced_bytes, enhanced_type, was_enhanced = (
                await self._image_enhancer.enhance_or_original_with_status(
                    image_bytes,
                    content_type,
                )
            )
            if not was_enhanced:
                return face, None
            pending_id = await self._scan_images.save(
                owner_user_id=owner_user_id,
                card_id=card_id,
                data=enhanced_bytes,
                content_type=enhanced_type,
            )
            return face, pending_id

        tasks = [
            enhance_face(face, file_id)
            for face, file_id in sources.items()
            if file_id
        ]
        results = await asyncio.gather(*tasks)

        updates: dict = {"scan_image_enhancement_error": None}
        replaced_pending_ids: list[str] = []
        generated = False
        for face, pending_id in results:
            if not pending_id:
                continue
            generated = True
            field = f"scan_image_{face}_pending_id"
            old_pending_id = document.get(field)
            if old_pending_id and old_pending_id != pending_id:
                replaced_pending_ids.append(old_pending_id)
            updates[field] = pending_id

        existing_pending = bool(
            document.get("scan_image_front_pending_id")
            or document.get("scan_image_back_pending_id")
        )
        if generated or existing_pending:
            updates["scan_image_enhancement_status"] = "preview_ready"
            if not generated:
                updates["scan_image_enhancement_error"] = (
                    "A new preview could not be generated; the previous preview is still available."
                )
        else:
            updates["scan_image_enhancement_status"] = "failed"
            updates["scan_image_enhancement_error"] = "AI image enhancement is unavailable."

        return ScanPreviewResult(updates=updates, replaced_pending_ids=replaced_pending_ids)

    async def delete_files(self, file_ids: list[str]) -> None:
        for file_id in dict.fromkeys(file_ids):
            try:
                await self._scan_images.delete(file_id)
            except CardPersistenceError:
                # Database state is authoritative; orphan cleanup can be retried later.
                continue
