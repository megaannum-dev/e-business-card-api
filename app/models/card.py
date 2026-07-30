from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

WalletDisplay = Literal["photo", "classic"]
PhotoFace = Literal["front", "back"]
ParseStatus = Literal["pending", "parsed", "failed", "fallback"]
ParseSource = Literal["llm", "offline", "manual"]
EnhancementStatus = Literal["none", "queued", "processing", "pending_review", "applied", "failed"]
ScanImageEnhancementStatus = Literal["none", "processing", "preview_ready", "applied", "discarded", "failed"]


class CoreFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Contact full name")
    company_name: str | None = None
    job_title: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    website: str | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be empty")
        return stripped


class CapturedCardBase(BaseModel):
    """Hybrid schema shared by LLM output and persisted documents."""

    model_config = ConfigDict(extra="forbid")

    core_fields: CoreFields
    custom_fields: dict[str, str] = Field(default_factory=dict)

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


class CapturedCardDocument(CapturedCardBase):
    owner_user_id: str = Field(..., min_length=1)
    scanned_at: datetime
    raw_ocr_text: str | None = None
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
    parse_status: ParseStatus = "parsed"
    parse_source: ParseSource = "llm"
    enhancement_status: EnhancementStatus = "none"
    enhanced_suggestions: dict[str, str] = Field(default_factory=dict)
    edited_fields: list[str] = Field(default_factory=list)
    parse_error: str | None = None
    parsed_at: datetime | None = None


class CapturedCardResponse(CapturedCardBase):
    id: str = Field(..., alias="_id")
    owner_user_id: str
    scanned_at: datetime
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
    scan_image_front_pending_url: str | None = Field(
        default=None,
        description="API path to preview the AI-cleaned front scan.",
    )
    scan_image_back_pending_url: str | None = Field(
        default=None,
        description="API path to preview the AI-cleaned back scan.",
    )
    scan_image_enhancement_status: ScanImageEnhancementStatus = "none"
    scan_image_enhancement_error: str | None = None
    wallet_display: WalletDisplay = Field(
        description="Wallet face: photo scan or classic palette (defaults to photo when a scan exists)",
    )
    photo_face: PhotoFace = Field(
        description="Photo face being displayed when wallet display is photo",
    )
    parse_status: ParseStatus = Field(
        default="parsed",
        description="Current parse state for this card record.",
    )
    parse_source: ParseSource = Field(
        default="llm",
        description="Source of the currently applied parsed fields.",
    )
    enhancement_status: EnhancementStatus = Field(
        default="none",
        description="AI enhancement lifecycle state for reviewing suggestions.",
    )
    enhanced_suggestions: dict[str, str] = Field(
        default_factory=dict,
        description="Field suggestions generated during enhancement review flow.",
    )
    parse_error: str | None = Field(
        default=None,
        description="Last parse or enhancement error reason, if any.",
    )
    parsed_at: datetime | None = Field(
        default=None,
        description="Timestamp of the latest successful parse update.",
    )

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)
