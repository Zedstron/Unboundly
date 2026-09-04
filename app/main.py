import os
import json
import asyncio
from pathlib import Path
from fastapi import FastAPI
from redis.asyncio import Redis
from app.core.config import settings
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from app.infrastructure.sqlite import init_db
from app.web.routes import router as web_router
from app.infrastructure.sqlite import mark_message_seen, save_message
from app.infrastructure.ai import OpenAICompatibleAI
from app.infrastructure.redis_store import RedisMemory
from app.api.routes import router as api_router, init_services, service


async def worker_task(redis: Redis, memory: RedisMemory) -> None:
    try:
        while True:
            for item in await memory.due(limit=50):
                payload = item["payload"]
                conversation_service = service(payload["persona_id"])

                if item["key"] == "reply_pending":
                    message_id = await mark_message_seen(
                        payload["conversation_id"], payload["user_message_id"]
                    )
                    if message_id:
                        channel = f"persona:out:{payload['persona_id']}{payload['conversation_id']}"
                        await redis.publish(channel, json.dumps({
                            "type": "status",
                            "persona_id": payload["persona_id"],
                            "conversation_id": payload["conversation_id"],
                            "message_id": message_id,
                            "status": "seen",
                        }))

                if "text" not in payload:
                    try:
                        messages = await conversation_service._prompt(payload['conversation_id'])
                        payload["text"] = await conversation_service.ai.chat(messages, temperature=0.85)
                    except:
                        payload["text"] = "hmmm"

                bot_message_id = await save_message(
                    payload['persona_id'], payload['conversation_id'], 'bot', payload["text"], 'seen'
                )

                channel = f"persona:out:{payload['persona_id']}{payload['conversation_id']}"
                await redis.publish(channel, json.dumps({
                    "type": "message",
                    "persona_id": payload["persona_id"],
                    "conversation_id": payload["conversation_id"],
                    "message_id": bot_message_id,
                    "text": payload["text"],
                }))

            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    redis = Redis.from_url(settings.redis_url, decode_responses=True)

    memory = RedisMemory(redis)
    ai = OpenAICompatibleAI(settings.llm_base_url, settings.llm_api_key, settings.llm_model)

    init_services(memory, ai, redis)

    app.state.redis = redis

    worker = asyncio.create_task(worker_task(redis, memory))
    app.state.worker = worker

    yield

    worker.cancel()
    try:
        await asyncio.wait_for(worker, timeout=1.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    try:
        await asyncio.wait_for(redis.close(), timeout=1.0)
        await asyncio.wait_for(redis.connection_pool.disconnect(), timeout=1.0)
    except Exception:
        pass

    asyncio.get_running_loop().call_soon(os._exit, 0)


BASE_DIR = Path(__file__).resolve().parent.parent
UI_DIR = BASE_DIR / "ui"

app = FastAPI(title="Persona Chat", lifespan=lifespan)

app.mount("/assets", StaticFiles(directory=UI_DIR / "assets"), name="assets")

app.include_router(web_router)
app.include_router(api_router, prefix="/api")