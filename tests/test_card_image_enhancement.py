import unittest
from unittest.mock import AsyncMock

from app.services.card_service import CardService
from app.services.user_card_service import UserCardService


class StoredCardImageEnhancementTests(unittest.IsolatedAsyncioTestCase):
    async def test_enhances_and_replaces_stored_front_and_back_images(self) -> None:
        scan_images = AsyncMock()
        scan_images.read.side_effect = [
            (b"front-original", "image/jpeg"),
            (b"back-original", "image/jpeg"),
        ]
        scan_images.save.side_effect = ["front-enhanced-id", "back-enhanced-id"]

        enhancer = AsyncMock()
        enhancer.enhance_or_original_with_status.side_effect = [
            (b"front-enhanced", "image/png", True),
            (b"back-enhanced", "image/png", True),
        ]

        service = CardService(
            collection=AsyncMock(),
            scan_image_service=scan_images,
            openrouter_service=AsyncMock(),
            image_enhancement_service=enhancer,
        )

        updates, replaced_ids = await service._enhance_stored_scan_images(
            document={
                "scan_image_id": "front-original-id",
                "scan_image_back_id": "back-original-id",
            },
            owner_user_id="user-1",
            card_id="card-1",
        )

        self.assertEqual(
            updates,
            {
                "scan_image_id": "front-enhanced-id",
                "scan_image_front_id": "front-enhanced-id",
                "scan_image_back_id": "back-enhanced-id",
            },
        )
        self.assertEqual(
            replaced_ids,
            {"front-original-id", "back-original-id"},
        )

    async def test_keeps_stored_image_when_enhancement_falls_back(self) -> None:
        scan_images = AsyncMock()
        scan_images.read.return_value = (b"original", "image/jpeg")

        enhancer = AsyncMock()
        enhancer.enhance_or_original_with_status.return_value = (
            b"original",
            "image/jpeg",
            False,
        )

        service = CardService(
            collection=AsyncMock(),
            scan_image_service=scan_images,
            openrouter_service=AsyncMock(),
            image_enhancement_service=enhancer,
        )

        updates, replaced_ids = await service._enhance_stored_scan_images(
            document={"scan_image_id": "original-id"},
            owner_user_id="user-1",
            card_id="card-1",
        )

        self.assertEqual(updates, {})
        self.assertEqual(replaced_ids, set())
        scan_images.save.assert_not_awaited()

    def test_scan_urls_change_when_stored_image_is_replaced(self) -> None:
        self.assertEqual(
            CardService._scan_front_image_url("card-1", "file-2"),
            "/api/v1/cards/card-1/scan-image/front?v=file-2",
        )
        self.assertEqual(
            UserCardService._scan_front_image_url("card-1", "file-2"),
            "/api/v1/user-cards/card-1/scan-image/front?v=file-2",
        )


if __name__ == "__main__":
    unittest.main()
