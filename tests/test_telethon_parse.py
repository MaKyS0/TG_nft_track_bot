from telethon.tl import types

from app.domain import Collection
from app.sources.telethon_source import parse_resale_gift

COLLECTION = Collection(id=5, title="Fallback")


def unique_gift(**overrides):
    values = dict(
        id=1,
        gift_id=5,
        title="Plush Pepe",
        slug="PlushPepe-42",
        num=42,
        attributes=[
            types.StarGiftAttributeModel(name="Classic", document=None, rarity=types.StarGiftAttributeRarity(permille=15)),
            types.StarGiftAttributePattern(name="Stars", document=None, rarity=types.StarGiftAttributeRarity(permille=25)),
            types.StarGiftAttributeBackdrop(
                name="Black",
                backdrop_id=1,
                center_color=0,
                edge_color=0,
                pattern_color=0,
                text_color=0,
                rarity=types.StarGiftAttributeRarityRare(),
            ),
        ],
        availability_issued=10,
        availability_total=100,
        owner_id=types.PeerUser(user_id=77),
        resell_amount=[types.StarsAmount(amount=1500, nanos=0)],
    )
    values.update(overrides)
    return types.StarGiftUnique(**values)


def user(**kw):
    return types.User(id=77, username=kw.get("username"), first_name="Ann", last_name="Lee")


def test_parses_attributes_price_and_public_owner():
    listing = parse_resale_gift(unique_gift(), {77: user(username="annlee")}, {}, COLLECTION)
    assert listing is not None
    assert (listing.slug, listing.collection, listing.number) == ("PlushPepe-42", "Plush Pepe", 42)
    assert (listing.model, listing.model_rarity) == ("Classic", 15)
    assert (listing.symbol, listing.symbol_rarity) == ("Stars", 25)
    assert (listing.backdrop, listing.backdrop_rarity) == ("Black", None)  # tier-only rarity has no number
    assert (listing.price, listing.currency) == (1500, "XTR")
    assert listing.owner_url == "https://t.me/annlee"
    assert listing.url == "https://t.me/nft/PlushPepe-42"


def test_owner_without_username_has_no_link():
    listing = parse_resale_gift(unique_gift(), {77: user()}, {}, COLLECTION)
    assert listing.owner_url is None
    assert listing.owner_name == "Ann Lee"


def test_unknown_owner_is_left_empty():
    listing = parse_resale_gift(unique_gift(owner_id=None), {}, {}, COLLECTION)
    assert listing.owner_id is None and listing.owner_username is None and listing.owner_name is None


def test_ton_only_offer():
    gift = unique_gift(resell_amount=[types.StarsTonAmount(amount=2_000_000_000)])
    listing = parse_resale_gift(gift, {}, {}, COLLECTION)
    assert (listing.price, listing.currency) == (2_000_000_000, "TON")


def test_stars_preferred_when_both_prices_exist():
    gift = unique_gift(resell_amount=[types.StarsTonAmount(amount=1), types.StarsAmount(amount=99, nanos=0)])
    assert parse_resale_gift(gift, {}, {}, COLLECTION).currency == "XTR"


def test_not_for_sale_or_not_unique_is_skipped():
    assert parse_resale_gift(unique_gift(resell_amount=None), {}, {}, COLLECTION) is None
    plain = types.StarGift(id=1, sticker=None, stars=1, convert_stars=1)
    assert parse_resale_gift(plain, {}, {}, COLLECTION) is None
