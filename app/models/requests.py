from pydantic import BaseModel, ConfigDict, Field

from app.models.card import CoreFields, PhotoFace, WalletDisplay

OCR_TEXT_MAX_LENGTH = 1500
OCR_TEXT_MAX_LINES = 35


class ProcessCardRequest(BaseModel):
    raw_ocr_text: str = Field(
        ...,
        min_length=1,
        max_length=OCR_TEXT_MAX_LENGTH,
        description="Raw OCR text from on-device extraction",
    )


class UpdateWalletDisplayRequest(BaseModel):
    wallet_display: WalletDisplay | None = None
    photo_face: PhotoFace | None = None


class CapturedCardUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_fields: CoreFields | None = None
    custom_fields: dict[str, str] | None = None


class ManualCardCreate(BaseModel):
    """A contact typed in by hand, with no card photo and no OCR text."""

    model_config = ConfigDict(extra="forbid")

    core_fields: CoreFields
    custom_fields: dict[str, str] = Field(default_factory=dict)


class ApplyEnhancementRequest(BaseModel):
    accept_all: bool = False
    accepted_fields: list[str] = Field(default_factory=list)
    accepted_overrides: dict[str, str] = Field(default_factory=dict)
