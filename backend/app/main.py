import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.api.v1.health import router as health_router
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.redis import close_redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("sia")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    from app.bot.factory import get_bot

    bot = get_bot()
    if bot is not None and settings.public_url and settings.telegram_webhook_secret.get_secret_value():
        try:
            await bot.set_webhook(f"{settings.public_url.rstrip('/')}/api/v1/telegram/webhook",
                                  secret_token=settings.telegram_webhook_secret.get_secret_value(),
                                  allowed_updates=["message", "callback_query"], drop_pending_updates=False)
        except Exception:
            log.exception("could not register Telegram webhook")
    yield
    if bot is not None:
        await bot.session.close()
    await close_redis()
    await dispose_engine()




def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Social Intelligence AI", version="0.1.0", lifespan=lifespan,
                  docs_url=None if settings.env == "production" else "/docs",
                  redoc_url=None, openapi_url="/api/v1/openapi.json")
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                           allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    app.include_router(health_router)
    app.include_router(api_router)
    return app


app = create_app()
