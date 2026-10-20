import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.config import Settings
from app.db import repo
from app.web.auth import InitDataError, validate_init_data
from app.web.server import create_web_app

TOKEN = "123456:TEST-TOKEN"


def sign(user_id=100, *, token=TOKEN, auth_date=None, first_name="Ann", extra=None) -> str:
    """Build initData exactly as Telegram does (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app)."""
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": "AAH",
        "user": json.dumps({"id": user_id, "first_name": first_name, "username": f"u{user_id}"}),
        **(extra or {}),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


# ── initData validation ────────────────────────────────────────────────────


def test_valid_init_data():
    user = validate_init_data(sign(42), TOKEN)
    assert (user.id, user.first_name, user.username) == (42, "Ann", "u42")


def test_signature_covers_every_field():
    # swap the user id after signing: must be rejected
    forged = sign(1).replace("%22id%22%3A+1", "%22id%22%3A+2")
    assert forged != sign(1)
    with pytest.raises(InitDataError, match="signature"):
        validate_init_data(forged, TOKEN)


def test_wrong_token_is_rejected():
    with pytest.raises(InitDataError, match="signature"):
        validate_init_data(sign(token="999:OTHER"), TOKEN)


@pytest.mark.parametrize("data", ["", "garbage", "auth_date=1&user=%7B%7D"])
def test_malformed_init_data(data):
    with pytest.raises(InitDataError):
        validate_init_data(data, TOKEN)


def test_expired_and_future_init_data():
    now = 1_000_000.0
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(sign(auth_date=int(now) - 90_000), TOKEN, max_age=86_400, now=now)
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(sign(auth_date=int(now) + 3_600), TOKEN, now=now)
    assert validate_init_data(sign(auth_date=int(now) - 100), TOKEN, now=now).id == 100


# ── API ────────────────────────────────────────────────────────────────────


@pytest.fixture
async def client(session_factory):
    settings = Settings(
        bot_token=TOKEN,
        database_url="sqlite+aiosqlite://",
        webapp_url="https://example.com",
        max_filters_per_user=3,
        gift_source="mock",
    )
    async with TestClient(TestServer(create_web_app(session_factory, settings))) as c:
        yield c


def auth(user_id=100):
    return {"Authorization": "tma " + sign(user_id)}


async def test_api_requires_valid_init_data(client):
    assert (await client.get("/api/me")).status == 401
    assert (await client.get("/api/me", headers={"Authorization": "tma garbage"})).status == 401
    assert (await client.get("/api/me", headers={"Authorization": "Bearer " + sign()})).status == 401
    stale = {"Authorization": "tma " + sign(auth_date=1)}
    assert (await client.get("/api/me", headers=stale)).status == 401


async def test_me_registers_user_and_reports_stats(client, session_factory):
    r = await client.get("/api/me", headers=auth(7))
    data = await r.json()
    assert r.status == 200
    assert data["user"] == {"id": 7, "first_name": "Ann", "username": "u7"}
    assert data["notifications_enabled"] is True and data["source"] == "mock"
    async with session_factory() as s:
        assert (await s.get(repo.User, 7)) is not None


async def test_filter_crud(client):
    r = await client.post(
        "/api/filters",
        json={"name": "Pepe cheap", "collection": "Plush Pepe", "max_price": "5 000", "max_rarity": 20},
        headers=auth(),
    )
    created = (await r.json())["filter"]
    assert r.status == 201
    assert (created["collection"], created["max_price"], created["max_rarity"], created["is_active"]) == (
        "Plush Pepe", 5000, 20, True,
    )

    r = await client.patch(f"/api/filters/{created['id']}", json={"is_active": False, "model": "Classic"}, headers=auth())
    assert (await r.json())["filter"]["is_active"] is False

    listed = (await (await client.get("/api/filters", headers=auth())).json())["filters"]
    assert [f["id"] for f in listed] == [created["id"]] and listed[0]["model"] == "Classic"

    # null clears a field
    r = await client.patch(f"/api/filters/{created['id']}", json={"model": None}, headers=auth())
    assert (await r.json())["filter"]["model"] is None

    assert (await client.delete(f"/api/filters/{created['id']}", headers=auth())).status == 200
    assert (await (await client.get("/api/filters", headers=auth())).json())["filters"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"min_price": "abc"},
        {"min_price": 10, "max_price": 5},
        {"max_rarity": 5000},
        {"is_active": "yes"},
        {"name": "  "},
        {"collection": ["x"]},
        {"evil": 1},
    ],
)
async def test_invalid_filter_payloads_are_rejected_and_create_nothing(client, payload):
    r = await client.post("/api/filters", json=payload, headers=auth())
    assert r.status == 400 and (await r.json())["error"]
    assert (await (await client.get("/api/filters", headers=auth())).json())["filters"] == []


