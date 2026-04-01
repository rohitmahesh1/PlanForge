# server/main.py
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.auth import router as auth_router
from app.api import message, calendar, tasks, prefs, ops, policies
from app.integrations import telegram_router, twilio_router

logger = logging.getLogger(__name__)
app = FastAPI(title="assistant-scheduler")
settings = get_settings()

# CORS (adjust for your clients)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth_router)          # /auth/*
app.include_router(message.router)       # /message
app.include_router(calendar.router)      # /calendar/*
app.include_router(tasks.router)         # /tasks/*
app.include_router(prefs.router)         # /prefs
app.include_router(ops.router)           # /ops/*
app.include_router(policies.router)      # /policies/*

if settings.enable_telegram_integration and telegram_router is not None:
    app.include_router(telegram_router)  # /integrations/telegram/*
elif settings.enable_telegram_integration:
    logger.warning("Telegram integration was enabled but could not be imported.")

if settings.enable_twilio_integration and twilio_router is not None:
    app.include_router(twilio_router)    # /integrations/twilio/*
elif settings.enable_twilio_integration:
    logger.warning("Twilio integration was enabled but could not be imported.")
