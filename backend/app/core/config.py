from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    jwt_secret: str = "change-me"
    jwt_alg: str = "HS256"
    jwt_ttl_min: int = 60

    database_url: str = "sqlite:///./epc.db"

    oracle_erp_base_url: str | None = None
    oracle_erp_client_id: str | None = None
    oracle_erp_client_secret: str | None = None

    sap_s4_base_url: str | None = None
    sap_s4_client_id: str | None = None
    sap_s4_client_secret: str | None = None

    procore_base_url: str = "https://api.procore.com"
    procore_token: str | None = None

    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
