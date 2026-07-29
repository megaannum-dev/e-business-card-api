import asyncio
import logging
import re
from datetime import UTC, datetime

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorCollection
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from app.core.exceptions import (
    CardNotFoundError,
    CardPersistenceError,
    OpenRouterError,
    OpenRouterTimeoutError,
    ScanImageNotFoundError,
)
from app.models.card import CapturedCardBase, CapturedCardDocument, CapturedCardResponse, PhotoFace, WalletDisplay

from app.models.requests import CapturedCardUpdate
from app.services.image_enhancement_service import ImageEnhancementService
from app.services.openrouter import OpenRouterService
from app.services.scan_image_service import ScanImageService

logger = logging.getLogger(__name__)

_OFFLINE_PREVIEW_CUSTOM_KEY = re.compile(r"^line_\d+$")


class CardService:
    def __init__(
        self,
        collection: AsyncIOMotorCollection,
        scan_image_service: ScanImageService | None = None,
        openrouter_service: OpenRouterService | None = None,
        image_enhancement_service: ImageEnhancementService | None = None,
    ) -> None:
        self._collection = collection
        self._scan_images = scan_image_service
        self._openrouter = openrouter_service or OpenRouterService()
        self._image_enhancer = image_enhancement_service or ImageEnhancementService()

    async def process_and_save(
        self,
        owner_user_id: str,
        raw_ocr_text: str,
        scan_image_bytes: bytes | None = None,
        scan_image_content_type: str = "image/jpeg",
        scan_image_back_bytes: bytes | None = None,
        scan_image_back_content_type: str = "image/jpeg",
    ) -> CapturedCardResponse:
        parsed = await self._openrouter.parse_ocr_text(raw_ocr_text)

        document = CapturedCardDocument(
            owner_user_id=owner_user_id,
            scanned_at=datetime.now(UTC),
            raw_ocr_text=raw_ocr_text.strip(),
            core_fields=parsed.core_fields,
            custom_fields=parsed.custom_fields,
            parse_status="parsed",
            parse_source="llm",
            enhancement_status="none",
            parsed_at=datetime.now(UTC),
        )

        try:
            validated_payload = document.model_dump(mode="python")
            insert_result = await self._collection.insert_one(validated_payload)
        except ValidationError as exc:
            logger.exception("Captured card failed Pydantic validation before persistence")
            raise CardPersistenceError("Card document failed validation") from exc
        except PyMongoError as exc:
            logger.exception("MongoDB insert failed for captured card")
            raise CardPersistenceError("Failed to persist captured card") from exc

        card_id = str(insert_result.inserted_id)
        scan_image_id: str | None = None
        scan_image_front_id: str | None = None
        scan_image_back_id: str | None = None
        wallet_display: WalletDisplay = "classic"
        photo_face: PhotoFace = "front"

        if scan_image_bytes:
            if self._scan_images is None:
                raise CardPersistenceError("Scan image storage is not configured")
            (
                (scan_image_bytes, scan_image_content_type),
                enhanced_back,
            ) = await self._enhance_scan_pair(
                scan_image_bytes,
                scan_image_content_type,
                scan_image_back_bytes,
                scan_image_back_content_type,
            )
            if enhanced_back is not None:
                scan_image_back_bytes, scan_image_back_content_type = enhanced_back
            scan_image_front_id = await self._scan_images.save(
                owner_user_id=owner_user_id,
                card_id=card_id,
                data=scan_image_bytes,
                content_type=scan_image_content_type,
            )
            scan_image_id = scan_image_front_id
            if scan_image_back_bytes:
                scan_image_back_id = await self._scan_images.save(
                    owner_user_id=owner_user_id,
                    card_id=card_id,
                    data=scan_image_back_bytes,
                    content_type=scan_image_back_content_type,
                )
            wallet_display = "photo"
            try:
                await self._collection.update_one(
                    {"_id": insert_result.inserted_id},
                    {
                        "$set": {
                            "scan_image_id": scan_image_id,
                            "scan_image_front_id": scan_image_front_id,
                            "scan_image_back_id": scan_image_back_id,
                            "wallet_display": wallet_display,
                            "photo_face": photo_face,
                        }
                    },
                )
            except PyMongoError as exc:
                logger.exception("Failed to link scan image to card %s", card_id)
                raise CardPersistenceError("Failed to persist captured card") from exc

        return self._to_response(
            {
                **validated_payload,
                "_id": insert_result.inserted_id,
                "scan_image_id": scan_image_id,
                "scan_image_front_id": scan_image_front_id,
                "scan_image_back_id": scan_image_back_id,
                "wallet_display": wallet_display,
                "photo_face": photo_face,
            }
        )

    async def save_offline_draft(
        self,
        owner_user_id: str,
        raw_ocr_text: str,
        core_fields: dict,
        custom_fields: dict,
        edited_fields: list[str] | None = None,
        scan_image_bytes: bytes | None = None,
        scan_image_content_type: str = "image/jpeg",
        scan_image_back_bytes: bytes | None = None,
        scan_image_back_content_type: str = "image/jpeg",
    ) -> CapturedCardResponse:
        document = CapturedCardDocument(
            owner_user_id=owner_user_id,
            scanned_at=datetime.now(UTC),
            raw_ocr_text=raw_ocr_text.strip(),
            core_fields=core_fields,
            custom_fields=custom_fields,
            edited_fields=edited_fields or [],
            parse_status="fallback",
            parse_source="offline",
            enhancement_status="queued",
            parsed_at=datetime.now(UTC),
        )

        try:
            validated_payload = document.model_dump(mode="python")
            insert_result = await self._collection.insert_one(validated_payload)
        except ValidationError as exc:
            logger.exception("Offline draft failed Pydantic validation before persistence")
            raise CardPersistenceError("Card document failed validation") from exc
        except PyMongoError as exc:
            logger.exception("MongoDB insert failed for offline draft")
            raise CardPersistenceError("Failed to persist offline draft") from exc

        card_id = str(insert_result.inserted_id)
        scan_image_id: str | None = None
        scan_image_front_id: str | None = None
        scan_image_back_id: str | None = None
        wallet_display: WalletDisplay = "classic"
        photo_face: PhotoFace = "front"

        if scan_image_bytes:
            if self._scan_images is None:
                raise CardPersistenceError("Scan image storage is not configured")
            scan_image_front_id = await self._scan_images.save(
                owner_user_id=owner_user_id,
                card_id=card_id,
                data=scan_image_bytes,
                content_type=scan_image_content_type,
            )
            scan_image_id = scan_image_front_id
            if scan_image_back_bytes:
                scan_image_back_id = await self._scan_images.save(
                    owner_user_id=owner_user_id,
                    card_id=card_id,
                    data=scan_image_back_bytes,
                    content_type=scan_image_back_content_type,
                )
            wallet_display = "photo"
            try:
                await self._collection.update_one(
                    {"_id": insert_result.inserted_id},
                    {
                        "$set": {
                            "scan_image_id": scan_image_id,
                            "scan_image_front_id": scan_image_front_id,
                            "scan_image_back_id": scan_image_back_id,
                            "wallet_display": wallet_display,
                            "photo_face": photo_face,
                        }
                    },
                )
            except PyMongoError as exc:
                logger.exception("Failed to link scan image to offline draft %s", card_id)
                raise CardPersistenceError("Failed to persist offline draft") from exc

        return self._to_response(
            {
                **validated_payload,
                "_id": insert_result.inserted_id,
                "scan_image_id": scan_image_id,
                "scan_image_front_id": scan_image_front_id,
                "scan_image_back_id": scan_image_back_id,
                "wallet_display": wallet_display,
                "photo_face": photo_face,
            }
        )

    async def enhance_card(self, card_id: str, owner_user_id: str) -> CapturedCardResponse:
        document = await self._get_owned_card_document(card_id, owner_user_id)
        raw_ocr_text = document.get("raw_ocr_text")
        if not raw_ocr_text or not str(raw_ocr_text).strip():
            raise CardPersistenceError("Card has no OCR text available for enhancement")

        try:
            await self._collection.update_one(
                {"_id": document["_id"]},
                {"$set": {"enhancement_status": "processing", "parse_error": None}},
            )
        except PyMongoError as exc:
            logger.exception("Failed to mark card %s as processing", card_id)
            raise CardPersistenceError("Failed to start enhancement") from exc

        try:
            parsed = await self._openrouter.parse_ocr_text(str(raw_ocr_text))
            suggestions = CardService._build_enhancement_suggestions(
                current_core=document.get("core_fields", {}),
                current_custom=document.get("custom_fields", {}),
                parsed=parsed,
                edited_fields=set(document.get("edited_fields", [])),
            )
            image_updates, replaced_image_ids = await self._enhance_stored_scan_images(
                document=document,
                owner_user_id=owner_user_id,
                card_id=card_id,
            )
            next_status = "pending_review" if suggestions else "applied"
            update_fields: dict = {
                "enhanced_suggestions": suggestions,
                "enhancement_status": next_status,
                "parse_error": None,
                **image_updates,
            }
            if not suggestions:
                core_fields, custom_fields = CardService._merge_llm_parse_respecting_edits(
                    current_core=document.get("core_fields", {}),
                    current_custom=document.get("custom_fields", {}),
                    parsed=parsed,
                    edited_fields=set(document.get("edited_fields", [])),
                )
                update_fields["core_fields"] = core_fields
                update_fields["custom_fields"] = custom_fields
                update_fields["parse_status"] = "parsed"
                update_fields["parse_source"] = "llm"
                update_fields["parsed_at"] = datetime.now(UTC)

            await self._collection.update_one(
                {"_id": document["_id"]},
                {"$set": update_fields},
            )
            if self._scan_images is not None:
                for old_image_id in replaced_image_ids:
                    try:
                        await self._scan_images.delete(old_image_id)
                    except CardPersistenceError:
                        logger.warning(
                            "Failed to delete replaced scan image %s for card %s",
                            old_image_id,
                            card_id,
                        )
        except (OpenRouterError, OpenRouterTimeoutError) as exc:
            await self._collection.update_one(
                {"_id": document["_id"]},
                {
                    "$set": {
                        "enhancement_status": "failed",
                        "parse_error": str(exc),
                    }
                },
            )
            raise
        except PyMongoError as exc:
            logger.exception("Failed to persist enhancement for card %s", card_id)
            raise CardPersistenceError("Failed to persist enhancement") from exc

        updated = await self._get_owned_card_document(card_id, owner_user_id)
        return self._to_response(updated)

    async def apply_enhancement(
        self,
        card_id: str,
        owner_user_id: str,
        *,
        accept_all: bool,
        accepted_fields: list[str],
        accepted_overrides: dict[str, str],
    ) -> CapturedCardResponse:
        document = await self._get_owned_card_document(card_id, owner_user_id)
        suggestions: dict[str, str] = document.get("enhanced_suggestions", {})
        if not suggestions:
            raise CardPersistenceError("No enhancement suggestions are available")

        accepted = set(suggestions.keys()) if accept_all else set(accepted_fields)
        core_fields = dict(document.get("core_fields", {}))
        custom_fields = dict(document.get("custom_fields", {}))

        for field_key, value in suggestions.items():
            if field_key not in accepted:
                continue
            next_value = accepted_overrides.get(field_key, value)
            if field_key.startswith("core."):
                core_key = field_key.removeprefix("core.")
                core_fields[core_key] = next_value
            elif field_key.startswith("custom."):
                custom_key = field_key.removeprefix("custom.")
                custom_fields[custom_key] = next_value

        edited_fields = set(document.get("edited_fields", []))
        custom_fields = CardService._strip_offline_preview_custom_fields(
            custom_fields,
            edited_fields,
        )

        try:
            validated = CapturedCardDocument(
                owner_user_id=document["owner_user_id"],
                scanned_at=document["scanned_at"],
                raw_ocr_text=document.get("raw_ocr_text"),
                core_fields=core_fields,
                custom_fields=custom_fields,
                edited_fields=document.get("edited_fields", []),
                parse_status="parsed",
                parse_source="llm",
                enhancement_status="applied",
                enhanced_suggestions={},
                parse_error=None,
                parsed_at=datetime.now(UTC),
                scan_image_id=document.get("scan_image_id"),
                scan_image_front_id=document.get("scan_image_front_id"),
                scan_image_back_id=document.get("scan_image_back_id"),
                wallet_display=document.get("wallet_display"),
                photo_face=document.get("photo_face"),
            )
            await self._collection.update_one(
                {"_id": document["_id"]},
                {
                    "$set": {
                        "core_fields": validated.core_fields.model_dump(mode="python"),
                        "custom_fields": validated.custom_fields,
                        "parse_status": "parsed",
                        "parse_source": "llm",
                        "enhancement_status": "applied",
                        "enhanced_suggestions": {},
                        "parse_error": None,
                        "parsed_at": validated.parsed_at,
                    }
                },
            )
        except ValidationError as exc:
            raise CardPersistenceError("Applied enhancement failed validation") from exc
        except PyMongoError as exc:
            logger.exception("Failed to apply enhancement for card %s", card_id)
            raise CardPersistenceError("Failed to apply enhancement") from exc

        updated = await self._get_owned_card_document(card_id, owner_user_id)
        return self._to_response(updated)

    async def import_from_user_card(
        self,
        owner_user_id: str,
        *,
        core_fields: dict,
        custom_fields: dict,
        source_scan_front_id: str | None,
        source_scan_back_id: str | None,
        wallet_display: str = "classic",
        photo_face: str = "front",
    ) -> CapturedCardResponse:
        document = CapturedCardDocument(
            owner_user_id=owner_user_id,
            scanned_at=datetime.now(UTC),
            core_fields=core_fields,
            custom_fields=custom_fields,
            parse_status="parsed",
            parse_source="manual",
            enhancement_status="none",
            parsed_at=datetime.now(UTC),
        )

        try:
            validated_payload = document.model_dump(mode="python")
            insert_result = await self._collection.insert_one(validated_payload)
        except ValidationError as exc:
            logger.exception("Imported card failed validation before persistence")
            raise CardPersistenceError("Card document failed validation") from exc
        except PyMongoError as exc:
            logger.exception("MongoDB insert failed for imported card")
            raise CardPersistenceError("Failed to persist captured card") from exc

        card_id = str(insert_result.inserted_id)
        scan_image_id: str | None = None
        scan_image_front_id: str | None = None
        scan_image_back_id: str | None = None
        resolved_wallet_display: WalletDisplay = (
            wallet_display if wallet_display in ("photo", "classic") else "classic"
        )
        resolved_photo_face: PhotoFace = photo_face if photo_face in ("front", "back") else "front"

        if source_scan_front_id:
            if self._scan_images is None:
                raise CardPersistenceError("Scan image storage is not configured")
            scan_image_front_id = await self._copy_scan_image(
                owner_user_id=owner_user_id,
                card_id=card_id,
                source_file_id=source_scan_front_id,
            )
            scan_image_id = scan_image_front_id
            if source_scan_back_id:
                scan_image_back_id = await self._copy_scan_image(
                    owner_user_id=owner_user_id,
                    card_id=card_id,
                    source_file_id=source_scan_back_id,
                )
            resolved_wallet_display = "photo"
            try:
                await self._collection.update_one(
                    {"_id": insert_result.inserted_id},
                    {
                        "$set": {
                            "scan_image_id": scan_image_id,
                            "scan_image_front_id": scan_image_front_id,
                            "scan_image_back_id": scan_image_back_id,
                            "wallet_display": resolved_wallet_display,
                            "photo_face": resolved_photo_face,
                        }
                    },
                )
            except PyMongoError as exc:
                logger.exception("Failed to link scan images to imported card %s", card_id)
                raise CardPersistenceError("Failed to persist captured card") from exc

        return self._to_response(
            {
                **validated_payload,
                "_id": insert_result.inserted_id,
                "scan_image_id": scan_image_id,
                "scan_image_front_id": scan_image_front_id,
                "scan_image_back_id": scan_image_back_id,
                "wallet_display": resolved_wallet_display,
                "photo_face": resolved_photo_face,
            }
        )

    async def _copy_scan_image(
        self,
        *,
        owner_user_id: str,
        card_id: str,
        source_file_id: str,
    ) -> str:
        if self._scan_images is None:
            raise CardPersistenceError("Scan image storage is not configured")
        image_bytes, content_type = await self._scan_images.read(source_file_id)
        return await self._scan_images.save(
            owner_user_id=owner_user_id,
            card_id=card_id,
            data=image_bytes,
            content_type=content_type,
        )

    async def _enhance_scan_pair(
        self,
        front_bytes: bytes,
        front_content_type: str,
        back_bytes: bytes | None,
        back_content_type: str,
    ) -> tuple[tuple[bytes, str], tuple[bytes, str] | None]:
        front_task = self._image_enhancer.enhance_or_original(
            front_bytes,
            front_content_type,
        )
        if back_bytes is None:
            return await front_task, None

        front, back = await asyncio.gather(
            front_task,
            self._image_enhancer.enhance_or_original(
                back_bytes,
                back_content_type,
            ),
        )
        return front, back

    async def _enhance_stored_scan_images(
        self,
        *,
        document: dict,
        owner_user_id: str,
        card_id: str,
    ) -> tuple[dict[str, str], set[str]]:
        if self._scan_images is None:
            return {}, set()

        front_image_id = CardService._scan_front_image_id(document)
        back_image_id = CardService._scan_back_image_id(document)

        async def enhance_face(
            image_id: str | None,
            face: PhotoFace,
        ) -> tuple[PhotoFace, str, str] | None:
            if not image_id:
                return None
            try:
                image_bytes, content_type = await self._scan_images.read(image_id)
                enhanced_bytes, enhanced_content_type, was_enhanced = (
                    await self._image_enhancer.enhance_or_original_with_status(
                        image_bytes,
                        content_type,
                    )
                )
                if not was_enhanced:
                    return None
                new_image_id = await self._scan_images.save(
                    owner_user_id=owner_user_id,
                    card_id=card_id,
                    data=enhanced_bytes,
                    content_type=enhanced_content_type,
                )
                return face, new_image_id, image_id
            except CardPersistenceError as exc:
                logger.warning(
                    "Failed to enhance stored %s scan for card %s: %s",
                    face,
                    card_id,
                    exc,
                )
                return None

        results = await asyncio.gather(
            enhance_face(front_image_id, "front"),
            enhance_face(back_image_id, "back"),
        )

        updates: dict[str, str] = {}
        replaced_ids: set[str] = set()
        for result in results:
            if result is None:
                continue
            face, new_image_id, old_image_id = result
            replaced_ids.add(old_image_id)
            if face == "front":
                updates["scan_image_id"] = new_image_id
                updates["scan_image_front_id"] = new_image_id
            else:
                updates["scan_image_back_id"] = new_image_id

        return updates, replaced_ids

    async def list_for_user(self, owner_user_id: str) -> list[CapturedCardResponse]:
        try:
            cursor = self._collection.find({"owner_user_id": owner_user_id}).sort(
                "scanned_at",
                -1,
            )
            documents = await cursor.to_list(length=None)
        except PyMongoError as exc:
            logger.exception("MongoDB query failed for captured cards")
            raise CardPersistenceError("Failed to load captured cards") from exc

        return [self._to_response(document) for document in documents]

    async def update_wallet_display(
        self,
        card_id: str,
        owner_user_id: str,
        wallet_display: WalletDisplay | None = None,
        photo_face: PhotoFace | None = None,
    ) -> CapturedCardResponse:
        document = await self._get_owned_card_document(card_id, owner_user_id)

        resolved_front = CardService._scan_front_image_id(document)
        next_wallet_display = wallet_display or CardService._resolve_wallet_display(document, resolved_front)
        next_photo_face = photo_face or CardService._resolve_photo_face(document)

        if next_wallet_display == "photo" and not resolved_front:
            raise CardPersistenceError("Cannot show photo display without a scan image")
        if next_photo_face == "back" and not CardService._scan_back_image_id(document):
            raise CardPersistenceError("Cannot show back photo without a back scan image")

        try:
            await self._collection.update_one(
                {"_id": document["_id"]},
                {"$set": {"wallet_display": next_wallet_display, "photo_face": next_photo_face}},
            )
        except PyMongoError as exc:
            logger.exception("Failed to update wallet display for card %s", card_id)
            raise CardPersistenceError("Failed to update wallet display") from exc

        updated = {**document, "wallet_display": next_wallet_display, "photo_face": next_photo_face}
        return self._to_response(updated)

    async def update(
        self,
        card_id: str,
        owner_user_id: str,
        payload: CapturedCardUpdate,
    ) -> CapturedCardResponse:
        document = await self._get_owned_card_document(card_id, owner_user_id)
        payload_data = payload.model_dump(exclude_unset=True)

        core_fields = dict(document.get("core_fields", {}))
        if payload_data.get("core_fields") is not None:
            core_fields = payload_data["core_fields"]

        custom_fields = dict(document.get("custom_fields", {}))
        if payload_data.get("custom_fields") is not None:
            custom_fields = payload_data["custom_fields"]

        try:
            validated = CapturedCardDocument(
                owner_user_id=document["owner_user_id"],
                scanned_at=document["scanned_at"],
                raw_ocr_text=document.get("raw_ocr_text"),
                core_fields=core_fields,
                custom_fields=custom_fields,
                edited_fields=document.get("edited_fields", []),
                parse_status=document.get("parse_status", "parsed"),
                parse_source=document.get("parse_source", "llm"),
                enhancement_status=document.get("enhancement_status", "none"),
                enhanced_suggestions=document.get("enhanced_suggestions", {}),
                parse_error=document.get("parse_error"),
                parsed_at=document.get("parsed_at"),
                scan_image_id=document.get("scan_image_id"),
                scan_image_front_id=document.get("scan_image_front_id"),
                scan_image_back_id=document.get("scan_image_back_id"),
                wallet_display=document.get("wallet_display"),
                photo_face=document.get("photo_face"),
            )
            await self._collection.update_one(
                {"_id": document["_id"]},
                {
                    "$set": {
                        "core_fields": validated.core_fields.model_dump(mode="python"),
                        "custom_fields": validated.custom_fields,
                    }
                },
            )
        except ValidationError as exc:
            raise CardPersistenceError("Card document failed validation") from exc
        except PyMongoError as exc:
            logger.exception("Failed to update captured card %s", card_id)
            raise CardPersistenceError("Failed to update captured card") from exc

        updated = {
            **document,
            "core_fields": validated.core_fields.model_dump(mode="python"),
            "custom_fields": validated.custom_fields,
        }
        return self._to_response(updated)

    async def get_scan_image(
        self,
        card_id: str,
        owner_user_id: str,
        face: PhotoFace = "front",
    ) -> tuple[bytes, str]:
        document = await self._get_owned_card_document(card_id, owner_user_id)

        scan_image_id = (
            CardService._scan_front_image_id(document)
            if face == "front"
            else CardService._scan_back_image_id(document)
        )
        if not scan_image_id or self._scan_images is None:
            raise ScanImageNotFoundError("Scan image not found")

        try:
            return await self._scan_images.read(scan_image_id)
        except CardPersistenceError as exc:
            raise ScanImageNotFoundError("Scan image not found") from exc

    async def delete(self, card_id: str, owner_user_id: str) -> None:
        document = await self._get_owned_card_document(card_id, owner_user_id)

        scan_image_ids = {
            image_id
            for image_id in (
                document.get("scan_image_id"),
                document.get("scan_image_front_id"),
                document.get("scan_image_back_id"),
            )
            if image_id
        }
        if scan_image_ids and self._scan_images is not None:
            for scan_image_id in scan_image_ids:
                try:
                    await self._scan_images.delete(scan_image_id)
                except CardPersistenceError:
                    logger.warning(
                        "Failed to delete scan image %s for card %s",
                        scan_image_id,
                        card_id,
                    )

        try:
            result = await self._collection.delete_one(
                {"_id": document["_id"], "owner_user_id": owner_user_id},
            )
        except PyMongoError as exc:
            logger.exception("MongoDB delete failed for card %s", card_id)
            raise CardPersistenceError("Failed to delete captured card") from exc

        if result.deleted_count == 0:
            raise CardNotFoundError("Card not found")

    async def _get_owned_card_document(self, card_id: str, owner_user_id: str) -> dict:
        try:
            object_id = ObjectId(card_id)
        except InvalidId as exc:
            raise CardNotFoundError("Card not found") from exc

        try:
            document = await self._collection.find_one(
                {"_id": object_id, "owner_user_id": owner_user_id},
            )
        except PyMongoError as exc:
            logger.exception("MongoDB query failed for card %s", card_id)
            raise CardPersistenceError("Failed to load card") from exc

        if document is None:
            raise CardNotFoundError("Card not found")

        return document

    @staticmethod
    def _scan_image_url(card_id: str, scan_image_id: str | None) -> str | None:
        if not scan_image_id:
            return None
        return f"/api/v1/cards/{card_id}/scan-image"

    @staticmethod
    def _scan_front_image_id(document: dict) -> str | None:
        return document.get("scan_image_front_id") or document.get("scan_image_id")

    @staticmethod
    def _scan_back_image_id(document: dict) -> str | None:
        return document.get("scan_image_back_id")

    @staticmethod
    def _scan_front_image_url(card_id: str, scan_image_front_id: str | None) -> str | None:
        if not scan_image_front_id:
            return None
        return f"/api/v1/cards/{card_id}/scan-image/front"

    @staticmethod
    def _scan_back_image_url(card_id: str, scan_image_back_id: str | None) -> str | None:
        if not scan_image_back_id:
            return None
        return f"/api/v1/cards/{card_id}/scan-image/back"

    @staticmethod
    def _resolve_wallet_display(document: dict, scan_image_id: str | None) -> WalletDisplay:
        stored = document.get("wallet_display")
        if stored in ("photo", "classic"):
            return stored
        return "photo" if scan_image_id else "classic"

    @staticmethod
    def _resolve_photo_face(document: dict) -> PhotoFace:
        stored = document.get("photo_face")
        if stored in ("front", "back"):
            return stored
        return "front"

    @staticmethod
    def _strip_offline_preview_custom_fields(
        custom_fields: dict[str, str],
        edited_fields: set[str],
    ) -> dict[str, str]:
        stripped = dict(custom_fields)
        for key in list(stripped.keys()):
            if _OFFLINE_PREVIEW_CUSTOM_KEY.match(key) and f"custom.{key}" not in edited_fields:
                del stripped[key]
        return stripped

    @staticmethod
    def _merge_llm_parse_respecting_edits(
        *,
        current_core: dict,
        current_custom: dict,
        parsed: CapturedCardBase,
        edited_fields: set[str],
    ) -> tuple[dict, dict]:
        parsed_core = parsed.core_fields.model_dump(mode="python")
        core_fields = dict(current_core)

        for key, value in parsed_core.items():
            field_key = f"core.{key}"
            if field_key in edited_fields:
                continue
            if value is None or not str(value).strip():
                core_fields[key] = None
            else:
                core_fields[key] = str(value).strip()

        custom_fields: dict[str, str] = {}
        for key, value in parsed.custom_fields.items():
            field_key = f"custom.{key}"
            if field_key in edited_fields:
                continue
            text = str(value).strip()
            if text:
                custom_fields[key] = text

        for field_key in edited_fields:
            if not field_key.startswith("custom."):
                continue
            custom_key = field_key.removeprefix("custom.")
            current_value = current_custom.get(custom_key)
            if current_value is not None and str(current_value).strip():
                custom_fields[custom_key] = str(current_value).strip()
            elif custom_key in custom_fields:
                del custom_fields[custom_key]

        for field_key in edited_fields:
            if not field_key.startswith("core."):
                continue
            core_key = field_key.removeprefix("core.")
            if core_key in current_core:
                core_fields[core_key] = current_core[core_key]

        custom_fields = CardService._strip_offline_preview_custom_fields(
            custom_fields,
            edited_fields,
        )
        return core_fields, custom_fields

    @staticmethod
    def _build_enhancement_suggestions(
        *,
        current_core: dict,
        current_custom: dict,
        parsed: CapturedCardBase,
        edited_fields: set[str],
    ) -> dict[str, str]:
        suggestions: dict[str, str] = {}
        parsed_core = parsed.core_fields.model_dump(mode="python")

        for key, value in parsed_core.items():
            field_key = f"core.{key}"
            if field_key in edited_fields:
                continue
            if value is None or not str(value).strip():
                continue
            current_value = current_core.get(key)
            if str(value).strip() != str(current_value or "").strip():
                suggestions[field_key] = str(value).strip()

        for key, value in parsed.custom_fields.items():
            field_key = f"custom.{key}"
            if field_key in edited_fields:
                continue
            if not str(value).strip():
                continue
            current_value = current_custom.get(key)
            if str(value).strip() != str(current_value or "").strip():
                suggestions[field_key] = str(value).strip()

        return suggestions

    @staticmethod
    def _to_response(document: dict) -> CapturedCardResponse:
        card_id = str(document["_id"])
        scan_image_front_id = CardService._scan_front_image_id(document)
        scan_image_back_id = CardService._scan_back_image_id(document)
        return CapturedCardResponse(
            _id=card_id,
            owner_user_id=document["owner_user_id"],
            scanned_at=document["scanned_at"],
            core_fields=document["core_fields"],
            custom_fields=document.get("custom_fields", {}),
            scan_image_url=CardService._scan_image_url(card_id, scan_image_front_id),
            scan_image_front_url=CardService._scan_front_image_url(card_id, scan_image_front_id),
            scan_image_back_url=CardService._scan_back_image_url(card_id, scan_image_back_id),
            wallet_display=CardService._resolve_wallet_display(document, scan_image_front_id),
            photo_face=CardService._resolve_photo_face(document),
            parse_status=document.get("parse_status", "parsed"),
            parse_source=document.get("parse_source", "llm"),
            enhancement_status=document.get("enhancement_status", "none"),
            enhanced_suggestions=document.get("enhanced_suggestions", {}),
            parse_error=document.get("parse_error"),
            parsed_at=document.get("parsed_at"),
        )
