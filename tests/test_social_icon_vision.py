"""Vision pass that reads social-media icons off a card image.

The network call is thin; the risk lives in trusting what a model returns, so
these tests focus on the parser that maps a response onto custom_fields.
"""

import pytest

from app.core.config import Settings
from app.services.openrouter import (
    _ICON_SERVICE_FIELD_KEYS,
    OpenRouterService,
    _strip_json_fence,
)

MAX_LEN = 500


def parse(data):
    return OpenRouterService._parse_icon_payload(data, max_value_length=MAX_LEN)


class TestParseIconPayload:
    def test_maps_wechat_and_whatsapp_to_the_keys_the_app_reads(self):
        result = parse(
            {
                "icons": [
                    {"service": "wechat", "handle": "ILIAJEWELLERY"},
                    {"service": "whatsapp", "handle": "+852 9257 0306"},
                ]
            }
        )
        assert result == {"wechat_id": "ILIAJEWELLERY", "WhatsApp": "+852 9257 0306"}

    def test_takes_only_wechat_from_a_shared_handle(self):
        # The ILIA card: facebook, instagram and wechat all point at one handle.
        # We store the WeChat one and drop the rest.
        result = parse(
            {
                "icons": [
                    {"service": "facebook", "handle": "ILIAJEWELLERY"},
                    {"service": "instagram", "handle": "ILIAJEWELLERY"},
                    {"service": "wechat", "handle": "ILIAJEWELLERY"},
                ]
            }
        )
        assert result == {"wechat_id": "ILIAJEWELLERY"}

    def test_recognised_but_unstored_services_are_dropped(self):
        # They remain valid inputs so the model has somewhere correct to put a
        # Facebook logo, instead of forcing it into wechat or whatsapp.
        for service in ("facebook", "instagram", "line", "other"):
            assert parse({"icons": [{"service": service, "handle": "x"}]}) == {}

    def test_only_wechat_and_whatsapp_are_ever_stored(self):
        assert set(_ICON_SERVICE_FIELD_KEYS.values()) == {"wechat_id", "WhatsApp"}

    def test_rejects_a_handle_that_is_itself_a_service_name(self):
        # The caption trap that produced WhatsApp: "WeChat" on a real card.
        assert parse({"icons": [{"service": "whatsapp", "handle": "WeChat"}]}) == {}
        assert parse({"icons": [{"service": "wechat", "handle": "微信"}]}) == {}
        assert parse({"icons": [{"service": "wechat", "handle": "QR Code"}]}) == {}

    def test_ignores_unknown_services(self):
        assert parse({"icons": [{"service": "telegram", "handle": "andy"}]}) == {}
        assert parse({"icons": [{"service": "", "handle": "andy"}]}) == {}

    def test_first_value_wins_for_a_repeated_service(self):
        result = parse(
            {
                "icons": [
                    {"service": "wechat", "handle": "first"},
                    {"service": "wechat", "handle": "second"},
                ]
            }
        )
        assert result == {"wechat_id": "first"}

    def test_drops_blank_and_oversized_handles(self):
        assert parse({"icons": [{"service": "wechat", "handle": "   "}]}) == {}
        assert parse({"icons": [{"service": "wechat", "handle": "x" * (MAX_LEN + 1)}]}) == {}

    def test_survives_malformed_responses(self):
        for payload in (None, [], "nope", {}, {"icons": None}, {"icons": "no"}):
            assert parse(payload) == {}
        assert parse({"icons": [None, 5, "x"]}) == {}

    def test_caps_the_number_of_icons_considered(self):
        many = {"icons": [{"service": "wechat", "handle": f"h{i}"} for i in range(100)]}
        assert parse(many) == {"wechat_id": "h0"}

    def test_every_mapped_service_is_an_allowed_service(self):
        from app.services.openrouter import _ALLOWED_ICON_SERVICES

        assert set(_ICON_SERVICE_FIELD_KEYS).issubset(_ALLOWED_ICON_SERVICES)

    def test_keys_match_what_the_mobile_app_reads(self):
        # src/utils/customFieldKeys.ts reads wechat_id; CardDetailScreen reads WhatsApp.
        assert _ICON_SERVICE_FIELD_KEYS["wechat"] == "wechat_id"
        assert _ICON_SERVICE_FIELD_KEYS["whatsapp"] == "WhatsApp"


