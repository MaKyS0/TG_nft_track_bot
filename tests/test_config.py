import pytest
from pydantic import ValidationError

from app.config import Settings


def make(monkeypatch, **env):
    for key in ("BOT_TOKEN", "DATABASE_URL", "GIFT_SOURCE", "TG_API_ID", "TG_API_HASH", "TG_SESSION_STRING", "REDIS_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_empty_optional_variables_from_env_example_are_treated_as_unset(monkeypatch):
    s = make(
        monkeypatch,
        BOT_TOKEN="1:x",
        DATABASE_URL="sqlite+aiosqlite://",
        GIFT_SOURCE="mock",
        TG_API_ID="",
        TG_API_HASH="",
        TG_SESSION_STRING="",
        REDIS_URL="",
    )
    assert s.tg_api_id is None and s.redis_url == "" and s.gift_source == "mock"


def test_telethon_source_requires_credentials(monkeypatch):
    with pytest.raises(ValidationError, match="TG_SESSION_STRING"):
        make(monkeypatch, BOT_TOKEN="1:x", DATABASE_URL="x", GIFT_SOURCE="telethon", TG_API_ID="1", TG_API_HASH="h")


def test_token_is_not_leaked_by_repr(monkeypatch):
    s = make(monkeypatch, BOT_TOKEN="123:SECRET", DATABASE_URL="x")
    assert "SECRET" not in repr(s)


@pytest.mark.parametrize("pasted", ["8489614617:AAE-x_y12345 ", "  8489614617:AAE-x_y12345\n", "8489614617: AAE-x_y12345"])
def test_token_whitespace_from_pasting_is_stripped(monkeypatch, pasted):
    s = make(monkeypatch, BOT_TOKEN=pasted, DATABASE_URL="x")
    assert s.bot_token.get_secret_value() == "8489614617:AAE-x_y12345"


@pytest.mark.parametrize("bad", ["bot8489614617:AAE", "8489614617", "AAE-x_y12345", "'8489614617:AAE'", "8489614617:AA E!"])
def test_malformed_token_fails_early_without_leaking_it(monkeypatch, bad):
    with pytest.raises(ValidationError) as info:
        make(monkeypatch, BOT_TOKEN=bad, DATABASE_URL="x")
    assert "invalid format" in str(info.value)
    assert "AAE" not in str(info.value).replace("AA...", "")
