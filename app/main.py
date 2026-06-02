"""Entrypoint FastAPI (Railway · uvicorn · SSE)."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import chat, documents, health
from .config import settings

app = FastAPI(title="Legal AI Backend", version="0.0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")] if settings.cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(documents.router)


@app.get("/")
async def root() -> dict:
    return {"service": "legal-ai-backend", "docs": "/docs", "health": "/health"}
