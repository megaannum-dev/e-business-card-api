import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from bson import ObjectId

from app.services.card_service import CardService
from app.services.scan_image_review_service import ScanImageReviewService
from app.services.user_card_service import UserCardService


class StoredCardImageEnhancementTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_generation_preserves_original_and_replaces_only_candidate(self) -> None:
        scan_images = AsyncMock()
        scan_images.read.return_value = (b"front-original", "image/jpeg")
        scan_images.save.return_value = "front-candidate-2"
        enhancer = AsyncMock()
        enhancer.enhance_or_original_with_status.return_value = (
            b"front-enhanced",
            "image/png",
            True,
        )
        review = ScanImageReviewService(scan_images, enhancer)

        result = await review.generate_preview(
            document={
                "scan_image_front_original_id": "front-original",
                "scan_image_front_id": "front-original",
                "scan_image_front_pending_id": "front-candidate-1",
            },
            owner_user_id="user-1",
            card_id="card-1",
        )

        self.assertEqual(result.updates["scan_image_front_pending_id"], "front-candidate-2")
        self.assertEqual(result.updates["scan_image_enhancement_status"], "preview_ready")
        self.assertEqual(result.replaced_pending_ids, ["front-candidate-1"])
        self.assertNotIn("scan_image_front_id", result.updates)

    async def test_preview_failure_leaves_original_canonical(self) -> None:
        scan_images = AsyncMock()
        scan_images.read.return_value = (b"original", "image/jpeg")
        enhancer = AsyncMock()
        enhancer.enhance_or_original_with_status.return_value = (
            b"original",
            "image/jpeg",
            False,
        )
        review = ScanImageReviewService(scan_images, enhancer)

        result = await review.generate_preview(
            document={
                "scan_image_front_original_id": "original-id",
                "scan_image_front_id": "original-id",
            },
            owner_user_id="user-1",
            card_id="card-1",
        )

        self.assertEqual(result.updates["scan_image_enhancement_status"], "failed")
        self.assertNotIn("scan_image_front_id", result.updates)
        scan_images.save.assert_not_awaited()

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

    async def test_confirm_promotes_candidate_but_keeps_immutable_original(self) -> None:
        card_id = str(ObjectId())
        object_id = ObjectId(card_id)
        document = {
            "_id": object_id,
            "owner_user_id": "user-1",
            "scanned_at": datetime.now(UTC),
            "core_fields": {"name": "Alex"},
            "custom_fields": {},
            "scan_image_id": "original-id",
            "scan_image_front_id": "original-id",
            "scan_image_front_original_id": "original-id",
            "scan_image_front_pending_id": "candidate-id",
        }
        updated = {
            **document,
            "scan_image_id": "candidate-id",
            "scan_image_front_id": "candidate-id",
            "scan_image_front_pending_id": None,
            "scan_image_enhancement_status": "applied",
        }
        collection = AsyncMock()
        collection.find_one.side_effect = [document, updated]
        scan_images = AsyncMock()
        service = CardService(collection, scan_images, AsyncMock(), AsyncMock())

        response = await service.confirm_scan_image_enhancement(card_id, "user-1")

        self.assertEqual(response.scan_image_front_url, f"/api/v1/cards/{card_id}/scan-image/front?v=candidate-id")
        scan_images.delete.assert_not_awaited()
        self.assertEqual(
            collection.find_one.await_args_list[0].args[0]["owner_user_id"],
            "user-1",
        )

    async def test_user_card_discard_deletes_candidate_only(self) -> None:
        card_id = str(ObjectId())
        object_id = ObjectId(card_id)
        now = datetime.now(UTC)
        document = {
            "_id": object_id,
            "owner_user_id": "user-1",
            "core_fields": {"name": "Alex"},
            "custom_fields": {},
            "design_id": "classic",
            "design_type": "preset",
            "is_primary": True,
            "sort_order": 0,
            "created_at": now,
            "updated_at": now,
            "scan_image_id": "original-id",
            "scan_image_front_id": "original-id",
            "scan_image_front_original_id": "original-id",
            "scan_image_front_pending_id": "candidate-id",
        }
        updated = {
            **document,
            "scan_image_front_pending_id": None,
            "scan_image_enhancement_status": "discarded",
        }
        collection = AsyncMock()
        collection.find_one.side_effect = [document, updated]
        scan_images = AsyncMock()
        service = UserCardService(collection, scan_images, AsyncMock(), AsyncMock())

        response = await service.discard_scan_image_enhancement(card_id, "user-1")

        self.assertEqual(response.scan_image_enhancement_status, "discarded")
        scan_images.delete.assert_awaited_once_with("candidate-id")


if __name__ == "__main__":
    unittest.main()
