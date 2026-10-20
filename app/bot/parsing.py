"""Validation of values typed by users when editing filters."""

TEXT_FIELDS = {"collection", "model", "backdrop", "symbol"}
PRICE_FIELDS = {"min_price", "max_price"}
MAX_TEXT_LENGTH = 64
MAX_PRICE = 1_000_000_000
CLEAR_TOKENS = {"-", "—", "–", "сброс", "нет", "any", "любой", "любая", "любое"}


class InvalidValue(ValueError):
    """Raised with a user-facing (Russian) message."""


def parse_field_value(field: str, raw: str) -> str | int | None:
    """Return the cleaned value, or ``None`` to clear the field."""
    text = " ".join(raw.split())
    if not text or text.casefold() in CLEAR_TOKENS:
        return None

    if field in TEXT_FIELDS:
        if len(text) > MAX_TEXT_LENGTH:
            raise InvalidValue(f"Слишком длинное значение (максимум {MAX_TEXT_LENGTH} символов).")
        return text

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
