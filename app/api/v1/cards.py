import base64
import binascii
import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from motor.motor_asyncio import AsyncIOMotorCollection

from app.core.auth import get_current_user_id
from app.core.exceptions import (
    CardNotFoundError,
    CardPersistenceError,
    OpenRouterError,
    OpenRouterSafetyError,
    OpenRouterTimeoutError,
    ScanImageNotFoundError,
)
from app.core.config import get_settings
from app.core.llm_deps import enforce_llm_rate_limit
from app.db.mongodb import get_cards_collection_dependency, get_scan_image_service_dependency
from app.api.v1.share_links import get_share_link_service
from app.models.card import CapturedCardResponse, PhotoFace
from app.models.requests import (
    ApplyEnhancementRequest,
    CapturedCardUpdate,
    OCR_TEXT_MAX_LENGTH,
    UpdateWalletDisplayRequest,
)
from app.services.card_service import CardService
from app.services.llm_guardrails import validate_ocr_submission
from app.services.scan_image_service import ScanImageService
from app.services.share_link_service import (
    ShareLinkImportError,
    ShareLinkNotFoundError,
    ShareLinkService,
)
from app.utils.multipart_files import optional_upload_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cards", tags=["cards"])

MAX_SCAN_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_SCAN_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


def get_card_service(
    collection: AsyncIOMotorCollection = Depends(get_cards_collection_dependency),
    scan_image_service: ScanImageService = Depends(get_scan_image_service_dependency),
) -> CardService:
    return CardService(collection=collection, scan_image_service=scan_image_service)


@router.get(
    "",
    response_model=list[CapturedCardResponse],
    summary="List captured business cards for the authenticated user",
)
async def list_cards(
    owner_user_id: str = Depends(get_current_user_id),
    card_service: CardService = Depends(get_card_service),
) -> list[CapturedCardResponse]:
    try:
        return await card_service.list_for_user(owner_user_id)
    except CardPersistenceError as exc:
        logger.error("Database error while listing cards: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load captured cards.",
        ) from exc


@router.post(
    "/from-share/{token}",
    response_model=CapturedCardResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a shared user card into the authenticated user's collection",
)
async def import_card_from_share(
    token: str,
    owner_user_id: str = Depends(get_current_user_id),
    card_service: CardService = Depends(get_card_service),
    share_link_service: ShareLinkService = Depends(get_share_link_service),
) -> CapturedCardResponse:
    try:
        return await share_link_service.import_to_collection(
            token=token,
            importer_user_id=owner_user_id,
            card_service=card_service,
        )
    except ShareLinkNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ShareLinkImportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except CardPersistenceError as exc:
        logger.error("Failed to import shared card: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save shared card to your collection.",
        ) from exc


@router.post(
    "/process",
    response_model=CapturedCardResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Parse OCR text and persist a captured business card",
)
async def process_card(
    raw_ocr_text: str = Form(..., min_length=1, max_length=OCR_TEXT_MAX_LENGTH),
    scan_image: UploadFile | str | None = File(None),
    scan_image_base64: str | None = Form(None),
    scan_image_back: UploadFile | str | None = File(None),
    scan_image_back_base64: str | None = Form(None),
    owner_user_id: str = Depends(enforce_llm_rate_limit),
    card_service: CardService = Depends(get_card_service),
) -> CapturedCardResponse:
    settings = get_settings()
    try:
        validate_ocr_submission(
            raw_ocr_text,
            max_length=settings.ocr_text_max_length,
            max_lines=settings.ocr_text_max_lines,
        )
    except OpenRouterSafetyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OCR text is not valid for card parsing.",
        ) from exc

    scan_image_bytes: bytes | None = None
    scan_image_content_type = "image/jpeg"
    scan_image_back_bytes: bytes | None = None
    scan_image_back_content_type = "image/jpeg"
    scan_image = optional_upload_file(scan_image)
    scan_image_back = optional_upload_file(scan_image_back)

    if scan_image_base64:
        payload = scan_image_base64.strip()
        if payload.startswith("data:") and "," in payload:
            payload = payload.split(",", 1)[1]
        try:
            scan_image_bytes = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scan_image_base64 is not valid base64.",
            ) from exc

    if scan_image is not None and scan_image.filename and scan_image_bytes is None:
        scan_image_bytes = await scan_image.read()
        if scan_image_bytes:
            declared_type = (scan_image.content_type or "image/jpeg").split(";")[0].strip().lower()
            if declared_type not in ALLOWED_SCAN_IMAGE_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail="Scan image must be JPEG, PNG, or WebP.",
                )
            scan_image_content_type = declared_type

    if scan_image_bytes and len(scan_image_bytes) > MAX_SCAN_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Scan image must be 10 MB or smaller.",
        )

    if scan_image_back_base64:
        payload = scan_image_back_base64.strip()
        if payload.startswith("data:") and "," in payload:
            payload = payload.split(",", 1)[1]
        try:
            scan_image_back_bytes = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scan_image_back_base64 is not valid base64.",
            ) from exc

    if scan_image_back is not None and scan_image_back.filename and scan_image_back_bytes is None:
        scan_image_back_bytes = await scan_image_back.read()
        if scan_image_back_bytes:
            declared_type = (scan_image_back.content_type or "image/jpeg").split(";")[0].strip().lower()
            if declared_type not in ALLOWED_SCAN_IMAGE_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail="Back scan image must be JPEG, PNG, or WebP.",
                )
            scan_image_back_content_type = declared_type

    if scan_image_back_bytes and len(scan_image_back_bytes) > MAX_SCAN_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Back scan image must be 10 MB or smaller.",
        )

    try:
        return await card_service.process_and_save(
            owner_user_id=owner_user_id,
            raw_ocr_text=raw_ocr_text,
            scan_image_bytes=scan_image_bytes,
            scan_image_content_type=scan_image_content_type,
            scan_image_back_bytes=scan_image_back_bytes,
            scan_image_back_content_type=scan_image_back_content_type,
        )
    except OpenRouterTimeoutError as exc:
        logger.warning("OpenRouter timeout while processing card for user %s", owner_user_id)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="LLM parsing service timed out. Please try again.",
        ) from exc
    except OpenRouterSafetyError as exc:
        logger.warning("OCR safety validation failed for user %s: %s", owner_user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OCR text is not valid for card parsing.",
        ) from exc
    except OpenRouterError as exc:
        logger.error("OpenRouter error while processing card: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM parsing service is unavailable.",
        ) from exc
    except CardPersistenceError as exc:
        logger.error("Database error while saving card: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save captured card.",
        ) from exc