async def test_min_max_checked_against_existing_values(client):
    f = (await (await client.post("/api/filters", json={"max_price": 100}, headers=auth())).json())["filter"]
    r = await client.patch(f"/api/filters/{f['id']}", json={"min_price": 500}, headers=auth())
    assert r.status == 400


async def test_malformed_body_is_a_client_error(client):
    r = await client.post("/api/filters", data="not json", headers=auth())
    assert r.status == 400
    r = await client.post("/api/filters", data="[1]", headers=auth())
    assert r.status == 400


async def test_users_cannot_touch_each_others_filters(client):
    f = (await (await client.post("/api/filters", json={}, headers=auth(1))).json())["filter"]
    assert (await client.patch(f"/api/filters/{f['id']}", json={"is_active": False}, headers=auth(2))).status == 404
    assert (await client.delete(f"/api/filters/{f['id']}", headers=auth(2))).status == 404
    assert (await client.patch("/api/filters/abc", json={}, headers=auth(2))).status == 404
    assert (await (await client.get("/api/filters", headers=auth(2))).json())["filters"] == []
    assert (await (await client.get("/api/filters", headers=auth(1))).json())["filters"][0]["is_active"] is True


async def test_filter_limit(client):
    for _ in range(3):
        assert (await client.post("/api/filters", json={}, headers=auth())).status == 201
    r = await client.post("/api/filters", json={}, headers=auth())
    assert r.status == 409


async def test_notifications_toggle(client):
    r = await client.post("/api/notifications", json={"enabled": False}, headers=auth())
    assert (await r.json()) == {"notifications_enabled": False}
    assert (await (await client.get("/api/me", headers=auth())).json())["notifications_enabled"] is False
    assert (await client.post("/api/notifications", json={"enabled": "no"}, headers=auth())).status == 400


async def test_offers_follow_filters_cheapest_first(client, session_factory, make_listing):
    r = await client.get("/api/offers", headers=auth())
    assert (await r.json()) == {"has_filters": False, "total": 0, "offers": []}

    await client.post("/api/filters", json={"collection": "Plush Pepe"}, headers=auth())
    async with session_factory() as s:
        await repo.save_listings(
            s,
            [
                make_listing(slug="PlushPepe-1", price=900, backdrop_center=0xE3BE62, backdrop_edge=0x9A7421),
                make_listing(slug="PlushPepe-2", number=2, price=300),
                make_listing(slug="Heart-3", collection="Heart Locket", price=1),
            ],
        )
        await s.commit()

    data = await (await client.get("/api/offers", headers=auth())).json()
    assert data["has_filters"] and data["total"] == 2
    assert [o["slug"] for o in data["offers"]] == ["PlushPepe-2", "PlushPepe-1"]
    pricey = data["offers"][1]
    assert pricey["backdrop_center"] == "#e3be62" and pricey["url"] == "https://t.me/nft/PlushPepe-1"
    assert pricey["owner_url"] == "https://t.me/seller"


async def test_static_page_and_security_headers(client):
    page = await client.get("/")
    body = await page.text()
    assert page.status == 200 and "NFT Tracker" in body and "/static/app.js" in body
    assert "telegram.org" in page.headers["Content-Security-Policy"]
    assert page.headers["X-Content-Type-Options"] == "nosniff"
    assert (await client.get("/static/app.js")).status == 200
    assert (await client.get("/healthz")).status == 200
    api = await client.get("/api/me", headers=auth())
    assert api.headers["Cache-Control"] == "no-store"


async def test_first_open_with_parallel_requests_does_not_race(client):
    """The Mini App fires /api/me, /api/filters and /api/offers at once for a brand-new user."""
    import asyncio

    responses = await asyncio.gather(*(client.get(path, headers=auth(555)) for path in ("/api/me", "/api/filters", "/api/offers")))
    assert [r.status for r in responses] == [200, 200, 200]
