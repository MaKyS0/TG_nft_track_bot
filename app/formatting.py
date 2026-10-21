from html import escape

from app.domain import NANO_TON, STARS, Listing
from app.matching import FilterLike

FIELD_LABELS = {
    "collection": "Коллекция",
    "model": "Модель",
    "backdrop": "Фон",
    "symbol": "Символ",
    "min_price": "Мин. цена (⭐)",
    "max_price": "Макс. цена (⭐)",
    "min_ton": "Мин. цена (TON)",
    "max_ton": "Макс. цена (TON)",
    "max_rarity": "Редкость, не больше",
}

DEAL_NOTE = "<i>ℹ️ Бот сделок не проводит. Цену и наличие проверьте на странице подарка перед покупкой.</i>"


def thousands(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def ton_str(nano: int) -> str:
    whole, frac = divmod(nano, NANO_TON)
    digits = f"{frac:09d}".rstrip("0")
    return f"{thousands(whole)}.{digits}" if digits else thousands(whole)


def format_price(price: int, currency: str) -> str:
    if currency == STARS:
        return f"⭐ {thousands(price)}"
    return f"💎 {ton_str(price)} TON"


def format_rarity(permille: int | None) -> str:
    if permille is None:
        return ""
    return f" ({permille / 10:g}%)".replace(".", ",")


def format_owner(listing: Listing) -> str:
    if listing.owner_url:
        return f'<a href="{escape(listing.owner_url)}">@{escape(listing.owner_username or "")}</a>'
    if listing.owner_name:
        return f"{escape(listing.owner_name)} (публичного профиля нет)"
    return "не указан / скрыт"


def format_listing(listing: Listing, *, filter_name: str | None = None, index: int | None = None) -> str:
    """Card for a chat message or photo caption: name, then the price (what people look for first),
    then the attributes and the owner."""
    prefix = f"{index}. " if index is not None else ""
    if listing.has_price:
        price = f"<b>{format_price(listing.price, listing.currency)}</b>"
        if listing.market_url:  # sold on a third-party market rather than on the gift page
            price += f" на <b>{escape(listing.market_title)}</b>"
    else:  # seen on the market's account, which does not publish the price
        price = f"🏷 Цена: смотрите на <b>{escape(listing.market_title)}</b>"
    lines = [
        f'{prefix}🎁 <a href="{escape(listing.url)}"><b>{escape(listing.collection)}</b> #{listing.number}</a>',
        price,
    ]
    if listing.model:
        lines.append(f"🧩 Модель: {escape(listing.model)}{format_rarity(listing.model_rarity)}")
    if listing.backdrop:
        lines.append(f"🎨 Фон: {escape(listing.backdrop)}{format_rarity(listing.backdrop_rarity)}")
    if listing.symbol:
        lines.append(f"✨ Символ: {escape(listing.symbol)}{format_rarity(listing.symbol_rarity)}")
    lines.append(f"👤 Владелец: {format_owner(listing)}")
    if filter_name:
        lines.append(f"🔎 Фильтр: {escape(filter_name)}")
    return "\n".join(lines)


def format_filter(f: FilterLike) -> str:
    """Multi-line description of the criteria set in a filter."""
    parts = []
    for field, label in FIELD_LABELS.items():
        value = getattr(f, field)
        if value is not None and value != "":
            if field in ("min_ton", "max_ton"):
                shown = ton_str(value)
            elif field == "max_rarity":  # stored in per-mille, shown in percent like everywhere else
                shown = f"{value / 10:g}%".replace(".", ",")
            else:
                shown = thousands(value) if isinstance(value, int) else escape(str(value))
            parts.append(f"• {label}: <b>{shown}</b>")
    return "\n".join(parts) if parts else "• без ограничений (подойдёт любой подарок)"


def filter_button_title(name: str, f: FilterLike, is_active: bool) -> str:
    bits = [v for v in (f.collection, f.model, f.backdrop, f.symbol) if v]
    summary = ", ".join(bits) or "любой"
    mark = "✅" if is_active else "⏸"
    title = f"{mark} {name}: {summary}"
    return title if len(title) <= 60 else title[:57] + "…"