@router.post(
    "/offline-draft",
    response_model=CapturedCardResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save an offline OCR draft for later LLM enhancement",
)
async def save_offline_draft(
    raw_ocr_text: str = Form(..., min_length=1, max_length=OCR_TEXT_MAX_LENGTH),
    core_fields_json: str = Form(...),
    custom_fields_json: str = Form("{}"),
    edited_fields_json: str = Form("[]"),
    scan_image: UploadFile | str | None = File(None),
    scan_image_base64: str | None = Form(None),
    scan_image_back: UploadFile | str | None = File(None),
    scan_image_back_base64: str | None = Form(None),
    owner_user_id: str = Depends(get_current_user_id),
    card_service: CardService = Depends(get_card_service),
) -> CapturedCardResponse:
    try:
        core_fields = json.loads(core_fields_json)
        custom_fields = json.loads(custom_fields_json)
        edited_fields = json.loads(edited_fields_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON in offline draft fields.",
        ) from exc

    settings = get_settings()
    try:
        validate_ocr_submission(
            raw_ocr_text,
            max_length=settings.ocr_text_max_length,
            max_lines=settings.ocr_text_max_lines,
        )
    except OpenRouterSafetyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OCR text is not valid for card parsing.",
        ) from exc

    scan_image_bytes: bytes | None = None
    scan_image_content_type = "image/jpeg"
    scan_image_back_bytes: bytes | None = None
    scan_image_back_content_type = "image/jpeg"
    scan_image = optional_upload_file(scan_image)
    scan_image_back = optional_upload_file(scan_image_back)

    if scan_image_base64:
        payload = scan_image_base64.strip()
        if payload.startswith("data:") and "," in payload:
            payload = payload.split(",", 1)[1]
        try:
            scan_image_bytes = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scan_image_base64 is not valid base64.",
            ) from exc

    if scan_image is not None and scan_image.filename and scan_image_bytes is None:
        scan_image_bytes = await scan_image.read()
        if scan_image_bytes:
            declared_type = (scan_image.content_type or "image/jpeg").split(";")[0].strip().lower()
            if declared_type not in ALLOWED_SCAN_IMAGE_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail="Scan image must be JPEG, PNG, or WebP.",
                )
            scan_image_content_type = declared_type

    if scan_image_back_base64:
        payload = scan_image_back_base64.strip()
        if payload.startswith("data:") and "," in payload:
            payload = payload.split(",", 1)[1]
        try:
            scan_image_back_bytes = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scan_image_back_base64 is not valid base64.",
            ) from exc

    if scan_image_back is not None and scan_image_back.filename and scan_image_back_bytes is None:
        scan_image_back_bytes = await scan_image_back.read()
        if scan_image_back_bytes:
            declared_type = (scan_image_back.content_type or "image/jpeg").split(";")[0].strip().lower()
            if declared_type not in ALLOWED_SCAN_IMAGE_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail="Back scan image must be JPEG, PNG, or WebP.",
                )
            scan_image_back_content_type = declared_type

    try:
        return await card_service.save_offline_draft(
            owner_user_id=owner_user_id,
            raw_ocr_text=raw_ocr_text,
            core_fields=core_fields,
            custom_fields=custom_fields,
            edited_fields=edited_fields if isinstance(edited_fields, list) else [],
            scan_image_bytes=scan_image_bytes,
            scan_image_content_type=scan_image_content_type,
            scan_image_back_bytes=scan_image_back_bytes,
            scan_image_back_content_type=scan_image_back_content_type,
        )
    except CardPersistenceError as exc:
        logger.error("Failed to save offline draft: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/{card_id}/enhance",
    response_model=CapturedCardResponse,
    summary="Run LLM enhancement for an offline draft and produce review suggestions",
)
async def enhance_card(
    card_id: str,
    owner_user_id: str = Depends(enforce_llm_rate_limit),
    card_service: CardService = Depends(get_card_service),
) -> CapturedCardResponse:
    try:
        return await card_service.enhance_card(card_id, owner_user_id)
    except CardNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found.") from exc
    except OpenRouterTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="LLM parsing service timed out. Please try again.",
        ) from exc
    except OpenRouterSafetyError as exc:
        logger.warning("OCR safety validation failed during enhancement for card %s: %s", card_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stored OCR text is not valid for card parsing.",
        ) from exc
    except OpenRouterError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM parsing service is unavailable.",
        ) from exc
    except CardPersistenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{card_id}/enhancement/apply",
    response_model=CapturedCardResponse,
    summary="Apply accepted LLM enhancement suggestions to a card",
)
async def apply_card_enhancement(
    card_id: str,
    payload: ApplyEnhancementRequest,
    owner_user_id: str = Depends(get_current_user_id),
    card_service: CardService = Depends(get_card_service),
) -> CapturedCardResponse:
    try:
        return await card_service.apply_enhancement(
            card_id,
            owner_user_id,
            accept_all=payload.accept_all,
            accepted_fields=payload.accepted_fields,
            accepted_overrides=payload.accepted_overrides,
        )
    except CardNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found.") from exc
    except CardPersistenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put(
    "/{card_id}",
    response_model=CapturedCardResponse,
    summary="Update a captured business card",
)
async def update_card(
    card_id: str,
    payload: CapturedCardUpdate,
    owner_user_id: str = Depends(get_current_user_id),
    card_service: CardService = Depends(get_card_service),
) -> CapturedCardResponse:
    try:
        return await card_service.update(card_id, owner_user_id, payload)
    except CardNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found.") from exc
    except CardPersistenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch(
    "/{card_id}/wallet-display",
    response_model=CapturedCardResponse,
    summary="Set wallet display mode for a captured card (photo scan vs classic palette)",
)
async def update_wallet_display(
    card_id: str,
    payload: UpdateWalletDisplayRequest,
    owner_user_id: str = Depends(get_current_user_id),
    card_service: CardService = Depends(get_card_service),
) -> CapturedCardResponse:
    try:
        return await card_service.update_wallet_display(
            card_id=card_id,
            owner_user_id=owner_user_id,
            wallet_display=payload.wallet_display,
            photo_face=payload.photo_face,
        )
    except CardNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Card not found.",
        ) from exc
    except CardPersistenceError as exc:
        logger.error("Failed to update wallet display: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.delete(
    "/{card_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a captured business card",
)
async def delete_card(
    card_id: str,
    owner_user_id: str = Depends(get_current_user_id),
    card_service: CardService = Depends(get_card_service),
) -> None:
    try:
        await card_service.delete(card_id, owner_user_id)
    except CardNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Card not found.",
        ) from exc
    except CardPersistenceError as exc:
        logger.error("Database error while deleting card %s: %s", card_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete captured card.",
        ) from exc


