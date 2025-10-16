# server/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import router as auth_router
from app.api import message, calendar, tasks, prefs, ops, policies
from app.integrations import telegram_router, twilio_router

app = FastAPI(title="assistant-scheduler")

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
app.include_router(telegram_router)      # /integrations/telegram/*
app.include_router(twilio_router)        # /integrations/twilio/*
