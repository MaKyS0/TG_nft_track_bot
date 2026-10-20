"""JSON API for the Mini App. Every /api/* request carries ``Authorization: tma <initData>``."""

import json
import logging
import time
from datetime import timedelta

from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.parsing import FILTER_FIELDS, InvalidValue, validate_filter_payload
from app.config import Settings
from app.db import repo
from app.db.models import User, UserFilter, utcnow
from app.domain import NANO_TON, Listing
from app.matching import matches
from app.services.search import find_offers
from app.web.auth import InitDataError, validate_init_data
from app.web.keys import DB_SESSION, RATE_LIMITER, SESSION_FACTORY, SETTINGS, USER_ID

log = logging.getLogger(__name__)

MAX_LISTINGS = 100
RATE_BURST = 30  # a page load is 3 requests; this leaves room for normal clicking
RATE_PER_SECOND = 5.0


class UserRateLimiter:
    """Token bucket per authenticated user (in memory). Stops one account from hammering the API."""

    def __init__(self, burst: int = RATE_BURST, per_second: float = RATE_PER_SECOND) -> None:
        self._burst, self._rate = burst, per_second
        self._state: dict[int, tuple[float, float]] = {}  # user -> (tokens, last seen)

    def allow(self, user_id: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        tokens, last = self._state.get(user_id, (float(self._burst), now))
        tokens = min(float(self._burst), tokens + (now - last) * self._rate)
        allowed = tokens >= 1.0
        self._state[user_id] = (tokens - 1.0 if allowed else tokens, now)
        if len(self._state) > 10_000:  # keep memory bounded
            self._state = {u: v for u, v in self._state.items() if now - v[1] < 60}
        return allowed


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _hex(color: int | None) -> str | None:
    return None if color is None else f"#{color & 0xFFFFFF:06x}"


def listing_json(item: Listing) -> dict:
    return {
        "slug": item.slug,
        "url": item.url,
        "collection": item.collection,
        "number": item.number,
        "price": item.price,
        "currency": item.currency,
        "model": item.model,
        "model_rarity": item.model_rarity,
        "backdrop": item.backdrop,
        "backdrop_rarity": item.backdrop_rarity,
        "backdrop_center": _hex(item.backdrop_center),
        "backdrop_edge": _hex(item.backdrop_edge),
        "symbol": item.symbol,
        "symbol_rarity": item.symbol_rarity,
        "owner_name": item.owner_name,
        "owner_username": item.owner_username,
        "owner_url": item.owner_url,
        "image_url": item.image_url,
        "seen_at": item.seen_at.isoformat() if item.seen_at else None,
        "market": item.market,
        "market_title": item.market_title,
        "market_url": item.market_url,
    }


def filter_json(f: UserFilter, matches_now: int | None = None) -> dict:
    values = {field: getattr(f, field) for field in FILTER_FIELDS}
    for key in ("min_ton", "max_ton"):  # stored as nanoTON, exposed in TON
        if values[key] is not None:
            values[key] = values[key] / NANO_TON
    data = {"id": f.id, "name": f.name, "is_active": f.is_active, **values}
    if matches_now is not None:
        data["matches"] = matches_now  # offers seen recently that satisfy this filter
    return data


# ── middleware ─────────────────────────────────────────────────────────────


@web.middleware
async def api_middleware(request: web.Request, handler):
    """Authenticate /api/* by initData, give the handler a DB session, commit only on success."""
    if not request.path.startswith("/api/"):
        return await handler(request)

    settings: Settings = request.app[SETTINGS]
    try:
        scheme, _, init_data = request.headers.get("Authorization", "").partition(" ")
        if scheme != "tma" or not init_data:
            raise ApiError(401, "Откройте мини-приложение из Telegram.")
        try:
            tg_user = validate_init_data(
                init_data, settings.bot_token.get_secret_value(), settings.webapp_auth_max_age
            )
        except InitDataError as e:
            log.info("Rejected initData: %s", e)
            raise ApiError(401, "Сеанс устарел. Закройте и снова откройте мини-приложение.") from e

        if not request.app[RATE_LIMITER].allow(tg_user.id):
            raise ApiError(429, "Слишком много запросов. Подождите немного.")

        factory: async_sessionmaker[AsyncSession] = request.app[SESSION_FACTORY]
        async with factory() as session:
            await repo.upsert_user(session, tg_user.id, tg_user.username, tg_user.first_name)
            request[DB_SESSION] = session
            request[USER_ID] = tg_user.id
            response = await handler(request)
            await session.commit()
            return response
    except ApiError as e:
        return web.json_response({"error": e.message}, status=e.status)
    except web.HTTPException:
        raise
    except Exception:
        log.exception("API request failed: %s %s", request.method, request.path)
        return web.json_response({"error": "Внутренняя ошибка. Попробуйте позже."}, status=500)


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ApiError(400, "Тело запроса должно быть JSON.") from e
    if not isinstance(body, dict):
        raise ApiError(400, "Тело запроса должно быть JSON-объектом.")
    return body


async def _own_filter(request: web.Request) -> UserFilter:
    try:
        filter_id = int(request.match_info["id"])
    except ValueError:
        raise ApiError(404, "Фильтр не найден.") from None
    f = await repo.get_filter(request[DB_SESSION], filter_id, request[USER_ID])
    if f is None:
        raise ApiError(404, "Фильтр не найден.")
    return f


# ── handlers ───────────────────────────────────────────────────────────────


async def get_me(request: web.Request) -> web.Response:
    session: AsyncSession = request[DB_SESSION]
    settings: Settings = request.app[SETTINGS]
    user = await session.get(User, request[USER_ID])
    mine = await repo.user_stats(session, user.id)
    total = await repo.global_stats(session)
    return web.json_response(
        {
            "user": {"id": user.id, "first_name": user.first_name, "username": user.username},
            "notifications_enabled": user.notifications_enabled,
            "source": settings.gift_source,
            "fresh_minutes": settings.search_fresh_minutes,
            "stats": {
                "filters_total": mine.filters_total,
                "filters_active": mine.filters_active,
                "notifications_received": mine.notifications_received,
                "users": total.users,
                "active_filters": total.active_filters,
                "listings_known": total.listings_known,
                "notifications_sent": total.notifications_sent,
                "last_poll_at": total.last_poll_at.isoformat() if total.last_poll_at else None,
            },
        }
    )


async def set_notifications(request: web.Request) -> web.Response:
    body = await _json_body(request)
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise ApiError(400, "Поле enabled должно быть true или false.")
    user = await request[DB_SESSION].get(User, request[USER_ID])
    user.notifications_enabled = enabled
    return web.json_response({"notifications_enabled": enabled})


async def list_filters(request: web.Request) -> web.Response:
    settings: Settings = request.app[SETTINGS]
    filters = await repo.list_filters(request[DB_SESSION], request[USER_ID])
    fresh = await repo.fresh_listings(request[DB_SESSION], utcnow() - timedelta(minutes=settings.search_fresh_minutes))
    return web.json_response(
        {"filters": [filter_json(f, sum(1 for item in fresh if matches(item, f))) for f in filters]}
    )


async def create_filter(request: web.Request) -> web.Response:
    settings: Settings = request.app[SETTINGS]
    body = await _json_body(request)
    try:
        changes = validate_filter_payload(body)
    except InvalidValue as e:
        raise ApiError(400, str(e)) from e
    f = await repo.create_filter(request[DB_SESSION], request[USER_ID], settings.max_filters_per_user)
    if f is None:
        raise ApiError(409, f"Максимум фильтров: {settings.max_filters_per_user}. Удалите ненужные.")
    for key, value in changes.items():
        setattr(f, key, value)
    return web.json_response({"filter": filter_json(f)}, status=201)


async def update_filter(request: web.Request) -> web.Response:
    f = await _own_filter(request)
    try:
        changes = validate_filter_payload(await _json_body(request), current=f)
    except InvalidValue as e:
        raise ApiError(400, str(e)) from e
    for key, value in changes.items():
        setattr(f, key, value)
    return web.json_response({"filter": filter_json(f)})


async def delete_filter(request: web.Request) -> web.Response:
    f = await _own_filter(request)
    await repo.delete_filter(request[DB_SESSION], f.id, request[USER_ID])
    return web.json_response({"ok": True})


async def get_offers(request: web.Request) -> web.Response:
    settings: Settings = request.app[SETTINGS]
    result = await find_offers(request[DB_SESSION], request[USER_ID], settings.search_fresh_minutes)
    return web.json_response(
        {
            "has_filters": result.has_filters,
            "total": len(result.listings),
            "offers": [listing_json(item) for item in result.listings[:MAX_LISTINGS]],
        }
    )


def add_routes(app: web.Application) -> None:
    app.router.add_get("/api/me", get_me)
    app.router.add_post("/api/notifications", set_notifications)
    app.router.add_get("/api/filters", list_filters)
    app.router.add_post("/api/filters", create_filter)
    app.router.add_patch("/api/filters/{id}", update_filter)
    app.router.add_delete("/api/filters/{id}", delete_filter)
    app.router.add_get("/api/offers", get_offers)
