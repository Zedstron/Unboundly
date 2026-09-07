import os
import asyncio
from pathlib import Path
from fastapi import FastAPI
from redis.asyncio import Redis
from app.core.config import settings
from app.core.logger import get_logger
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from app.services.workers import worker_task
from app.infrastructure.sqlite import init_db
from app.web.routes import router as web_router
from app.api.routes import router as api_router, init_services

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.debug("[app/lifespan] Application startup: initializing database")
    try:
        await init_db()
        logger.info("[app/lifespan] Database initialized successfully")
    except Exception as e:
        logger.error(f"[app/lifespan] Database initialization failed: {e}", exc_info=True)
        raise

    logger.debug(f"[app/lifespan] Connecting to Redis: {settings.redis_url}")
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        logger.info("[app/lifespan] Redis connection established")
    except Exception as e:
        logger.error(f"[app/lifespan] Redis connection failed: {e}", exc_info=True)
        raise

    logger.debug("[app/lifespan] Initializing conversation services")
    try:
        init_services(redis)
        logger.info("[app/lifespan] Conversation services initialized")
    except Exception as e:
        logger.error(f"[app/lifespan] Failed to initialize services: {e}", exc_info=True)
        raise

    app.state.redis = redis

    logger.debug("[app/lifespan] Starting background worker task")
    worker = asyncio.create_task(worker_task(redis))
    app.state.worker = worker
    logger.debug("[app/lifespan] Background worker task started")

    yield

    logger.debug("[app/lifespan] Application shutdown: cancelling worker task")
    worker.cancel()

    try:
        await asyncio.wait_for(worker, timeout=1.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        logger.debug("[app/lifespan] Worker task cancelled or timed out")
    except Exception as e:
        logger.warning(f"[app/lifespan] Error cancelling worker: {e}")

    logger.debug("[app/lifespan] Closing Redis connection")
    try:
        await asyncio.wait_for(redis.close(), timeout=1.0)
        await asyncio.wait_for(redis.connection_pool.disconnect(), timeout=1.0)
        logger.info("[app/lifespan] Redis connection closed")
    except Exception as e:
        logger.warning(f"[app/lifespan] Error closing Redis: {e}")

    logger.info("[app/lifespan] Application shutdown complete")
    asyncio.get_running_loop().call_soon(os._exit, 0)


BASE_DIR = Path(__file__).resolve().parent.parent
UI_DIR = BASE_DIR / "ui"

app = FastAPI(title="Persona Chat", lifespan=lifespan)

app.mount("/assets", StaticFiles(directory=UI_DIR / "assets"), name="assets")

app.include_router(web_router)
app.include_router(api_router, prefix="/api")