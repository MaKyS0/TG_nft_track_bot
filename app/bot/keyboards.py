from urllib.parse import urlencode

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models import UserFilter
from app.formatting import FIELD_LABELS, filter_button_title

BTN_SEARCH = "🎁 Найти подарки"
BTN_NOTIFICATIONS = "🔔 Мои уведомления"
BTN_FILTERS = "⚙️ Настройки фильтров"
BTN_STATS = "📊 Статистика"
BTN_HELP = "❓ Помощь"
MENU_BUTTONS = {BTN_SEARCH, BTN_NOTIFICATIONS, BTN_FILTERS, BTN_STATS, BTN_HELP}


class FilterCb(CallbackData, prefix="flt"):
    action: str  # list | new | open | edit | toggle | ask_delete | delete | cancel
    id: int = 0
    field: str = ""


class NotifyCb(CallbackData, prefix="ntf"):
    action: str  # toggle


class SearchCb(CallbackData, prefix="srch"):
    offset: int = 0


BTN_WEBAPP = "📱 Мини-приложение"


def main_menu(webapp_url: str = "") -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_SEARCH)],
        [KeyboardButton(text=BTN_NOTIFICATIONS), KeyboardButton(text=BTN_FILTERS)],
        [KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_HELP)],
    ]
    if webapp_url:  # opens the Mini App directly; Telegram sends no text message for this button
        rows.insert(0, [KeyboardButton(text=BTN_WEBAPP, web_app=WebAppInfo(url=webapp_url))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def filters_list(filters: list[UserFilter]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for f in filters:
        b.button(text=filter_button_title(f.name, f, f.is_active), callback_data=FilterCb(action="open", id=f.id))
    b.button(text="➕ Новый фильтр", callback_data=FilterCb(action="new"))
    b.adjust(1)
    return b.as_markup()


def filter_editor(f: UserFilter) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for field, label in FIELD_LABELS.items():
        b.button(text=label, callback_data=FilterCb(action="edit", id=f.id, field=field))
    b.adjust(2)
    toggle = "⏸ Выключить" if f.is_active else "▶️ Включить"
    b.row(
        InlineKeyboardButton(text=toggle, callback_data=FilterCb(action="toggle", id=f.id).pack()),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=FilterCb(action="ask_delete", id=f.id).pack()),
    )
    b.row(InlineKeyboardButton(text="⬅️ К списку", callback_data=FilterCb(action="list").pack()))
    return b.as_markup()


def confirm_delete(filter_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Да, удалить", callback_data=FilterCb(action="delete", id=filter_id))
    b.button(text="Отмена", callback_data=FilterCb(action="open", id=filter_id))
    b.adjust(2)
    return b.as_markup()


def cancel_edit(filter_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=FilterCb(action="cancel", id=filter_id).pack())]]
    )


def notifications_panel(enabled: bool, filters: list[UserFilter]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text="🔕 Выключить уведомления" if enabled else "🔔 Включить уведомления",
        callback_data=NotifyCb(action="toggle"),
    )
    for f in filters:
        b.button(text=filter_button_title(f.name, f, f.is_active), callback_data=FilterCb(action="open", id=f.id))
    b.adjust(1)
    return b.as_markup()


def create_filter_prompt() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="➕ Создать фильтр", callback_data=FilterCb(action="new").pack())]]
    )


BTN_OPEN_APP = "📱 Открыть в мини-приложении"


def webapp_button(webapp_url: str, slug: str | None = None) -> InlineKeyboardButton:
    """Inline button that opens the Mini App (allowed in private chats only, which is all this bot uses).
    With ``slug`` the app opens that offer's card if it is still on sale."""
    url = f"{webapp_url}/?{urlencode({'offer': slug})}" if slug else f"{webapp_url}/"
    return InlineKeyboardButton(text=BTN_OPEN_APP, web_app=WebAppInfo(url=url))


def search_more(next_offset: int | None, webapp_url: str = "") -> InlineKeyboardMarkup | None:
    """Buttons under search results: next page (``None`` when this is the last one) and the Mini App."""
    rows = []
    if next_offset is not None:
        rows.append([InlineKeyboardButton(text="Показать ещё ▶️", callback_data=SearchCb(offset=next_offset).pack())])
    if webapp_url:
        rows.append([webapp_button(webapp_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
