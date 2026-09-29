import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from motor.motor_asyncio import AsyncIOMotorCollection

from app.core.auth import get_current_user_id, get_current_user_id_strict
from app.core.config import Settings, get_settings
from app.core.exceptions import CardPersistenceError, ScanImageNotFoundError
from app.db.mongodb import (
    get_scan_image_service_dependency,
    get_share_links_collection_dependency,
    get_user_cards_collection_dependency,
)
from app.models.card import PhotoFace
from app.models.share_link import ShareLinkResponse, SharedUserCardResponse
from app.services.scan_image_service import ScanImageService
from app.services.share_link_service import ShareLinkNotFoundError, ShareLinkService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user-cards", tags=["share-links"])
public_router = APIRouter(prefix="/public", tags=["share-links-public"])


def get_share_link_service(
    share_links_collection: AsyncIOMotorCollection = Depends(get_share_links_collection_dependency),
    user_cards_collection: AsyncIOMotorCollection = Depends(get_user_cards_collection_dependency),
    scan_image_service: ScanImageService = Depends(get_scan_image_service_dependency),
    settings: Settings = Depends(get_settings),
) -> ShareLinkService:
    return ShareLinkService(
        share_links_collection=share_links_collection,
        user_cards_collection=user_cards_collection,
        settings=settings,
        scan_image_service=scan_image_service,
    )


@router.put(
    "/{card_id}/share-link",
    response_model=ShareLinkResponse,
    summary="Create or get active share link for a user card",
)
async def create_or_get_share_link(
    card_id: str,
    owner_user_id: str = Depends(get_current_user_id_strict),
    service: ShareLinkService = Depends(get_share_link_service),
) -> ShareLinkResponse:
    try:
        return await service.create_or_get_active_link(owner_user_id=owner_user_id, card_id=card_id)
    except ShareLinkNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CardPersistenceError as exc:
        logger.error("Failed to create share link: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.delete(
    "/{card_id}/share-link",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke active share link for a user card",
)
async def revoke_share_link(
    card_id: str,
    owner_user_id: str = Depends(get_current_user_id_strict),
    service: ShareLinkService = Depends(get_share_link_service),
) -> None:
    try:
        await service.deactivate_active_link(owner_user_id=owner_user_id, card_id=card_id)
    except ShareLinkNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CardPersistenceError as exc:
        logger.error("Failed to revoke share link: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@public_router.get(
    "/user-cards/{token}",
    response_model=SharedUserCardResponse,
    summary="Resolve a public share token to a user card payload",
)
async def resolve_shared_user_card(
    token: str,
    service: ShareLinkService = Depends(get_share_link_service),
) -> SharedUserCardResponse:
    try:
        return await service.resolve_shared_card(token)
    except ShareLinkNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CardPersistenceError as exc:
        logger.error("Failed to resolve shared user card: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@public_router.get(
    "/user-cards/{token}/scan-image/{face}",
    summary="Download shared user card scan image by face",
    responses={200: {"content": {"image/jpeg": {}}, "description": "Scan image bytes"}},
)
async def get_shared_scan_image(
    token: str,
    face: PhotoFace,
    service: ShareLinkService = Depends(get_share_link_service),
) -> Response:
    try:
        image_bytes, content_type = await service.get_public_scan_image(token=token, face=face)
        return Response(content=image_bytes, media_type=content_type)
    except ShareLinkNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ScanImageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CardPersistenceError as exc:
        logger.error("Failed to fetch shared scan image: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
