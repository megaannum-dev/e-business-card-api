import base64
import binascii
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from motor.motor_asyncio import AsyncIOMotorCollection

from app.core.auth import get_current_user_id
from app.core.exceptions import (
    CardPersistenceError,
    OpenRouterError,
    OpenRouterSafetyError,
    OpenRouterTimeoutError,
    ScanImageNotFoundError,
)
from app.core.llm_deps import enforce_llm_rate_limit
from app.db.mongodb import (
    get_scan_image_service_dependency,
    get_user_cards_collection_dependency,
)
from app.models.card import PhotoFace
from app.models.requests import OCR_TEXT_MAX_LENGTH, UpdateWalletDisplayRequest
from app.models.user_card import (
    ParsedUserCardPreview,
    ParseUserCardRequest,
    ReorderUserCardsRequest,
    UserCardCreate,
    UserCardResponse,
    UserCardUpdate,
)
from app.services.scan_image_service import ScanImageService
from app.services.user_card_service import UserCardNotFoundError, UserCardService
from app.utils.multipart_files import optional_upload_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user-cards", tags=["user-cards"])

MAX_SCAN_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_SCAN_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


def get_user_card_service(
    collection: AsyncIOMotorCollection = Depends(get_user_cards_collection_dependency),
    scan_image_service: ScanImageService = Depends(get_scan_image_service_dependency),
) -> UserCardService:
    return UserCardService(collection=collection, scan_image_service=scan_image_service)


