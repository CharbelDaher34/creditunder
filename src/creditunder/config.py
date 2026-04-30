from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    database_url: str = "postgresql+asyncpg://creditunder:creditunder@localhost:5432/creditunder"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:19092"
    kafka_topic: str = "credit-applications"
    kafka_consumer_group: str = "creditunder-processor"

    # AI Service — OpenAI-compatible
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = "sk-placeholder"
    ai_model: str = "gpt-4o"
    ai_confidence_threshold: float = 0.7

    # External services
    dms_base_url: str = "http://localhost:8001"
    edw_base_url: str = "http://localhost:8002"


settings = Settings()
