from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.common import show
from app.bot.keyboards import BTN_SEARCH, SearchCb, create_filter_prompt, search_more
from app.config import Settings
from app.formatting import DEAL_NOTE, format_listing
from app.services.search import find_offers

router = Router(name="search")

PAGE_SIZE = 5


async def render_search(
    target: Message | CallbackQuery, session: AsyncSession, user_id: int, settings: Settings, offset: int = 0
) -> None:
    result = await find_offers(session, user_id, settings.search_fresh_minutes)
    if not result.has_filters:
        await show(
            target,
            "У вас нет активных фильтров. Создайте фильтр — и я покажу подходящие предложения.",
            create_filter_prompt(),
        )
        return
    found = result.listings

    page = found[offset : offset + PAGE_SIZE]
    if not page:
        await show(
            target,
            "Пока ничего не найдено.\n\n"
            f"Ищу среди предложений, замеченных за последние {settings.search_fresh_minutes} мин. "
            "Новые подходящие лоты придут уведомлением, если оно включено.",
        )
        return

    cards = "\n\n".join(format_listing(item, index=offset + i + 1) for i, item in enumerate(page))
    header = f"🎁 <b>Найдено: {len(found)}</b> (сначала дешёвые, {offset + 1}–{offset + len(page)})"
    text = f"{header}\n\n{cards}\n\n{DEAL_NOTE}"
    next_offset = offset + PAGE_SIZE if offset + PAGE_SIZE < len(found) else None
    await show(target, text, search_more(next_offset, settings.webapp_url))


@router.message(F.text == BTN_SEARCH)
async def search_button(message: Message, session: AsyncSession, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await render_search(message, session, message.from_user.id, settings)


@router.callback_query(SearchCb.filter())
async def search_page(
    query: CallbackQuery, callback_data: SearchCb, session: AsyncSession, settings: Settings
) -> None:
    await render_search(query, session, query.from_user.id, settings, offset=max(callback_data.offset, 0))
