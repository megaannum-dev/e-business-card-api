from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Allow docker-compose-only vars (e.g. MONGO_ROOT_USERNAME) in the same .env
        extra="ignore",
    )

    app_name: str = "E-Business Card API"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False
    # Deployment target: "dev" or "prod" — set via DEPLOY_ENV in .env (see
    # deploy/.env.dev.example / deploy/.env.production.example). Plain local
    # `docker-compose.yml` doesn't set this, so it defaults to "dev".
    deploy_env: str = "dev"

    mongo_uri: str = "mongodb://mongodb:27017"
    mongo_db_name: str = "e_business_card"
    mongo_cards_collection: str = "captured_cards"
    mongo_user_cards_collection: str = "user_cards"
    mongo_share_links_collection: str = "share_links"
    mongo_llm_rate_limits_collection: str = "llm_rate_limits"

    share_public_base_url: str = "https://focms.megaannum.ai:8001/c"

    openrouter_api_key: str = ""
    # Optional separate key for OpenRouter image models (e.g. US account for openai/*).
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_timeout_seconds: float = 30.0
    openrouter_max_retries: int = 2
    openrouter_max_tokens: int = 2048

    openrouter_api_key_us: str = ""
    openrouter_image_model: str = "google/gemini-3.1-flash-image"
    openrouter_image_provider: str = "google-vertex/global"
    openrouter_image_timeout_seconds: float = 30
    openrouter_image_quality: str = "high"
    openrouter_image_enhancement_enabled: bool = True
    # Total image-generation attempts before falling back to the original scan.
    openrouter_image_max_attempts: int = 3

    ocr_text_max_length: int = 1500
    ocr_text_max_lines: int = 35
    llm_max_custom_fields: int = 30
    llm_max_field_value_length: int = 500

    llm_rate_limit_per_hour: int = 10
    llm_rate_limit_per_day: int = 20

    firebase_credentials_path: str = ""

    @property
    def enable_docs(self) -> bool:
        """Swagger UI (/docs), ReDoc (/redoc) and /openapi.json are only
        served when DEPLOY_ENV=dev. Never expose these on a public prod
        server."""
        return self.deploy_env.strip().lower() == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