@router.get(
    "/{card_id}/scan-image",
    summary="Download the scanned card image for a captured card",
    responses={
        200: {
            "content": {"image/jpeg": {}},
            "description": "Scan image bytes",
        }
    },
)
async def get_card_scan_image(
    card_id: str,
    owner_user_id: str = Depends(get_current_user_id),
    card_service: CardService = Depends(get_card_service),
) -> Response:
    try:
        image_bytes, content_type = await card_service.get_scan_image(card_id, owner_user_id, face="front")
    except ScanImageNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan image not found.",
        ) from exc
    except CardPersistenceError as exc:
        logger.error("Database error while loading scan image: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load scan image.",
        ) from exc

    return Response(content=image_bytes, media_type=content_type)


@router.get(
    "/{card_id}/scan-image/{face}",
    summary="Download the scanned card image for a captured card by face",
    responses={
        200: {
            "content": {"image/jpeg": {}},
            "description": "Scan image bytes",
        }
    },
)
async def get_card_scan_image_by_face(
    card_id: str,
    face: PhotoFace,
    owner_user_id: str = Depends(get_current_user_id),
    card_service: CardService = Depends(get_card_service),
) -> Response:
    try:
        image_bytes, content_type = await card_service.get_scan_image(card_id, owner_user_id, face=face)
    except ScanImageNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan image not found.",
        ) from exc
    except CardPersistenceError as exc:
        logger.error("Database error while loading scan image: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load scan image.",
        ) from exc

    return Response(content=image_bytes, media_type=content_type)
