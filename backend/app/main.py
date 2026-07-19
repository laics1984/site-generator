import logging
from contextlib import asynccontextmanager

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
from app.routers import brand, cms, document, generate, health, pages, preview, scrape
from app.services.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    await init_db()
    # Resolve + log the LLM backend once so it's visible at boot (and the
    # resolution is cached before the first request).
    from app.services.llm import resolve_llm_backend

    backend = resolve_llm_backend()
    model = settings.mlx_model if backend == "mlx" else settings.ollama_model
    logging.getLogger("app").info(
        "LLM backend: %s (model=%s, LLM_BACKEND=%s)", backend, model, settings.llm_backend
    )
    if settings.reasoning_model:
        logging.getLogger("app").info(
            "LLM reasoning role: %s (model=%s, base_url=%s, think=%s)",
            (settings.reasoning_backend or backend).lower(),
            settings.reasoning_model,
            settings.reasoning_base_url or "(backend default)",
            settings.reasoning_think,
        )

    yield

    # --- shutdown ---
    # The LLM layer shares one keep-alive httpx client across calls; close it
    # so uvicorn reloads don't leak sockets.
    from app.services.llm import aclose_shared_client

    await aclose_shared_client()


app = FastAPI(
    title="Webtree Site Generator",
    description="AI-powered website generator producing BuilderElement schemas compatible with the webtree builder.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    # Explicit rather than wildcard: a credentialed CORS surface should only
    # advertise the methods/headers the API actually uses.
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

app.include_router(health.router)
app.include_router(brand.router)
app.include_router(scrape.router)
app.include_router(document.router)
app.include_router(pages.router)
app.include_router(generate.router)
app.include_router(cms.router)
app.include_router(preview.router)