class TestStripJsonFence:
    def test_removes_markdown_fences_some_models_add(self):
        assert _strip_json_fence('```json\n{"icons": []}\n```') == '{"icons": []}'
        assert _strip_json_fence('```\n{"icons": []}\n```') == '{"icons": []}'

    def test_leaves_plain_json_untouched(self):
        assert _strip_json_fence('{"icons": []}') == '{"icons": []}'


class TestVisionGating:
    @pytest.mark.asyncio
    async def test_returns_empty_when_disabled(self):
        service = OpenRouterService(
            Settings(openrouter_vision_enabled=False, openrouter_api_key="k")
        )
        assert await service.detect_social_handles(b"imagebytes") == {}

    @pytest.mark.asyncio
    async def test_returns_empty_without_an_api_key(self):
        service = OpenRouterService(
            Settings(openrouter_vision_enabled=True, openrouter_api_key="")
        )
        assert await service.detect_social_handles(b"imagebytes") == {}

    @pytest.mark.asyncio
    async def test_returns_empty_for_an_empty_image(self):
        service = OpenRouterService(
            Settings(openrouter_vision_enabled=True, openrouter_api_key="k")
        )
        assert await service.detect_social_handles(b"") == {}


class TestDefaults:
    def test_vision_is_off_by_default(self):
        # Ships dark: enabling it is a deliberate deployment decision.
        assert Settings().openrouter_vision_enabled is False

    def test_default_vision_model_is_a_chat_model_not_an_image_generator(self):
        # x-ai/grok-imagine-* generates images and cannot answer questions
        # about one; picking it here would fail at runtime, not at startup.
        assert "imagine" not in Settings().openrouter_vision_model


class TestSocialFieldsStillMissing:
    """The gate deciding whether a vision call is worth making, per service."""

    @staticmethod
    def missing(fields):
        from app.services.card_service import CardService

        return CardService._social_fields_still_missing(fields)

    def test_both_missing_on_a_card_with_neither(self):
        assert self.missing({}) == {"wechat_id", "WhatsApp"}

    def test_finding_whatsapp_in_text_does_not_stop_the_wechat_search(self):
        # Regression: an `any()` check here skipped vision entirely, so a card
        # naming WhatsApp in text lost its icon-only WeChat handle.
        assert self.missing({"WhatsApp": "+852 9257 0306"}) == {"wechat_id"}

    def test_finding_wechat_in_text_does_not_stop_the_whatsapp_search(self):
        assert self.missing({"wechat_id": "LCCPAHK"}) == {"WhatsApp"}

    def test_no_call_when_both_are_already_known(self):
        assert self.missing({"wechat_id": "LCCPAHK", "WhatsApp": "+852 1"}) == set()

    def test_a_wechat_qr_covers_wechat_but_not_whatsapp(self):
        # Regression: this used to return early and skip WhatsApp too.
        assert self.missing({"wechat_qr_url": "https://u.wechat.com/X"}) == {"WhatsApp"}

    def test_a_wechat_qr_plus_whatsapp_needs_no_call(self):
        assert self.missing(
            {"wechat_qr_url": "https://u.wechat.com/X", "WhatsApp": "+852 1"}
        ) == set()

    def test_blank_and_none_values_count_as_missing(self):
        assert self.missing({"wechat_id": "   ", "WhatsApp": None}) == {
            "wechat_id",
            "WhatsApp",
        }


class _FakeScanImages:
    def __init__(self):
        self.reads = []

    async def read(self, image_id):
        self.reads.append(image_id)
        return (b"bytes-" + image_id.encode(), "image/jpeg")


class _FakeOpenRouter:
    """Returns per-image icon results, recording which images were asked about."""

    def __init__(self, by_image):
        self.by_image = by_image
        self.calls = []

    async def detect_social_handles(self, image_bytes, content_type="image/jpeg"):
        key = image_bytes.decode().removeprefix("bytes-")
        self.calls.append(key)
        return self.by_image.get(key, {})


def _make_service(openrouter, scan_images):
    from app.services.card_service import CardService

    service = CardService.__new__(CardService)
    service._openrouter = openrouter
    service._scan_images = scan_images
    return service


def _parsed(custom=None):
    from app.models.card import CapturedCardBase

    return CapturedCardBase(
        core_fields={"name": "Joelle Ho"}, custom_fields=custom or {}
    )


