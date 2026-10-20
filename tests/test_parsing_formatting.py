import pytest

from app.bot.parsing import InvalidValue, parse_field_value
from app.formatting import format_listing, format_price


@pytest.mark.parametrize("raw", ["-", "—", " сброс ", ""])
def test_clear_tokens(raw):
    assert parse_field_value("model", raw) is None
    assert parse_field_value("min_price", raw) is None


def test_text_and_numbers():
    assert parse_field_value("collection", "  Plush   Pepe ") == "Plush Pepe"
    assert parse_field_value("min_price", "1 500") == 1500
    assert parse_field_value("max_rarity", "20") == 20


@pytest.mark.parametrize(
    ("field", "raw"),
    [("min_price", "abc"), ("min_price", "-5"), ("max_rarity", "0"), ("max_rarity", "1001"), ("model", "x" * 65)],
)
def test_invalid_values(field, raw):
    with pytest.raises(InvalidValue):
        parse_field_value(field, raw)


def test_price_formatting():
    assert format_price(1500, "XTR") == "⭐ 1 500"
    assert format_price(12_500_000_000, "TON") == "💎 12.5 TON"


def test_listing_card_escapes_html_and_shows_owner(make_listing):
    card = format_listing(make_listing(collection="<b>x</b>", owner_username="seller"))
    assert "<b>x</b>" not in card.replace("<b>&lt;b&gt;x&lt;/b&gt;</b>", "")
    assert 'href="https://t.me/seller"' in card
    assert "https://t.me/nft/PlushPepe-1" in card
    assert "(1%)" in card  # model rarity 10 permille


def test_hidden_owner_is_not_invented(make_listing):
    card = format_listing(make_listing(owner_username=None, owner_name=None, owner_id=None))
    assert "скрыт" in card and "t.me/seller" not in card
