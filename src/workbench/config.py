from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://creditunder:creditunder@localhost:5432/creditunder"
    database_read_url: str = ""

    dms_base_url: str = "http://localhost:8001"
    crm_base_url: str = "http://localhost:8003"
    cors_origins: str = "*"

    @property
    def effective_read_url(self) -> str:
        return self.database_read_url or self.database_url


settings = Settings()
