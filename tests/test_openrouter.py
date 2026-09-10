import json
import unittest

from app.services.openrouter import OpenRouterService


def _make_completion_body(custom_fields: dict[str, str]) -> dict:
    content = {
        "core_fields": {
            "name": "Alex Lee",
            "company_name": None,
            "job_title": None,
            "email": None,
            "phone": None,
            "website": None,
        },
        "custom_fields": custom_fields,
    }
    return {"choices": [{"message": {"content": json.dumps(content)}}]}


class OpenRouterWhatsappTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = OpenRouterService.__new__(OpenRouterService)
        from app.core.config import Settings

        self.service._settings = Settings()

    def test_whatsapp_field_is_extracted(self) -> None:
        body = _make_completion_body({"WhatsApp": "+852 9123 4567"})
        card = self.service._parse_completion_response(body)
        self.assertEqual(card.custom_fields.get("WhatsApp"), "+852 9123 4567")

    def test_card_without_whatsapp_has_no_whatsapp_key(self) -> None:
        body = _make_completion_body({"fax": "+852 2222 3333"})
        card = self.service._parse_completion_response(body)
        self.assertNotIn("WhatsApp", card.custom_fields)


class NormalizeCustomFieldKeyTests(unittest.TestCase):
    def test_known_variants_normalize_to_whatsapp(self) -> None:
        variants = [
            "WhatsApp",
            "whatsapp",
            "WHATSAPP",
            "Whats App",
            "whats app",
            "WhatsApp Number",
            "whatsapp number",
            "WhatsApp No.",
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertEqual(
                    OpenRouterService._normalize_custom_field_key(variant),
                    "WhatsApp",
                )


if __name__ == "__main__":
    unittest.main()
