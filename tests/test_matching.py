from types import SimpleNamespace

import pytest

from app.matching import matches, normalize


def flt(**kw):
    base = dict(collection=None, model=None, backdrop=None, symbol=None, min_price=None, max_price=None, min_ton=None, max_ton=None, max_rarity=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_empty_filter_matches_everything(make_listing):
    assert matches(make_listing(), flt())


@pytest.mark.parametrize(
    "criteria",
    [
        dict(collection="plush pepe"),
        dict(collection="  Plush   PEPE "),
        dict(model="classic", backdrop="BLACK", symbol="stars"),
        dict(min_price=1000, max_price=1000),
        dict(max_rarity=10),
    ],
)
def test_matching_criteria(make_listing, criteria):
    assert matches(make_listing(), flt(**criteria))


@pytest.mark.parametrize(
    "criteria",
    [
        dict(collection="Heart Locket"),
        dict(model="Neon"),
        dict(backdrop="Red"),
        dict(symbol="Hearts"),
        dict(min_price=1001),
        dict(max_price=999),
        dict(max_rarity=9),
    ],
)
def test_non_matching_criteria(make_listing, criteria):
    assert not matches(make_listing(), flt(**criteria))


def test_all_criteria_must_hold(make_listing):
    assert not matches(make_listing(), flt(collection="Plush Pepe", model="Neon"))


def test_apostrophe_variants_are_equal(make_listing):
    listing = make_listing(collection="Durov's Cap")
    assert matches(listing, flt(collection="Durov’s Cap"))
    assert normalize("A  b") == "a b"


def test_ton_offers_never_match_star_price_bounds(make_listing):
    ton = make_listing(currency="TON", price=5_000_000_000)
    assert not matches(ton, flt(max_price=10**9))
    assert not matches(ton, flt(min_price=1))
    assert matches(ton, flt(collection="Plush Pepe"))


def test_rarity_filter_requires_known_rarity(make_listing):
    unknown = make_listing(model_rarity=None, backdrop_rarity=None, symbol_rarity=None)
    assert not matches(unknown, flt(max_rarity=1000))


def test_missing_attribute_fails_attribute_filter(make_listing):
    assert not matches(make_listing(model=None), flt(model="Classic"))
