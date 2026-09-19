import asyncio
from redis.asyncio import Redis
from app.core.config import settings
from app.core.logger import get_logger
from app.api.routes import init_services
from app.services.workers import worker_task
from app.infrastructure.sqlite import init_db
from app.infrastructure.mcp import registry as mcp

logger = get_logger(__name__)

class AppContext:
    def __init__(self):
        self.redis: Redis | None = None
        self.worker: asyncio.Task | None = None

    async def startup(self):
        logger.debug("Application startup")

        await init_db()
        logger.info("Database initialized")

        try:
            await mcp.connect()

            if mcp.has_tools:
                logger.info("MCP tools available: %s", mcp.get_tool_names())
            else:
                logger.info("No MCP tools configured")
        except Exception:
            logger.exception("MCP registry connection failed")

        logger.debug("Connecting to Redis: %s", settings.redis_url)

        self.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True
        )

        logger.info("Redis connection established")

        await init_services(self.redis)
        logger.info("Conversation services initialized")

        self.worker = asyncio.create_task(
            worker_task(self.redis)
        )

        logger.info("Background worker started")

    async def shutdown(self):
        logger.debug("Application shutdown")

        if self.worker:
            self.worker.cancel()

            try:
                await asyncio.wait_for(
                    self.worker,
                    timeout=1.0,
                )
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                logger.exception("Error stopping worker")

        if self.redis:
            try:
                await asyncio.wait_for(
                    self.redis.aclose(),
                    timeout=1.0,
                )

                await asyncio.wait_for(
                    self.redis.connection_pool.disconnect(),
                    timeout=1.0,
                )

                logger.info("Redis connection closed")

            except Exception:
                logger.exception("Error closing Redis")

        try:
            await mcp.close()
        except Exception:
            logger.exception("Error closing MCP registry")

        logger.info("Application shutdown complete")