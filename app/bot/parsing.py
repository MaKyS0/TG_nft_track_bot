"""Validation of values typed by users when editing filters."""

from decimal import Decimal, InvalidOperation

from app.domain import NANO_TON

TEXT_FIELDS = {"collection", "model", "backdrop", "symbol"}
PRICE_FIELDS = {"min_price", "max_price"}
TON_FIELDS = {"min_ton", "max_ton"}
MAX_TON = 1_000_000_000
MAX_TEXT_LENGTH = 64
MAX_PRICE = 1_000_000_000
CLEAR_TOKENS = {"-", "—", "–", "сброс", "нет", "any", "любой", "любая", "любое"}


FILTER_FIELDS = ("collection", "model", "backdrop", "symbol", "min_price", "max_price", "min_ton", "max_ton", "max_rarity")


class InvalidValue(ValueError):
    """Raised with a user-facing (Russian) message."""


def validate_filter_payload(payload: dict, current=None) -> dict:
    """Validate a structured edit (mini app API) and return the field changes to apply.

    ``current`` is the existing filter (for the min/max price cross-check). Nothing is applied here,
    so a rejected payload leaves the filter untouched.
    """
    changes: dict = {}
    for key, raw in payload.items():
        if key in FILTER_FIELDS:
            if raw is None:
                changes[key] = None
            elif isinstance(raw, bool) or not isinstance(raw, (str, int) + ((float,) if key in TON_FIELDS else ())):
                raise InvalidValue(f"Некорректное значение поля «{key}».")
            else:
                changes[key] = parse_field_value(key, str(raw))
        elif key == "name":
            name = " ".join(str(payload["name"]).split())
            if not name or len(name) > MAX_TEXT_LENGTH:
                raise InvalidValue(f"Название: от 1 до {MAX_TEXT_LENGTH} символов.")
            changes["name"] = name
        elif key == "is_active":
            if not isinstance(raw, bool):
                raise InvalidValue("Поле is_active должно быть true или false.")
            changes["is_active"] = raw
        else:
            raise InvalidValue(f"Неизвестное поле: {key}.")

    for low_key, high_key in (("min_price", "max_price"), ("min_ton", "max_ton")):
        low = changes.get(low_key, getattr(current, low_key, None))
        high = changes.get(high_key, getattr(current, high_key, None))
        if low is not None and high is not None and low > high:
            raise InvalidValue("Минимальная цена не может быть больше максимальной.")
    return changes


def parse_field_value(field: str, raw: str) -> str | int | None:
    """Return the cleaned value, or ``None`` to clear the field."""
    text = " ".join(raw.split())
    if not text or text.casefold() in CLEAR_TOKENS:
        return None

    if field in TEXT_FIELDS:
        if len(text) > MAX_TEXT_LENGTH:
            raise InvalidValue(f"Слишком длинное значение (максимум {MAX_TEXT_LENGTH} символов).")
        return text

    if field in TON_FIELDS:
        cleaned = text.replace(" ", "").replace("_", "").replace(",", ".")
        try:
            amount = Decimal(cleaned)
        except InvalidOperation:
            raise InvalidValue("Нужно число в TON, например 12.5.") from None
        if not amount.is_finite() or amount < 0 or amount > MAX_TON:
            raise InvalidValue("Нужно число в TON от 0 до 1 000 000 000.")
        if amount.as_tuple().exponent < -4:
            raise InvalidValue("В TON допустимо не больше 4 знаков после запятой.")
        return int(amount * NANO_TON)

    if field in PRICE_FIELDS or field == "max_rarity":
        digits = text.replace(" ", "").replace("_", "").replace(",", "")
        if not digits.isdigit():
            raise InvalidValue("Нужно целое неотрицательное число, например 1500.")
        number = int(digits)
        if field == "max_rarity":
            if not 1 <= number <= 1000:
                raise InvalidValue("Редкость задаётся в промилле от 1 до 1000 (5‰ = 0,5%).")
        elif number > MAX_PRICE:
            raise InvalidValue("Слишком большое число.")
        return number

    raise InvalidValue("Неизвестное поле.")
