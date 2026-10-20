from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import BTN_STATS
from app.config import Settings
from app.db import repo

router = Router(name="stats")

SOURCE_TITLES = {"mock": "демо-данные (mock)", "telegram": "маркетплейс Telegram", "mrkt": "MRKT"}


@router.message(F.text == BTN_STATS)
async def stats(message: Message, session: AsyncSession, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    mine = await repo.user_stats(session, message.from_user.id)
    total = await repo.global_stats(session)
    last_poll = total.last_poll_at.strftime("%d.%m.%Y %H:%M UTC") if total.last_poll_at else "ещё не было"
    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        "<b>Ваша:</b>\n"
        f"• фильтров: {mine.filters_total} (активных: {mine.filters_active})\n"
        f"• получено уведомлений: {mine.notifications_received}\n\n"
        "<b>Общая:</b>\n"
        f"• пользователей: {total.users}\n"
        f"• активных фильтров: {total.active_filters}\n"
        f"• известных предложений: {total.listings_known}\n"
        f"• отправлено уведомлений: {total.notifications_sent}\n"
        f"• последний опрос рынка: {last_poll}\n"
        f"• источник данных: {', '.join(SOURCE_TITLES[name] for name in settings.sources)}"
    )
