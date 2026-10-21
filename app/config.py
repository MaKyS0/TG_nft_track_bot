import re
from functools import lru_cache

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


KNOWN_SOURCES = {"mock", "telegram", "mrkt", "mrkt_vault"}
_PLACEHOLDERS = ("{id32}", "{id}", "{slug}", "{number}")
_LINK_SHAPE = re.compile(r"https://t\.me/mrkt(?:[/?][A-Za-z0-9_:/?=&.%{}-]*)?")


def is_valid_link_template(template: str) -> bool:
    """A t.me/mrkt link whose only braces are the three known placeholders (never str.format on config)."""
    rest = template
    for token in _PLACEHOLDERS:
        rest = rest.replace(token, "")
    return bool(_LINK_SHAPE.fullmatch(template)) and "{" not in rest and "}" not in rest


def parse_sources(value: str) -> list[str]:
    names = []
    for raw in value.split(","):
        name = raw.strip().lower()
        name = "telegram" if name == "telethon" else name
        if not name:
            continue
        if name not in KNOWN_SOURCES:
            raise ValueError(f"unknown GIFT_SOURCE entry {name!r}; allowed: {', '.join(sorted(KNOWN_SOURCES))}")
        if name not in names:
            names.append(name)
    if not names:
        raise ValueError("GIFT_SOURCE is empty")
    if "mock" in names and len(names) > 1:
        raise ValueError("GIFT_SOURCE=mock cannot be combined with real markets")
    return names


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

    # Comma-separated markets: mock | telegram (alias: telethon) | mrkt. Example: "telegram,mrkt".
    gift_source: str = "mock"
    # MRKT has no documented catalogue endpoint: with "telegram" enabled its collections follow Telegram's;
    # for MRKT alone list them here (comma-separated names).
    mrkt_collections: str = ""
    mrkt_request_delay: float = 2.0
    # Where "Buy on MRKT" leads. MRKT documents no per-gift link, so the default opens the app; once you know
    # the real format (MRKT -> gift -> Share), put it here using {id}, {slug} and {number} as placeholders.
    mrkt_link_template: str = "https://t.me/mrkt/app"
    mrkt_vault_username: str = "mrktbank"  # public account on which MRKT holds deposited gifts
    mrkt_page_size: int = 20
    mrkt_max_pages: int = 3
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
    web_host: str = "127.0.0.1"  # loopback by default; Docker sets 0.0.0.0 (port is published to localhost only)
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

    @field_validator("mrkt_vault_username")
    @classmethod
    def _check_vault_username(cls, value: str) -> str:
        value = value.strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", value):
            raise ValueError("MRKT_VAULT_USERNAME must be a Telegram username (5-32 letters, digits, underscores)")
        return value

    @field_validator("mrkt_link_template")
    @classmethod
    def _check_link_template(cls, value: str) -> str:
        value = value.strip()
        if not is_valid_link_template(value):
            raise ValueError(
                "MRKT_LINK_TEMPLATE must be a https://t.me/mrkt... link; only {id}, {id32}, {slug} and {number} "
                "are allowed as placeholders"
            )
        return value

    @model_validator(mode="after")
    def _check_webapp(self) -> "Settings":
        if self.webapp_url and not self.webapp_url.startswith("https://"):
            raise ValueError("WEBAPP_URL must start with https:// (Telegram requires HTTPS for Mini Apps)")
        self.webapp_url = self.webapp_url.rstrip("/")
        return self

    @property
    def sources(self) -> list[str]:
        return parse_sources(self.gift_source)

    @property
    def mrkt_collection_names(self) -> list[str]:
        return [name.strip() for name in self.mrkt_collections.split(",") if name.strip()]

    @model_validator(mode="after")
    def _check_telethon(self) -> "Settings":
        sources = parse_sources(self.gift_source)
        if "mrkt" in sources and "telegram" not in sources and not self.mrkt_collection_names:
            raise ValueError("GIFT_SOURCE=mrkt without telegram requires MRKT_COLLECTIONS (comma-separated names)")
        if any(s in sources for s in ("telegram", "mrkt", "mrkt_vault")):  # all use the operator's Telegram account
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
                raise ValueError(f"GIFT_SOURCE={self.gift_source} requires: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
