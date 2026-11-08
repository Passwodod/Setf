from fastapi import APIRouter

from app.api.v1 import approvals, auth, bot_webhook, persons, privacy, telegram

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(persons.router)
api_router.include_router(telegram.router)
api_router.include_router(privacy.router)
api_router.include_router(approvals.router)
api_router.include_router(bot_webhook.router)
