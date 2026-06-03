from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import brand, cms, document, generate, health, pages, scrape
from app.services.db import init_db


app = FastAPI(
    title="Webtree Site Generator",
    description="AI-powered website generator producing BuilderElement schemas compatible with the webtree builder.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(brand.router)
app.include_router(scrape.router)
app.include_router(document.router)
app.include_router(pages.router)
app.include_router(generate.router)
app.include_router(cms.router)


@app.on_event("startup")
async def _on_startup() -> None:
    await init_db()
