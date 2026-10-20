"""Attacker's-eye tests: everything here tries to make the system misbehave."""

import logging
import os
import stat
from datetime import UTC, datetime
from importlib import util
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import text
from yarl import URL

from app.config import Settings
from app.db import repo
from app.domain import Listing
from app.formatting import filter_button_title, format_filter, format_listing
from app.logging_setup import RedactFilter
from app.services.notifier import NotifyResult, TelegramNotifier
from app.sources.telethon_source import parse_resale_gift
from app.web.api import UserRateLimiter
from app.web.server import create_web_app
from tests.test_web import TOKEN, auth, sign

BOT_TOKEN_LIKE = "123456:SECRET-SHOULD-NEVER-LEAK"


@pytest.fixture
async def client(session_factory):
    settings = Settings(bot_token=TOKEN, database_url="sqlite+aiosqlite://", webapp_url="https://example.com",
                        max_filters_per_user=50)
    async with TestClient(TestServer(create_web_app(session_factory, settings))) as c:
        yield c


# ── file access: can the public web server be used to read files? ──────────


@pytest.mark.parametrize(
    "path",
    [
        "/static/%2e%2e/config.py",
        "/static/%2e%2e/%2e%2e/%2e%2e/.env",
        "/static/..%2f..%2fapp/config.py",
        "/static/%252e%252e/config.py",
        "/static/....//config.py",
        "/static/%00../config.py",
        "/static/./../static/../config.py",
        "/static//etc/passwd",
        "/static/\\..\\config.py",
        "/.env",
        "/app/config.py",
        "/data/gifts.db",
    ],
)
async def test_path_traversal_cannot_read_files(client, path):
    response = await client.get(URL(path, encoded=True))
    body = await response.text()
    assert response.status in (400, 403, 404), (path, response.status)
    assert "class Settings" not in body and "BOT_TOKEN" not in body and "root:" not in body


async def test_static_directory_has_no_listing_and_rejects_writes(client):
    assert (await client.get("/static/")).status in (403, 404)
    assert (await client.post("/static/app.js", data="x")).status in (404, 405)
    assert (await client.put("/", data="x")).status in (404, 405)
    assert (await client.get("/static/app.js")).status == 200


# ── authentication: can someone act as another user? ───────────────────────


async def test_cannot_forge_or_reuse_signatures(client):
    mine = sign(1)
    other = sign(2)
    # splice user 2's identity into user 1's signature
    spliced = mine.split("&hash=")[0].replace("u1", "u2") + "&hash=" + mine.split("&hash=")[1]
    for forged in (spliced, mine + "&extra=1", mine.replace("auth_date=", "auth_date=9"), sign(1, token="999:OTHER-BOT")):
        r = await client.get("/api/me", headers={"Authorization": "tma " + forged})
        assert r.status == 401
    # query parameters or other headers never select the user
    r = await client.get("/api/me?user_id=2&id=2", headers={"Authorization": "tma " + mine, "X-User-Id": "2"})
    assert (await r.json())["user"]["id"] == 1
    assert (await client.get("/api/me", headers={"Authorization": "tma " + other})).status == 200


async def test_responses_never_contain_secrets_or_internals(client):
    for path in ("/api/me", "/api/filters", "/api/offers"):
        body = await (await client.get(path, headers=auth(5))).text()
        assert TOKEN not in body and "hash" not in body.lower() and "traceback" not in body.lower()


async def test_internal_errors_do_not_leak_details(client, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("secret internal detail /home/user/.env")

    monkeypatch.setattr(repo, "list_filters", boom)
    r = await client.get("/api/filters", headers=auth(5))
    body = await r.text()
    assert r.status == 500 and "secret internal detail" not in body and ".env" not in body


# ── injection ──────────────────────────────────────────────────────────────

PAYLOADS = [
    "'; DROP TABLE user_filters; --",
    "\" OR 1=1 --",
    "Robert'); DELETE FROM users;--",
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "{{7*7}} ${7*7} %s %(x)s",
    "a" * 64,
]


@pytest.mark.parametrize("payload", PAYLOADS)
async def test_sql_and_html_payloads_are_stored_inertly(client, session_factory, payload):
    r = await client.post("/api/filters", json={"collection": payload[:64], "name": payload[:64]}, headers=auth(7))
    assert r.status == 201
    assert r.headers["Content-Type"].startswith("application/json") and r.headers["X-Content-Type-Options"] == "nosniff"
    async with session_factory() as s:
        assert (await s.execute(text("SELECT count(*) FROM user_filters"))).scalar_one() >= 1
        assert (await s.execute(text("SELECT count(*) FROM users"))).scalar_one() >= 1
    # the bot renders these as HTML: they must come out escaped
    f = SimpleNamespace(collection=payload, model=None, backdrop=None, symbol=None, min_price=None, max_price=None,
                        min_ton=None, max_ton=None, max_rarity=None)
    rendered = format_filter(f)
    assert "<script" not in rendered and "<img" not in rendered
    # button captions are plain text in Telegram (HTML is never parsed there), so they need no escaping
    assert filter_button_title(payload, f, True).startswith("✅")


def hostile_listing(**kw) -> Listing:
    base = dict(slug="Pepe-1", collection_id=1, collection="<b>x</b>", number=1, price=5, model="<i>m</i>",
                backdrop="\"><script>", symbol="<u>", owner_name="<a href=javascript:1>", owner_username=None)
    base.update(kw)
    return Listing(**base)


def test_listing_card_escapes_third_party_text():
    card = format_listing(hostile_listing(), filter_name="<script>f</script>")
    for raw in ("<b>x</b>", "<i>m</i>", "<script>", "<u>", "<a href=javascript"):
        assert raw not in card


@pytest.mark.parametrize("name", ['x"><script>', "a b", "ab", "9abcde", "abcd", "x" * 40, "a-b_c1", "abc\nde", "ünïcode1"])
def test_owner_links_are_built_only_from_valid_usernames(name):
    assert hostile_listing(owner_username=name).owner_url is None


def test_valid_username_builds_a_clean_link():
    assert hostile_listing(owner_username="valid_name9").owner_url == "https://t.me/valid_name9"


class _Recorder(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):  # pragma: no cover
        pass

    async def stream_content(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        return Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=1, type="private"), text="x")


