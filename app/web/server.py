import logging
from pathlib import Path

from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.web.api import UserRateLimiter, add_routes, api_middleware
from app.web.keys import RATE_LIMITER, SESSION_FACTORY, SETTINGS

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Telegram's own script is the only third-party code; everything else is served from this origin.
CSP = (
    "default-src 'none'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'"
)


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def _healthz(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _security_headers(request: web.Request, response: web.StreamResponse) -> None:
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Server"] = "nft-tracker"  # do not advertise Python/aiohttp versions
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"


def create_web_app(session_factory: async_sessionmaker[AsyncSession], settings: Settings) -> web.Application:
    app = web.Application(middlewares=[api_middleware], client_max_size=16 * 1024)
    app[SETTINGS] = settings
    app[SESSION_FACTORY] = session_factory
    app[RATE_LIMITER] = UserRateLimiter()
    app.on_response_prepare.append(_security_headers)
    app.router.add_get("/", _index)
    app.router.add_get("/healthz", _healthz)
    add_routes(app)
    app.router.add_static("/static/", STATIC_DIR)
    return app


async def start_web(app: web.Application, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("Mini App server listening on %s:%d", host, port)
    return runner
