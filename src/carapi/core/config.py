from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CARAPI_", env_file=".env", extra="ignore")

    app_name: str = "car-api"
    database_url: str = "sqlite+pysqlite:///:memory:"
    sql_echo: bool = False
    log_level: str = "INFO"

    # Comma-separated in env: CARAPI_API_KEYS=key1,key2. Empty => all writes rejected.
    api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)

    rate_limit_enabled: bool = True
    rate_limit_requests: Annotated[int, Field(ge=1)] = 60
    rate_limit_window_seconds: Annotated[float, Field(gt=0)] = 60.0

    # CSV import limits (POST /cars/import) and optional startup seed file.
    import_max_bytes: Annotated[int, Field(ge=1)] = 5_000_000
    import_max_rows: Annotated[int, Field(ge=1)] = 10_000
    seed_file: Path | None = None

    @field_validator("api_keys", mode="before")
    @classmethod
    def _split_keys(cls, v: object) -> object:
        if isinstance(v, str):
            return [k.strip() for k in v.split(",") if k.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