async def test_notification_sent_to_users_is_escaped_end_to_end():
    session = _Recorder()
    notifier = TelegramNotifier(Bot("123456:TEST", session=session), rate_per_second=1000)
    assert await notifier.notify(1, hostile_listing(), "<script>f</script>") is NotifyResult.SENT
    sent = [c for c in session.calls if isinstance(c, SendMessage)][0]
    for raw in ("<b>x</b>", "<script>", "<i>m</i>", "<u>"):
        assert raw not in sent.text
    urls = [b.url for row in sent.reply_markup.inline_keyboard for b in row]
    assert urls == ["https://t.me/nft/Pepe-1"]  # buttons carry only links we built ourselves


def test_unsafe_slugs_from_third_parties_are_rejected():
    from telethon.tl import types

    gift = types.StarGiftUnique(
        id=1, gift_id=5, title="Plush Pepe", slug="../evil\"><x", num=1, attributes=[], availability_issued=1,
        availability_total=1, resell_amount=[types.StarsAmount(amount=10, nanos=0)],
    )
    assert parse_resale_gift(gift, {}, {}, SimpleNamespace(id=5, title="x")) is None


# ── abuse / denial of service ──────────────────────────────────────────────


async def test_request_size_and_method_limits(client):
    big = await client.post("/api/filters", data="x" * 20_000, headers=auth(8))
    assert big.status == 413
    assert (await client.delete("/api/filters", headers=auth(8))).status == 405
    assert (await client.get("/api/does-not-exist", headers=auth(8))).status == 404


async def test_api_is_rate_limited_per_user(client):
    statuses = [(await client.get("/api/filters", headers=auth(10))).status for _ in range(45)]
    assert 200 in statuses and statuses.count(429) >= 10 and statuses[0] == 200
    assert (await client.get("/api/filters", headers=auth(11))).status == 200  # other users unaffected


def test_rate_limiter_refills_over_time():
    limiter = UserRateLimiter(burst=3, per_second=1.0)
    assert [limiter.allow(1, now=0.0) for _ in range(4)] == [True, True, True, False]
    assert limiter.allow(1, now=2.0) and limiter.allow(1, now=2.0) and not limiter.allow(1, now=2.0)


async def test_headers_are_hardened_even_on_errors(client):
    for response in (await client.get("/nope"), await client.get("/api/me"), await client.get("/")):
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        banner = response.headers.get("Server", "")
        assert "aiohttp" not in banner and "Python" not in banner


# ── secrets handling ───────────────────────────────────────────────────────


def test_logging_redacts_secrets(caplog):
    logger = logging.getLogger("leaktest")
    handler = logging.StreamHandler()
    handler.addFilter(RedactFilter([BOT_TOKEN_LIKE]))
    records = []
    handler.emit = records.append  # type: ignore[method-assign]
    logger.addHandler(handler)
    logger.warning("calling https://api.telegram.org/bot%s/getMe", BOT_TOKEN_LIKE)
    assert BOT_TOKEN_LIKE not in records[0].getMessage() and "***" in records[0].getMessage()


def test_settings_do_not_print_secrets():
    s = Settings(_env_file=None, bot_token=BOT_TOKEN_LIKE, database_url="x", gift_source="telegram",
                 tg_api_id=1, tg_api_hash="HASH-SECRET", tg_session_string="SESSION-SECRET")
    shown = repr(s) + str(s) + str(s.model_dump())
    for secret in ("SECRET-SHOULD", "HASH-SECRET", "SESSION-SECRET"):
        assert secret not in shown


def test_session_script_writes_env_privately_without_printing(tmp_path, capsys):
    spec = util.spec_from_file_location("make_session", Path(__file__).parent.parent / "scripts" / "make_session.py")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)

    env = tmp_path / ".env"
    env.write_text("BOT_TOKEN=keep-me\nTG_SESSION_STRING=old\n")
    os.chmod(env, 0o644)
    module.write_env_value(env, "TG_SESSION_STRING", "NEW-SESSION-VALUE")
    lines = env.read_text().splitlines()
    assert "BOT_TOKEN=keep-me" in lines and "TG_SESSION_STRING=NEW-SESSION-VALUE" in lines
    assert sum(line.startswith("TG_SESSION_STRING") for line in lines) == 1
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert "NEW-SESSION-VALUE" not in capsys.readouterr().out
