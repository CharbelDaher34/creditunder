import os

from pydantic_settings import BaseSettings, SettingsConfigDict

_OPENAI_OFFICIAL_BASE_URL = "https://api.openai.com/v1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://creditunder:creditunder@localhost:5432/creditunder"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:19092"
    kafka_topic: str = "credit-applications"
    kafka_consumer_group: str = "creditunder-processor"

    # AI Service — OpenAI-compatible
    # If OPENAI_API_KEY is set it always takes priority: base URL is forced to
    # the official OpenAI endpoint regardless of AI_BASE_URL.
    openai_api_key: str = ""
    ai_base_url: str = _OPENAI_OFFICIAL_BASE_URL
    ai_api_key: str = "sk-placeholder"
    ai_model: str = "gpt-4o"
    ai_confidence_threshold: float = 0.7

    # External services
    dms_base_url: str = "http://localhost:8001"
    edw_base_url: str = "http://localhost:8002"

    @property
    def effective_ai_base_url(self) -> str:
        """Returns the official OpenAI base URL when OPENAI_API_KEY is set."""
        if self.openai_api_key:
            return _OPENAI_OFFICIAL_BASE_URL
        return self.ai_base_url

    @property
    def effective_ai_api_key(self) -> str:
        """Returns OPENAI_API_KEY when set, otherwise falls back to AI_API_KEY."""
        return self.openai_api_key if self.openai_api_key else self.ai_api_key


settings = Settings()