@router.get(
    "",
    response_model=list[UserCardResponse],
    summary="List the authenticated user's own business cards",
)
async def list_user_cards(
    owner_user_id: str = Depends(get_current_user_id),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> list[UserCardResponse]:
    try:
        return await user_card_service.list_for_user(owner_user_id)
    except CardPersistenceError as exc:
        logger.error("Database error while listing user cards: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load user cards.",
        ) from exc


@router.post(
    "",
    response_model=UserCardResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user business card",
)
async def create_user_card(
    payload: UserCardCreate,
    owner_user_id: str = Depends(get_current_user_id),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> UserCardResponse:
    try:
        return await user_card_service.create(owner_user_id, payload)
    except CardPersistenceError as exc:
        logger.error("Database error while creating user card: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user card.",
        ) from exc


@router.patch(
    "/reorder",
    response_model=list[UserCardResponse],
    summary="Reorder user business cards (first id becomes primary)",
)
async def reorder_user_cards(
    payload: ReorderUserCardsRequest,
    owner_user_id: str = Depends(get_current_user_id),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> list[UserCardResponse]:
    try:
        return await user_card_service.reorder(owner_user_id, payload.ordered_ids)
    except CardPersistenceError as exc:
        logger.error("Database error while reordering user cards: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/parse",
    response_model=ParsedUserCardPreview,
    summary="Parse OCR text into user card fields (preview only)",
)
async def parse_user_card(
    payload: ParseUserCardRequest,
    _: str = Depends(enforce_llm_rate_limit),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> ParsedUserCardPreview:
    try:
        return await user_card_service.parse_preview(payload.raw_ocr_text)
    except OpenRouterTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="LLM parsing service timed out. Please try again.",
        ) from exc
    except OpenRouterSafetyError as exc:
        logger.warning("OCR safety validation failed while parsing user card: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OCR text is not valid for card parsing.",
        ) from exc
    except OpenRouterError as exc:
        logger.error("OpenRouter error while parsing user card: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM parsing service is unavailable.",
        ) from exc


@router.post(
    "/process",
    response_model=UserCardResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Parse OCR text and create a user business card with optional scan image",
)
async def process_user_card(
    raw_ocr_text: str = Form(..., min_length=1, max_length=OCR_TEXT_MAX_LENGTH),
    scan_image: UploadFile | str | None = File(None),
    scan_image_base64: str | None = Form(None),
    scan_image_back: UploadFile | str | None = File(None),
    scan_image_back_base64: str | None = Form(None),
    design_id: str = Form("classic"),
    is_primary: bool = Form(False),
    enhance_scan_image: bool = Form(True),
    owner_user_id: str = Depends(enforce_llm_rate_limit),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> UserCardResponse:
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
        return await user_card_service.process_and_save(
            owner_user_id=owner_user_id,
            raw_ocr_text=raw_ocr_text,
            scan_image_bytes=scan_image_bytes,
            scan_image_content_type=scan_image_content_type,
            scan_image_back_bytes=scan_image_back_bytes,
            scan_image_back_content_type=scan_image_back_content_type,
            design_id=design_id.strip() or "classic",
            is_primary=is_primary,
            enhance_scan_image=enhance_scan_image,
        )
    except OpenRouterTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="LLM parsing service timed out. Please try again.",
        ) from exc
    except OpenRouterSafetyError as exc:
        logger.warning("OCR safety validation failed while processing user card: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OCR text is not valid for card parsing.",
        ) from exc
    except OpenRouterError as exc:
        logger.error("OpenRouter error while processing user card: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM parsing service is unavailable.",
        ) from exc
    except CardPersistenceError as exc:
        logger.error("Database error while saving user card: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save user card.",
        ) from exc


@router.patch(
    "/{card_id}/wallet-display",
    response_model=UserCardResponse,
    summary="Set display mode for a user card (photo scan vs design template)",
)
async def update_user_card_wallet_display(
    card_id: str,
    payload: UpdateWalletDisplayRequest,
    owner_user_id: str = Depends(get_current_user_id),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> UserCardResponse:
    try:
        return await user_card_service.update_wallet_display(
            card_id=card_id,
            owner_user_id=owner_user_id,
            wallet_display=payload.wallet_display,
            photo_face=payload.photo_face,
        )
    except UserCardNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CardPersistenceError as exc:
        logger.error("Failed to update user card wallet display: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/{card_id}/scan-image-enhancement/retry",
    response_model=UserCardResponse,
    summary="Generate a new AI-cleaned scan preview",
)
async def retry_user_card_scan_image_enhancement(
    card_id: str,
    owner_user_id: str = Depends(enforce_llm_rate_limit),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> UserCardResponse:
    try:
        return await user_card_service.retry_scan_image_enhancement(card_id, owner_user_id)
    except UserCardNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CardPersistenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{card_id}/scan-image-enhancement/confirm",
    response_model=UserCardResponse,
    summary="Use the staged AI-cleaned scan",
)
async def confirm_user_card_scan_image_enhancement(
    card_id: str,
    owner_user_id: str = Depends(get_current_user_id),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> UserCardResponse:
    try:
        return await user_card_service.confirm_scan_image_enhancement(card_id, owner_user_id)
    except UserCardNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CardPersistenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{card_id}/scan-image-enhancement/discard",
    response_model=UserCardResponse,
    summary="Discard the AI-cleaned preview and use the original scan",
)
async def discard_user_card_scan_image_enhancement(
    card_id: str,
    owner_user_id: str = Depends(get_current_user_id),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> UserCardResponse:
    try:
        return await user_card_service.discard_scan_image_enhancement(card_id, owner_user_id)
    except UserCardNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CardPersistenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get(
    "/{card_id}/scan-image/{face}/pending",
    summary="Download a staged AI-cleaned scan preview",
)
async def get_user_card_pending_scan_image(
    card_id: str,
    face: PhotoFace,
    owner_user_id: str = Depends(get_current_user_id),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> Response:
    try:
        image_bytes, content_type = await user_card_service.get_pending_scan_image(
            card_id,
            owner_user_id,
            face=face,
        )
    except ScanImageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(content=image_bytes, media_type=content_type)


@router.get(
    "/{card_id}/scan-image",
    summary="Download the scanned image for a user business card",
    responses={
        200: {
            "content": {"image/jpeg": {}},
            "description": "Scan image bytes",
        }
    },
)
async def get_user_card_scan_image(
    card_id: str,
    owner_user_id: str = Depends(get_current_user_id),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> Response:
    try:
        image_bytes, content_type = await user_card_service.get_scan_image(
            card_id,
            owner_user_id,
            face="front",
        )
    except ScanImageNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan image not found.",
        ) from exc
    except CardPersistenceError as exc:
        logger.error("Database error while loading user card scan image: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load scan image.",
        ) from exc

    return Response(content=image_bytes, media_type=content_type)


@router.get(
    "/{card_id}/scan-image/{face}",
    summary="Download the scanned image for a user business card by face",
    responses={
        200: {
            "content": {"image/jpeg": {}},
            "description": "Scan image bytes",
        }
    },
)
async def get_user_card_scan_image_by_face(
    card_id: str,
    face: PhotoFace,
    owner_user_id: str = Depends(get_current_user_id),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> Response:
    try:
        image_bytes, content_type = await user_card_service.get_scan_image(
            card_id,
            owner_user_id,
            face=face,
        )
    except ScanImageNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan image not found.",
        ) from exc
    except CardPersistenceError as exc:
        logger.error("Database error while loading user card scan image: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load scan image.",
        ) from exc

    return Response(content=image_bytes, media_type=content_type)


@router.put(
    "/{card_id}",
    response_model=UserCardResponse,
    summary="Update a user business card",
)
async def update_user_card(
    card_id: str,
    payload: UserCardUpdate,
    owner_user_id: str = Depends(get_current_user_id),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> UserCardResponse:
    try:
        return await user_card_service.update(owner_user_id, card_id, payload)
    except UserCardNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CardPersistenceError as exc:
        logger.error("Database error while updating user card %s: %s", card_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user card.",
        ) from exc


@router.delete(
    "/{card_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user business card",
)
async def delete_user_card(
    card_id: str,
    owner_user_id: str = Depends(get_current_user_id),
    user_card_service: UserCardService = Depends(get_user_card_service),
) -> None:
    try:
        await user_card_service.delete(owner_user_id, card_id)
    except UserCardNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CardPersistenceError as exc:
        logger.error("Database error while deleting user card %s: %s", card_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete user card.",
        ) from exc
