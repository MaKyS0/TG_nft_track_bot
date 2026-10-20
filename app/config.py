import re
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: `TG_API_ID=` (as in .env.example / docker-compose) means "not set", not "invalid int".
    # hide_input_in_errors: a validation error must never echo a (malformed) token or session string.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
        hide_input_in_errors=True,
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

    # Display name shown in Telegram; the bot sets it on startup when it differs.
    bot_name: str = "NFT Tracker"

    # Mini App: public HTTPS URL that reaches the built-in web server (empty = mini app disabled).
    webapp_url: str = ""
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    webapp_auth_max_age: int = 86400  # seconds an initData signature stays valid

    @field_validator("bot_token", mode="before")
    @classmethod
    def _clean_token(cls, value):
        # Pasting on a phone often appends a space or newline.
        return "".join(value.split()) if isinstance(value, str) else value

    @field_validator("bot_token")
    @classmethod
    def _check_token_shape(cls, value: SecretStr) -> SecretStr:
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", value.get_secret_value()):
            raise ValueError(
                "BOT_TOKEN has an invalid format: expected something like 123456789:AA... exactly as "
                "@BotFather sent it (no 'bot' prefix, no quotes, no spaces)"
            )
        return value

    @model_validator(mode="after")
    def _check_webapp(self) -> "Settings":
        if self.webapp_url and not self.webapp_url.startswith("https://"):
            raise ValueError("WEBAPP_URL must start with https:// (Telegram requires HTTPS for Mini Apps)")
        self.webapp_url = self.webapp_url.rstrip("/")
        return self

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
