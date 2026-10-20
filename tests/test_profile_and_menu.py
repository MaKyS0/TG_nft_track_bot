from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetMe

from app.bot import keyboards as kb
from app.bot.profile import DESCRIPTION, SHORT_DESCRIPTION, configure_profile
from app.config import Settings


def test_main_menu_has_webapp_button_only_when_configured():
    plain = kb.main_menu()
    assert all(b.web_app is None for row in plain.keyboard for b in row)

    menu = kb.main_menu("https://app.example.com")
    first = menu.keyboard[0][0]
    assert first.text == kb.BTN_WEBAPP and first.web_app.url == "https://app.example.com"
    assert len(menu.keyboard) == len(plain.keyboard) + 1  # classic buttons are all still there


def test_webapp_url_must_be_https_and_is_normalised():
    with pytest.raises(ValueError, match="https"):
        Settings(bot_token="1:x", database_url="x", webapp_url="http://insecure.example.com")
    assert Settings(bot_token="1:x", database_url="x", webapp_url="https://a.example.com/").webapp_url == "https://a.example.com"


def fake_bot(name="NFT Tracker", description=DESCRIPTION, short=SHORT_DESCRIPTION):
    bot = AsyncMock()
    bot.get_my_name.return_value = SimpleNamespace(name=name)
    bot.get_my_description.return_value = SimpleNamespace(description=description)
    bot.get_my_short_description.return_value = SimpleNamespace(short_description=short)
    return bot


async def test_profile_is_updated_when_it_differs():
    bot = fake_bot(name="NFTTrackerBOT", description="", short="")
    await configure_profile(bot, "NFT Tracker", "https://app.example.com")
    bot.set_my_name.assert_awaited_once_with(name="NFT Tracker")
    bot.set_my_description.assert_awaited_once()
    bot.set_my_short_description.assert_awaited_once()
    menu = bot.set_chat_menu_button.await_args.kwargs["menu_button"]
    assert menu.web_app.url == "https://app.example.com" and menu.text == "NFT Tracker"


async def test_profile_calls_are_skipped_when_already_correct():
    """Telegram rate-limits profile edits, so an unchanged profile must cause no write calls."""
    bot = fake_bot()
    await configure_profile(bot, "NFT Tracker", "")
    bot.set_my_name.assert_not_awaited()
    bot.set_my_description.assert_not_awaited()
    bot.set_my_short_description.assert_not_awaited()


async def test_menu_button_is_reset_when_the_mini_app_is_off():
    bot = fake_bot()
    await configure_profile(bot, "NFT Tracker", "")
    assert type(bot.set_chat_menu_button.await_args.kwargs["menu_button"]).__name__ == "MenuButtonDefault"


async def test_profile_errors_are_not_fatal():
    bot = fake_bot(name="old")
    bot.set_my_name.side_effect = TelegramBadRequest(method=GetMe(), message="Too Many Requests")
    await configure_profile(bot, "NFT Tracker", "https://app.example.com")  # must not raise


# ── avatar ─────────────────────────────────────────────────────────────────


def _avatar_bot(photo_count):
    bot = fake_bot()
    bot.id = 8489614617
    bot.get_user_profile_photos.return_value = SimpleNamespace(total_count=photo_count)
    return bot


async def test_avatar_is_set_only_when_the_bot_has_no_photo():
    from app.bot.profile import AVATAR_PATH

    bot = _avatar_bot(0)
    await configure_profile(bot, "NFT Tracker", "")
    bot.set_my_profile_photo.assert_awaited_once()
    photo = bot.set_my_profile_photo.await_args.kwargs["photo"]
    assert type(photo).__name__ == "InputProfilePhotoStatic" and AVATAR_PATH.is_file()

    existing = _avatar_bot(1)  # a photo already there (e.g. set by hand) is never replaced
    await configure_profile(existing, "NFT Tracker", "")
    existing.set_my_profile_photo.assert_not_awaited()


async def test_avatar_problems_are_not_fatal_and_missing_file_is_skipped(tmp_path):
    bot = _avatar_bot(0)
    bot.set_my_profile_photo.side_effect = TelegramBadRequest(method=GetMe(), message="PHOTO_INVALID")
    await configure_profile(bot, "NFT Tracker", "")  # must not raise

    skipped = _avatar_bot(0)
    await configure_profile(skipped, "NFT Tracker", "", avatar=tmp_path / "missing.png")
    skipped.get_user_profile_photos.assert_not_awaited()


def test_bundled_avatar_is_a_square_png_telegram_accepts():
    import struct

    from app.bot.profile import AVATAR_PATH

    data = AVATAR_PATH.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert width == height >= 512 and len(data) < 5_000_000