@pytest.fixture
def vision_on(monkeypatch):
    from app.core.config import Settings
    import app.services.card_service as cs

    monkeypatch.setattr(
        cs, "get_settings", lambda: Settings(openrouter_vision_enabled=True)
    )


@pytest.mark.usefixtures("vision_on")
class TestTwoSidedCards:
    @pytest.mark.asyncio
    async def test_reads_icons_printed_on_the_back(self):
        # The front carries the contact details, the back the social icons.
        openrouter = _FakeOpenRouter({"back1": {"wechat_id": "LCCPAHK"}})
        service = _make_service(openrouter, _FakeScanImages())
        parsed = _parsed()
        await service._augment_with_social_icons(
            {"scan_image_front_id": "front1", "scan_image_back_id": "back1"}, parsed
        )
        assert parsed.custom_fields["wechat_id"] == "LCCPAHK"
        assert openrouter.calls == ["front1", "back1"]

    @pytest.mark.asyncio
    async def test_skips_the_back_once_everything_was_found_on_the_front(self):
        openrouter = _FakeOpenRouter(
            {"front1": {"wechat_id": "A", "WhatsApp": "+852 1"}}
        )
        service = _make_service(openrouter, _FakeScanImages())
        await service._augment_with_social_icons(
            {"scan_image_front_id": "front1", "scan_image_back_id": "back1"}, _parsed()
        )
        assert openrouter.calls == ["front1"]

    @pytest.mark.asyncio
    async def test_combines_one_service_per_side(self):
        openrouter = _FakeOpenRouter(
            {"front1": {"WhatsApp": "+852 1"}, "back1": {"wechat_id": "LCCPAHK"}}
        )
        service = _make_service(openrouter, _FakeScanImages())
        parsed = _parsed()
        await service._augment_with_social_icons(
            {"scan_image_front_id": "front1", "scan_image_back_id": "back1"}, parsed
        )
        assert parsed.custom_fields == {"WhatsApp": "+852 1", "wechat_id": "LCCPAHK"}

    @pytest.mark.asyncio
    async def test_front_wins_when_both_sides_disagree(self):
        openrouter = _FakeOpenRouter(
            {"front1": {"wechat_id": "FRONT"}, "back1": {"wechat_id": "BACK"}}
        )
        service = _make_service(openrouter, _FakeScanImages())
        parsed = _parsed()
        await service._augment_with_social_icons(
            {"scan_image_front_id": "front1", "scan_image_back_id": "back1"}, parsed
        )
        assert parsed.custom_fields["wechat_id"] == "FRONT"

    @pytest.mark.asyncio
    async def test_a_failing_side_does_not_stop_the_other(self):
        class Flaky(_FakeOpenRouter):
            async def detect_social_handles(self, image_bytes, content_type="image/jpeg"):
                key = image_bytes.decode().removeprefix("bytes-")
                self.calls.append(key)
                if key == "front1":
                    raise RuntimeError("vision exploded")
                return {"wechat_id": "LCCPAHK"}

        service = _make_service(Flaky({}), _FakeScanImages())
        parsed = _parsed()
        await service._augment_with_social_icons(
            {"scan_image_front_id": "front1", "scan_image_back_id": "back1"}, parsed
        )
        assert parsed.custom_fields["wechat_id"] == "LCCPAHK"

    @pytest.mark.asyncio
    async def test_front_only_card_still_works(self):
        openrouter = _FakeOpenRouter({"front1": {"wechat_id": "A"}})
        service = _make_service(openrouter, _FakeScanImages())
        parsed = _parsed()
        await service._augment_with_social_icons({"scan_image_front_id": "front1"}, parsed)
        assert parsed.custom_fields["wechat_id"] == "A"

    @pytest.mark.asyncio
    async def test_never_overwrites_a_value_the_text_pass_found(self):
        openrouter = _FakeOpenRouter({"front1": {"wechat_id": "FROM_ICON"}})
        service = _make_service(openrouter, _FakeScanImages())
        parsed = _parsed({"wechat_id": "FROM_TEXT"})
        await service._augment_with_social_icons({"scan_image_front_id": "front1"}, parsed)
        assert parsed.custom_fields["wechat_id"] == "FROM_TEXT"
        assert openrouter.calls == ["front1"]  # still looked for WhatsApp
