import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.errors import PyMongoError

from app.core.auth import get_current_user_id, get_current_user_id_strict
from app.core.exceptions import CardPersistenceError
from app.db.mongodb import (
    get_cards_collection,
    get_database,
    get_share_links_collection,
    get_user_cards_collection,
)
from app.services.account_service import AccountService
from app.services.scan_image_service import ScanImageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account", tags=["account"])


def get_account_service() -> AccountService:
    database = get_database()
    return AccountService(
        captured_cards=get_cards_collection(),
        user_cards=get_user_cards_collection(),
        share_links=get_share_links_collection(),
        scan_image_service=ScanImageService(database),
    )


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete the authenticated user's account and all associated data",
)
async def delete_account(
    owner_user_id: str = Depends(get_current_user_id_strict),
    account_service: AccountService = Depends(get_account_service),
) -> None:
    try:
        await account_service.delete_account(owner_user_id)
    except CardPersistenceError as exc:
        logger.warning("Account deletion failed for user %s: %s", owner_user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except PyMongoError as exc:
        logger.exception("Unexpected MongoDB error deleting account for user %s", owner_user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete account.",
        ) from exc
