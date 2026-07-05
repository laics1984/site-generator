import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# App loggers default to WARNING, which silently drops the per-stage generation
# timing (logged at INFO via app.services.timing). Configure a root handler at
# INFO so that breakdown is visible alongside uvicorn's own logs.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)

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
    # Resolve + log the LLM backend once so it's visible at boot (and the
    # resolution is cached before the first request).
    from app.services.llm import resolve_llm_backend

    backend = resolve_llm_backend()
    model = settings.mlx_model if backend == "mlx" else settings.ollama_model
    logging.getLogger("app").info(
        "LLM backend: %s (model=%s, LLM_BACKEND=%s)", backend, model, settings.llm_backend
    )


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    # The LLM layer shares one keep-alive httpx client across calls; close it
    # so uvicorn reloads don't leak sockets.
    from app.services.llm import aclose_shared_client

    await aclose_shared_client()
