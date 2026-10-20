from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: `TG_API_ID=` (as in .env.example / docker-compose) means "not set", not "invalid int".
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    bot_token: SecretStr
    database_url: str
    redis_url: str = ""

    gift_source: Literal["mock", "telethon"] = "mock"
    tg_api_id: int | None = None
    tg_api_hash: SecretStr | None = None
    tg_session_string: SecretStr | None = None

    poll_interval_seconds: float = 60
    source_request_delay: float = 1.5
    listings_per_collection: int = 50
    catalog_ttl_seconds: float = 900
    search_fresh_minutes: int = 30

    max_filters_per_user: int = 10
    notify_rate_per_second: float = 20

    log_level: str = "INFO"

    @model_validator(mode="after")
    def _check_telethon(self) -> "Settings":
        if self.gift_source == "telethon":
            missing = [
                name
                for name, value in (
                    ("TG_API_ID", self.tg_api_id),
                    ("TG_API_HASH", self.tg_api_hash),
                    ("TG_SESSION_STRING", self.tg_session_string),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"GIFT_SOURCE=telethon requires: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
