from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.card import (
    CapturedCardBase,
    CoreFields,
    PhotoFace,
    ScanImageEnhancementStatus,
    WalletDisplay,
)


class DesignType(StrEnum):
    PRESET = "preset"
    CUSTOM = "custom"


class UserCardBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_fields: CoreFields
    custom_fields: dict[str, str] = Field(default_factory=dict)
    design_id: str = Field(default="classic", min_length=1)
    design_type: DesignType = DesignType.PRESET
    custom_background_url: str | None = None

    @field_validator("custom_fields")
    @classmethod
    def normalize_custom_fields(cls, value: dict[str, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, raw in value.items():
            if raw is None:
                continue
            text = str(raw).strip()
            if text:
                normalized[str(key).strip()] = text
        return normalized


class UserCardCreate(UserCardBase):
    is_primary: bool = False


class UserCardUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_fields: CoreFields | None = None
    custom_fields: dict[str, str] | None = None
    design_id: str | None = None
    design_type: DesignType | None = None
    custom_background_url: str | None = None
    is_primary: bool | None = None


class UserCardDocument(UserCardBase):
    owner_user_id: str = Field(..., min_length=1)
    is_primary: bool = False
    sort_order: int = 0
    scan_image_id: str | None = None
    scan_image_front_id: str | None = None
    scan_image_back_id: str | None = None
    scan_image_front_original_id: str | None = None
    scan_image_back_original_id: str | None = None
    scan_image_front_pending_id: str | None = None
    scan_image_back_pending_id: str | None = None
    scan_image_enhancement_status: ScanImageEnhancementStatus = "none"
    scan_image_enhancement_error: str | None = None
    wallet_display: WalletDisplay | None = None
    photo_face: PhotoFace | None = None
    created_at: datetime
    updated_at: datetime


class UserCardResponse(UserCardDocument):
    id: str = Field(..., alias="_id")
    scan_image_url: str | None = Field(
        default=None,
        description="API path to download the scan image (requires Authorization header)",
    )
    scan_image_front_url: str | None = Field(
        default=None,
        description="API path to download the front scan image (requires Authorization header)",
    )
    scan_image_back_url: str | None = Field(
        default=None,
        description="API path to download the back scan image (requires Authorization header)",
    )
    scan_image_front_pending_url: str | None = None
    scan_image_back_pending_url: str | None = None
    scan_image_enhancement_status: ScanImageEnhancementStatus = "none"
    scan_image_enhancement_error: str | None = None
    wallet_display: WalletDisplay = Field(
        description="Card face: photo scan or selected design template (defaults to photo when a scan exists)",
    )
    photo_face: PhotoFace = Field(
        description="Photo face being displayed when wallet display is photo",
    )

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class ReorderUserCardsRequest(BaseModel):
    ordered_ids: list[str] = Field(..., min_length=1)


class ParseUserCardRequest(BaseModel):
    raw_ocr_text: str = Field(..., min_length=1, max_length=1500)


class ParsedUserCardPreview(CapturedCardBase):
    """LLM parse preview for user card setup — not persisted."""
