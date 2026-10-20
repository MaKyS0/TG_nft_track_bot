from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.handlers.common import show
from app.bot.parsing import InvalidValue, parse_field_value
from app.bot.states import FilterEdit
from app.config import Settings
from app.db import repo
from app.db.models import UserFilter
from app.formatting import FIELD_LABELS, format_filter

router = Router(name="filters")

FIELD_HINTS = {
    "collection": "название коллекции, например <code>Plush Pepe</code>",
    "model": "название модели, например <code>Classic</code>",
    "backdrop": "название фона, например <code>Black</code>",
    "symbol": "название символа, например <code>Stars</code>",
    "min_price": "минимальная цена в Stars, например <code>500</code>",
    "max_price": "максимальная цена в Stars, например <code>5000</code>",
    "min_ton": "минимальная цена в TON, например <code>12.5</code> (для лотов на MRKT и других рынках с TON)",
    "max_ton": "максимальная цена в TON, например <code>40</code>",
    "max_rarity": "порог редкости в промилле (1–1000): подойдут лоты, где модель, фон или символ "
    "редкие не больше этого значения, например <code>20</code> = 2%",
}


async def _show_list(target: Message | CallbackQuery, session: AsyncSession, user_id: int) -> None:
    filters = list(await repo.list_filters(session, user_id))
    if filters:
        text = (
            "⚙️ <b>Ваши фильтры</b>\n\nПодарок подходит, если он удовлетворяет <b>всем</b> условиям одного "
            "из активных фильтров. Выберите фильтр для настройки:"
        )
    else:
        text = "⚙️ <b>Фильтры</b>\n\nУ вас пока нет фильтров. Создайте первый:"
    await show(target, text, kb.filters_list(filters))


async def _show_editor(target: Message | CallbackQuery, f: UserFilter) -> None:
    status = "✅ активен" if f.is_active else "⏸ выключен"
    text = (
        f"⚙️ <b>{escape(f.name)}</b> — {status}\n\n{format_filter(f)}\n\n"
        "Выберите параметр, который хотите изменить. Пустые параметры не ограничивают поиск."
    )
    await show(target, text, kb.filter_editor(f))


# ── menu entry ─────────────────────────────────────────────────────────────


@router.message(F.text == kb.BTN_FILTERS)
async def filters_menu(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await _show_list(message, session, message.from_user.id)


@router.callback_query(kb.FilterCb.filter(F.action == "list"))
async def filters_back(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await _show_list(query, session, query.from_user.id)


# ── create / open / toggle / delete ────────────────────────────────────────


@router.callback_query(kb.FilterCb.filter(F.action == "new"))
async def filter_new(query: CallbackQuery, session: AsyncSession, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    f = await repo.create_filter(session, query.from_user.id, settings.max_filters_per_user)
    if f is None:
        await query.answer(f"Максимум фильтров: {settings.max_filters_per_user}. Удалите ненужные.", show_alert=True)
        return
    await _show_editor(query, f)


@router.callback_query(kb.FilterCb.filter(F.action.in_({"open", "cancel"})))
async def filter_open(
    query: CallbackQuery, callback_data: kb.FilterCb, session: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    f = await repo.get_filter(session, callback_data.id, query.from_user.id)
    if f is None:
        await query.answer("Фильтр не найден", show_alert=True)
        return
    await _show_editor(query, f)


@router.callback_query(kb.FilterCb.filter(F.action == "toggle"))
async def filter_toggle(query: CallbackQuery, callback_data: kb.FilterCb, session: AsyncSession) -> None:
    f = await repo.get_filter(session, callback_data.id, query.from_user.id)
    if f is None:
        await query.answer("Фильтр не найден", show_alert=True)
        return
    f.is_active = not f.is_active
    await _show_editor(query, f)


@router.callback_query(kb.FilterCb.filter(F.action == "ask_delete"))
async def filter_ask_delete(query: CallbackQuery, callback_data: kb.FilterCb, session: AsyncSession) -> None:
    f = await repo.get_filter(session, callback_data.id, query.from_user.id)
    if f is None:
        await query.answer("Фильтр не найден", show_alert=True)
        return
    await show(query, f"Удалить «{escape(f.name)}»?", kb.confirm_delete(f.id))


@router.callback_query(kb.FilterCb.filter(F.action == "delete"))
async def filter_delete(query: CallbackQuery, callback_data: kb.FilterCb, session: AsyncSession) -> None:
    await repo.delete_filter(session, callback_data.id, query.from_user.id)
    await _show_list(query, session, query.from_user.id)


# ── editing a single field ─────────────────────────────────────────────────


@router.callback_query(kb.FilterCb.filter(F.action == "edit"))
async def filter_edit(
    query: CallbackQuery, callback_data: kb.FilterCb, session: AsyncSession, state: FSMContext
) -> None:
    f = await repo.get_filter(session, callback_data.id, query.from_user.id)
    if f is None or callback_data.field not in FIELD_LABELS:
        await query.answer("Фильтр не найден", show_alert=True)
        return
    await state.set_state(FilterEdit.value)
    await state.update_data(filter_id=f.id, field=callback_data.field)
    label = FIELD_LABELS[callback_data.field]
    await show(
        query,
        f"✏️ <b>{label}</b>\n\nОтправьте значение: {FIELD_HINTS[callback_data.field]}.\n"
        "Чтобы сбросить параметр, отправьте <code>-</code>.",
        kb.cancel_edit(f.id),
    )


@router.message(FilterEdit.value, F.text, ~F.text.in_(kb.MENU_BUTTONS), ~F.text.startswith("/"))
async def filter_value(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    f = await repo.get_filter(session, data.get("filter_id", 0), message.from_user.id)
    field = data.get("field", "")
    if f is None or field not in FIELD_LABELS:
        await state.clear()
        await message.answer("Фильтр не найден. Откройте «⚙️ Настройки фильтров».")
        return

    try:
        value = parse_field_value(field, message.text)
        if value is not None and field in {"min_price", "max_price", "min_ton", "max_ton"}:
            pair = {"min_price": "max_price", "max_price": "min_price", "min_ton": "max_ton", "max_ton": "min_ton"}[field]
            other = getattr(f, pair)
            low, high = (value, other) if field.startswith("min") else (other, value)
            if low is not None and high is not None and low > high:
                raise InvalidValue("Минимальная цена не может быть больше максимальной.")
    except InvalidValue as e:
        await message.answer(f"⚠️ {escape(str(e))}\nПопробуйте ещё раз или нажмите «Отмена».", reply_markup=kb.cancel_edit(f.id))
        return

    setattr(f, field, value)
    await state.clear()
    await _show_editor(message, f)
