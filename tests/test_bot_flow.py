"""End-to-end through the real Dispatcher with a recording fake Bot API session."""

import importlib
from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import Chat, Message

from app.bot import keyboards as kb
from app.bot.factory import build_dispatcher
from app.bot.handlers import filters, notifications, search, start, stats
from app.bot.handlers import help as help_
from app.config import Settings
from app.db import repo

USER = {"id": 100, "is_bot": False, "first_name": "Tester", "username": "tester"}
CHAT = {"id": 100, "type": "private"}


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=100, type="private"), text="ok")
        return True

    @property
    def texts(self):
        return [m.text for m in self.calls if isinstance(m, (SendMessage, EditMessageText))]


def fresh_dispatcher(storage, session_factory, settings):
    """A Router can be attached to one Dispatcher only; re-import handler modules to get fresh routers."""
    for module in (start, help_, stats, notifications, search, filters):
        importlib.reload(module)
    return build_dispatcher(storage, session_factory, settings, throttle_interval=0)


class Harness:
    def __init__(self, session_factory):
        self.session = RecordingSession()
        self.bot = Bot("123456:TEST", session=self.session)
        settings = Settings(bot_token="123456:TEST", database_url="sqlite+aiosqlite://", search_fresh_minutes=30)
        self.dp = fresh_dispatcher(MemoryStorage(), session_factory, settings)
        self.n = 0

    async def message(self, text):
        self.n += 1
        entities = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else []
        await self.dp.feed_raw_update(
            self.bot,
            {
                "update_id": self.n,
                "message": {
                    "message_id": self.n,
                    "date": 1700000000,
                    "chat": CHAT,
                    "from": USER,
                    "text": text,
                    "entities": entities,
                },
            },
        )

    async def press(self, callback_data):
        self.n += 1
        await self.dp.feed_raw_update(
            self.bot,
            {
                "update_id": self.n,
                "callback_query": {
                    "id": str(self.n),
                    "from": USER,
                    "chat_instance": "x",
                    "data": callback_data.pack(),
                    "message": {"message_id": 1, "date": 1700000000, "chat": CHAT, "text": "menu"},
                },
            },
        )


@pytest.fixture
def h(session_factory):
    return Harness(session_factory)


async def test_start_greets_and_registers_user(h, session_factory):
    await h.message("/start")
    assert "Gift Track Bot" in h.session.texts[-1]
    send = h.session.calls[-1]
    assert [b.text for row in send.reply_markup.keyboard for b in row] == [
        kb.BTN_SEARCH, kb.BTN_NOTIFICATIONS, kb.BTN_FILTERS, kb.BTN_STATS, kb.BTN_HELP,
    ]
    async with session_factory() as s:
        user = await s.get(repo.User, 100)
        assert user.username == "tester" and user.notifications_enabled


async def test_create_and_edit_filter_flow(h, session_factory):
    await h.message("/start")
    await h.message(kb.BTN_FILTERS)
    await h.press(kb.FilterCb(action="new"))
    assert "Фильтр 1" in h.session.texts[-1]

    await h.press(kb.FilterCb(action="edit", id=1, field="collection"))
    await h.message("Plush Pepe")
    await h.press(kb.FilterCb(action="edit", id=1, field="max_price"))
    await h.message("abc")  # invalid: stays in the same state
    assert "целое" in h.session.texts[-1]
    await h.message("5 000")

    async with session_factory() as s:
        f = await repo.get_filter(s, 1, 100)
        assert (f.collection, f.max_price, f.is_active) == ("Plush Pepe", 5000, True)

    await h.press(kb.FilterCb(action="toggle", id=1))
    async with session_factory() as s:
        assert (await repo.get_filter(s, 1, 100)).is_active is False


async def test_min_price_cannot_exceed_max_price(h, session_factory):
    await h.message("/start")
    await h.press(kb.FilterCb(action="new"))
    for field, value in (("max_price", "100"), ("min_price", "500")):
        await h.press(kb.FilterCb(action="edit", id=1, field=field))
        await h.message(value)
    async with session_factory() as s:
        f = await repo.get_filter(s, 1, 100)
        assert (f.min_price, f.max_price) == (None, 100)


async def test_menu_button_during_edit_cancels_it_instead_of_becoming_a_value(h, session_factory):
    await h.message("/start")
    await h.press(kb.FilterCb(action="new"))
    await h.press(kb.FilterCb(action="edit", id=1, field="model"))
    await h.message(kb.BTN_HELP)
    assert "Помощь" in h.session.texts[-1]
    async with session_factory() as s:
        assert (await repo.get_filter(s, 1, 100)).model is None


async def test_cannot_touch_someone_elses_filter(h, session_factory):
    async with session_factory() as s:
        await repo.upsert_user(s, 999, "other", "Other")
        await repo.create_filter(s, 999, 10)  # filter id 1 belongs to user 999
        await s.commit()
    await h.message("/start")
    await h.press(kb.FilterCb(action="toggle", id=1))
    await h.press(kb.FilterCb(action="delete", id=1))
    async with session_factory() as s:
        f = await repo.get_filter(s, 1, 999)
        assert f is not None and f.is_active


async def test_filter_limit(h, session_factory):
    await h.message("/start")
    for _ in range(10):
        await h.press(kb.FilterCb(action="new"))
    before = len(h.session.calls)
    await h.press(kb.FilterCb(action="new"))  # 11th: refused with an alert
    assert len(h.session.calls) == before + 1
    async with session_factory() as s:
        assert len(await repo.list_filters(s, 100)) == 10


async def test_notifications_toggle(h, session_factory):
    await h.message("/start")
    await h.message(kb.BTN_NOTIFICATIONS)
    assert "включены" in h.session.texts[-1]
    await h.press(kb.NotifyCb(action="toggle"))
    assert "выключены" in h.session.texts[-1]
    async with session_factory() as s:
        assert (await s.get(repo.User, 100)).notifications_enabled is False


async def test_search_shows_matching_fresh_offers_cheapest_first(h, session_factory, make_listing):
    await h.message("/start")
    await h.message(kb.BTN_SEARCH)
    assert "нет активных фильтров" in h.session.texts[-1]

    await h.press(kb.FilterCb(action="new"))
    await h.press(kb.FilterCb(action="edit", id=1, field="collection"))
    await h.message("Plush Pepe")
    async with session_factory() as s:
        await repo.save_listings(
            s,
            [
                make_listing(slug="PlushPepe-1", price=900),
                make_listing(slug="PlushPepe-2", number=2, price=300),
                make_listing(slug="Other-3", collection="Heart Locket", price=1),
            ],
        )
        await s.commit()

    await h.message(kb.BTN_SEARCH)
    text = h.session.texts[-1]
    assert "Найдено: 2" in text and "Heart Locket" not in text
    assert text.index("PlushPepe-2") < text.index("PlushPepe-1")
    assert "Бот сделок не проводит" in text


async def test_stats_and_help_answer(h):
    await h.message("/start")
    await h.message(kb.BTN_STATS)
    assert "Статистика" in h.session.texts[-1]
    await h.message(kb.BTN_HELP)
    assert "не мгновенно" in h.session.texts[-1]
