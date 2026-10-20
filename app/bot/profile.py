import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile, InputProfilePhotoStatic, MenuButtonDefault, MenuButtonWebApp, WebAppInfo

log = logging.getLogger(__name__)

AVATAR_PATH = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"

DESCRIPTION = (
    "Слежу за маркетплейсом улучшенных Telegram-подарков и пишу, когда появляется предложение "
    "под ваши фильтры: коллекция, модель, фон, символ, цена, редкость."
)
SHORT_DESCRIPTION = "Уведомления о выгодных NFT-подарках Telegram по вашим фильтрам"


async def _ensure_avatar(bot: Bot, path: Path) -> None:
    """Give the bot a profile picture only if it has none, so a photo set by hand is never overwritten."""
    if not path.is_file():
        return
    photos = await bot.get_user_profile_photos(user_id=bot.id, limit=1)
    if photos.total_count == 0:
        await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=FSInputFile(path)))
        log.info("Bot profile picture set")


async def configure_profile(bot: Bot, name: str, webapp_url: str = "", avatar: Path | None = AVATAR_PATH) -> None:
    """Make the bot's public profile match the config. Idempotent: only changes what differs, because
    Telegram rate-limits profile edits. Failures are logged, never fatal."""
    try:
        if (await bot.get_my_name()).name != name:
            await bot.set_my_name(name=name)
        if (await bot.get_my_description()).description != DESCRIPTION:
            await bot.set_my_description(description=DESCRIPTION)
        if (await bot.get_my_short_description()).short_description != SHORT_DESCRIPTION:
            await bot.set_my_short_description(short_description=SHORT_DESCRIPTION)
        if webapp_url:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text=name[:16], web_app=WebAppInfo(url=webapp_url))
            )
        else:  # Mini App is off: do not leave a button pointing at an address that no longer answers
            await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
    except TelegramAPIError as e:
        log.warning("Could not update the bot profile: %s", e)

    if avatar is not None:  # separate try: a picture problem must not hide that the rest succeeded
        try:
            await _ensure_avatar(bot, avatar)
        except TelegramAPIError as e:
            log.info("Could not set the bot profile picture (upload it with @BotFather /setuserpic): %s", e)
