from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.handlers.common import show
from app.db import repo
from app.db.models import User

router = Router(name="notifications")


async def _render(target: Message | CallbackQuery, session: AsyncSession, user_id: int) -> None:
    user = await session.get(User, user_id)
    filters = list(await repo.list_filters(session, user_id))
    stats = await repo.user_stats(session, user_id)
    enabled = bool(user and user.notifications_enabled)

    lines = [
        "🔔 <b>Мои уведомления</b>",
        "",
        f"Статус: {'✅ включены' if enabled else '🔕 выключены'}",
        f"Активных фильтров: {stats.filters_active} из {stats.filters_total}",
        f"Получено уведомлений: {stats.notifications_received}",
        "",
        "Я проверяю рынок периодически (не мгновенно) и присылаю каждое подходящее предложение один раз.",
    ]
    if not filters:
        lines.append("\nУ вас нет фильтров — создайте их в «⚙️ Настройки фильтров».")
    await show(target, "\n".join(lines), kb.notifications_panel(enabled, filters))


@router.message(F.text == kb.BTN_NOTIFICATIONS)
async def notifications_menu(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await _render(message, session, message.from_user.id)


@router.callback_query(kb.NotifyCb.filter(F.action == "toggle"))
async def notifications_toggle(query: CallbackQuery, session: AsyncSession) -> None:
    user = await session.get(User, query.from_user.id)
    if user is not None:  # always present: the DB middleware upserts the sender
        user.notifications_enabled = not user.notifications_enabled
    await _render(query, session, query.from_user.id)
