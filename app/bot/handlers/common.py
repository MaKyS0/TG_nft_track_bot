from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, LinkPreviewOptions, Message

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


async def show(target: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Answer a message, or edit the message under an inline button in place."""
    if isinstance(target, CallbackQuery):
        if isinstance(target.message, Message):
            try:
                await target.message.edit_text(text, reply_markup=markup, link_preview_options=NO_PREVIEW)
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    raise
        else:  # message too old / inaccessible: send a fresh one instead
            await target.answer()
            await target.bot.send_message(target.from_user.id, text, reply_markup=markup, link_preview_options=NO_PREVIEW)
            return
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup, link_preview_options=NO_PREVIEW)
