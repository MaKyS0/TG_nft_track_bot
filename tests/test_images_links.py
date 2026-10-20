from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetMe, SendMessage, SendPhoto
from aiogram.types import Chat, Message

from app.config import Settings, is_valid_link_template
from app.domain import Collection
from app.services.notifier import NotifyResult, TelegramNotifier
from app.sources.mrkt import DEFAULT_LINK, parse_gift, render_link
from tests.test_mrkt import gift

PEPE = Collection(id=1, title="Plush Pepe")


# ── pictures ───────────────────────────────────────────────────────────────


def test_image_url_is_built_from_the_slug_only(make_listing):
    assert make_listing(slug="PlushPepe-1").image_url == "https://nft.fragment.com/gift/plushpepe-1.medium.jpg"
    assert make_listing(slug="Durov-9").image_url.endswith("/durov-9.medium.jpg")
    for bad in ("../x", 'a"b', "a b", "", "x" * 90):
        assert make_listing(slug=bad).image_url is None


# ── MRKT link template ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "template",
    [
        "https://t.me/mrkt/app",
        "https://t.me/mrkt",
        "https://t.me/mrkt/app?startapp={id}",
        "https://t.me/mrkt/app?startapp=gift_{id}_{number}",
        "https://t.me/mrkt/app?startapp={slug}",
    ],
)
def test_valid_link_templates(template):
    assert is_valid_link_template(template)


@pytest.mark.parametrize(
    "template",
    [
        "",
        "http://t.me/mrkt/app",
        "https://evil.example/mrkt/app",
        "https://t.me/other_bot/app",
        "https://t.me/mrktevil/app",
        "https://t.me/mrkt/app?startapp={id.__class__}",
        "https://t.me/mrkt/app?startapp={0}",
        "https://t.me/mrkt/app?x={id",
        "https://t.me/mrkt/app?x=<script>",
        "javascript:alert(1)",
        "https://t.me/mrkt/app\nHost: evil",
    ],
)
def test_dangerous_link_templates_are_rejected(template):
    assert not is_valid_link_template(template)
    with pytest.raises(ValueError, match="MRKT_LINK_TEMPLATE"):
        Settings(_env_file=None, bot_token="1:x", database_url="x", mrkt_link_template=template)


def test_default_template_opens_the_app():
    s = Settings(_env_file=None, bot_token="1:x", database_url="x")
    assert s.mrkt_link_template == DEFAULT_LINK == "https://t.me/mrkt/app"


def test_templates_are_filled_without_str_format():
    link = render_link("https://t.me/mrkt/app?startapp={id}-{number}", offer_id="abc-123", slug="PlushPepe-7", number=7)
    assert link == "https://t.me/mrkt/app?startapp=abc-123-7"
    # an offer without a usable id falls back to the plain app link instead of producing "startapp="
    assert render_link("https://t.me/mrkt/app?startapp={id}", offer_id=None, slug="S-1", number=1) == DEFAULT_LINK


def test_offer_ids_from_the_market_are_charset_checked():
    template = "https://t.me/mrkt/app?startapp={id}"
    good = parse_gift(gift(id="a1b2-C3_d4"), PEPE, template)
    assert good.external_id == "a1b2-C3_d4" and good.market_url == "https://t.me/mrkt/app?startapp=a1b2-C3_d4"
    for hostile in ("x&y=1", "../..", "a b", "a\r\nb", "<s>", "x" * 100, 123, None):
        bad = parse_gift(gift(id=hostile), PEPE, template)
        assert bad.external_id is None and bad.market_url == DEFAULT_LINK


def test_a_tampered_stored_link_is_ignored(make_listing):
    forged = make_listing(market="mrkt", buy_url="https://evil.example/phish")
    assert forged.market_url == DEFAULT_LINK  # only t.me links are ever used


# ── notifications with a picture ───────────────────────────────────────────


class Recorder(BaseSession):
    def __init__(self, photo_error: Exception | None = None):
        super().__init__()
        self.calls = []
        self.photo_error = photo_error

    async def close(self):  # pragma: no cover
        pass

    async def stream_content(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendPhoto) and self.photo_error:
            raise self.photo_error
        return Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=1, type="private"), text="x")


def notifier(session):
    return TelegramNotifier(Bot("123456:TEST", session=session), rate_per_second=1000)


async def test_notification_is_a_photo_with_caption_and_market_button_first(make_listing):
    session = Recorder()
    offer = make_listing(slug="PlushPepe-7", market="mrkt", currency="TON", price=5_000_000_000,
                         buy_url="https://t.me/mrkt/app?startapp=abc")
    assert await notifier(session).notify(1, offer, "f") is NotifyResult.SENT
    sent = session.calls[0]
    assert isinstance(sent, SendPhoto) and sent.photo == "https://nft.fragment.com/gift/plushpepe-7.medium.jpg"
    assert "5 TON" in sent.caption and len(sent.caption) <= 1024
    buttons = [(b.text, b.url) for row in sent.reply_markup.inline_keyboard for b in row]
    assert buttons[0] == ("🛒 Купить на MRKT", "https://t.me/mrkt/app?startapp=abc")
    assert ("🔗 Открыть подарок", "https://t.me/nft/PlushPepe-7") in buttons


async def test_falls_back_to_text_when_telegram_cannot_fetch_the_picture(make_listing):
    session = Recorder(photo_error=TelegramBadRequest(method=GetMe(), message="Bad Request: failed to get HTTP URL content"))
    assert await notifier(session).notify(1, make_listing(), "f") is NotifyResult.SENT
    assert [type(c).__name__ for c in session.calls] == ["SendPhoto", "SendMessage"]


async def test_overlong_text_goes_without_picture(make_listing):
    session = Recorder()
    await notifier(session).notify(1, make_listing(model="m" * 64, backdrop="b" * 64, symbol="s" * 64), "f" * 64)
    first = session.calls[0]
    assert isinstance(first, (SendPhoto, SendMessage))
    if isinstance(first, SendPhoto):
        assert len(first.caption) <= 1024


async def test_blocked_user_is_still_detected_with_photos(make_listing):
    from aiogram.exceptions import TelegramForbiddenError

    session = Recorder(photo_error=TelegramForbiddenError(method=GetMe(), message="Forbidden: bot was blocked by the user"))
    assert await notifier(session).notify(1, make_listing(), "f") is NotifyResult.BLOCKED
    assert [type(c).__name__ for c in session.calls] == ["SendPhoto"]  # no pointless second attempt


# ── web ────────────────────────────────────────────────────────────────────


async def test_csp_allows_only_the_gift_cdn_for_images():
    from app.web.server import CSP

    assert "img-src 'self' data: https://nft.fragment.com;" in CSP
    assert "script-src 'self' https://telegram.org;" in CSP and "connect-src 'self'" in CSP


def test_id32_placeholder_drops_dashes_and_template_is_accepted():
    from app.config import is_valid_link_template

    template = "https://t.me/mrkt/app?startapp={id32}"
    assert is_valid_link_template(template)
    link = render_link(template, offer_id="aad4f918-b602-42f2-9f63-7dd406120925", slug="S-1", number=1)
    assert link == "https://t.me/mrkt/app?startapp=aad4f918b60242f29f637dd406120925"
    assert render_link(template, offer_id=None, slug="S-1", number=1) == DEFAULT_LINK
