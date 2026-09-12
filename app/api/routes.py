import json
import asyncio
from redis.asyncio import Redis
from app.core.logger import get_logger
from app.domain.models import MessageIn
from app.services.conversation import ConversationService
from app.infrastructure.persona import PersonaStore
from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect


logger = get_logger(__name__)
router = APIRouter(tags=["chat"])
_services: dict[str: ConversationService] | None = {}
_redis: Redis | None = None


def init_services(redis: Redis) -> None:
    global _services, _redis
    _redis = redis

    store = PersonaStore()
    logger.info(f"[init_services] Initializing services for {len(store.available_personas())} personas")

    for persona in store.available_personas():
        personality = store.get_persona(persona["id"])
        _services[persona["id"]] = ConversationService(personality, redis)
        logger.debug(f"[init_services] Service initialized for persona_id={persona['id']}")

def service(id: str) -> ConversationService:
    if not _services:
        raise RuntimeError("Service not initialized")

    if id not in _services:
        raise RuntimeError("Invalid Persona Id provided")

    return _services.get(id)


def services() -> list[ConversationService]:
    return list((_services or {}).values())


@router.get("/{pid}/persona")
async def get_persona(pid: str):
    logger.debug(f"[get_persona] Fetching persona: pid={pid}")
    try:
        persona = PersonaStore().get_persona(pid)
        logger.debug(f"[get_persona] Persona fetched successfully: pid={pid}")
        return persona
    except FileNotFoundError as exc:
        logger.warning(f"[get_persona] Persona not found: pid={pid}")
        raise HTTPException(404, detail="Persona not found") from exc


@router.put("/{pid}/persona")
async def update_persona(pid: str, payload: dict = Body(...)):
    logger.info(f"[update_persona] Updating persona: pid={pid}")
    try:
        updated = PersonaStore().save_persona(pid, payload)
        logger.debug(f"[update_persona] Persona saved to store")

        current_service = service(pid)
        current_service.update_persona(updated)
        logger.info(f"[update_persona] Persona updated and service refreshed: pid={pid}")
        return updated
    except FileNotFoundError as exc:
        logger.warning(f"[update_persona] Persona not found: pid={pid}")
        raise HTTPException(404, detail="Persona not found") from exc
    except ValueError as exc:
        logger.error(f"[update_persona] Validation error for persona: pid={pid}, error={exc}")
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/{pid}/messages")
async def send_message(pid: str, payload: MessageIn):
    logger.info(f"[send_message] Received message for persona_id={pid}, conversation_id={payload.conversation_id}, text_length={len(payload.text)}")
    try:
        result = await service(pid).ingest(payload.conversation_id, payload.text)
        logger.debug(f"[send_message] Message ingested successfully: decision={result.get('decision')}, message_id={result.get('message_id')}")
        return result
    except Exception as exc:
        logger.error(f"[send_message] Error processing message for persona_id={pid}: {exc}", exc_info=True)
        raise HTTPException(502, detail=str(exc)) from exc

@router.get("/{pid}/conversation/{conversation_id}")
async def events(pid: str, conversation_id: str):
    logger.debug(f"[events] Fetching conversation: persona_id={pid}, conversation_id={conversation_id}")
    try:
        result = await service(pid).get_conversation(conversation_id)
        logger.debug(f"[events] Conversation fetched successfully: conversation_id={conversation_id}")
        return result
    except Exception as exc:
        logger.error(f"[events] Error fetching conversation: pid={pid}, conversation_id={conversation_id}, error={exc}", exc_info=True)
        raise HTTPException(502, detail=str(exc)) from exc

@router.delete("/{pid}/conversation/{conversation_id}")
async def clear_chat(pid: str, conversation_id: str):
    logger.info(f"[clear_chat] Clearing conversation: persona_id={pid}, conversation_id={conversation_id}")
    try:
        deleted = await service(pid).clear_conversation(conversation_id)
        logger.info(f"[clear_chat] Conversation cleared: conversation_id={conversation_id}, deleted_count={deleted}")
        return { "deleted": deleted }
    except Exception as exc:
        logger.error(f"[clear_chat] Error clearing conversation: pid={pid}, conversation_id={conversation_id}, error={exc}", exc_info=True)
        raise HTTPException(502, detail=str(exc)) from exc

@router.delete("/{pid}/conversation/{conversation_id}/messages/{message_id}")
async def delete_chat_message(pid: str, conversation_id: str, message_id: int):
    logger.debug(f"[delete_chat_message] Deleting message: persona_id={pid}, conversation_id={conversation_id}, message_id={message_id}")
    try:
        deleted = await service(pid).delete_message(conversation_id, message_id)
        if not deleted:
            logger.warning(f"[delete_chat_message] Message not found: conversation_id={conversation_id}, message_id={message_id}")
            raise HTTPException(404, detail="Message not found")

        logger.info(f"[delete_chat_message] Message deleted: conversation_id={conversation_id}, message_id={message_id}")
        return { "deleted": True }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[delete_chat_message] Error deleting message: conversation_id={conversation_id}, message_id={message_id}, error={exc}", exc_info=True)
        raise HTTPException(502, detail=str(exc)) from exc

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    logger.debug("[websocket] New WebSocket connection attempt")

    await websocket.accept()
    logger.info("[websocket] WebSocket connection accepted")
    
    if _redis is None:
        logger.error("[websocket] Redis not initialized, closing connection")
        await websocket.close(code=1008, reason="Redis not initialized")
        return
    
    pubsub = _redis.pubsub()
    subscribed_channels = set()
    pubsub_lock = asyncio.Lock()
    
    async def handle_redis_messages():
        try:
            logger.debug("[websocket/redis_listener] Redis listener started")
            while True:
                if pubsub.connection is None:
                    await asyncio.sleep(0.05)
                    continue

                async with pubsub_lock:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=0.1,
                    )

                if message is None:
                    continue

                message_type = message.get("type")
                if message_type != "message":
                    continue

                channel = message["channel"]
                raw_data = message["data"]

                try:
                    data = json.loads(raw_data)
                except (TypeError, json.JSONDecodeError):
                    logger.warning(f"[websocket/redis_listener] Forwarding non-JSON message from channel: {channel}")
                    data = {
                        "type": "redis_message",
                        "channel": channel,
                        "data": raw_data,
                    }

                logger.debug(f"[websocket/redis_listener] Forwarding message from channel: {channel}")
                await websocket.send_json(data)
        except Exception as e:
            logger.error(f"[websocket/redis_listener] Error: {e}", exc_info=True)
    
    async def handle_client_commands():
        try:
            logger.debug("[websocket/client_handler] Client command handler started")
            while True:
                data = await websocket.receive_json()
                command_type = data.get("type")
                logger.debug(f"[websocket/client_handler] Received command: type={command_type}")
                
                if command_type == "subscribe":
                    channel = data.get("channel")
                    if channel and channel not in subscribed_channels:
                        logger.info(f"[websocket/client_handler] Subscribing to channel: {channel}")
                        async with pubsub_lock:
                            await pubsub.subscribe(channel)

                        subscribed_channels.add(channel)
                        await websocket.send_json({"type": "subscribed", "channel": channel})
                    else:
                        logger.debug(f"[websocket/client_handler] Already subscribed to channel: {channel}")

                elif command_type == "unsubscribe":
                    channel = data.get("channel")
                    if channel and channel in subscribed_channels:
                        logger.info(f"[websocket/client_handler] Unsubscribing from channel: {channel}")
                        await pubsub.unsubscribe(channel)
                        subscribed_channels.discard(channel)

        except WebSocketDisconnect:
            logger.warning("[websocket/client_handler] Client disconnected")
        except Exception as e:
            logger.error(f"[websocket/client_handler] Error: {e}", exc_info=True)
    
    try:
        logger.debug(f"[websocket] Starting message handlers with {len(subscribed_channels)} subscribed channels")
        await asyncio.gather(handle_redis_messages(), handle_client_commands())
    finally:
        logger.info(f"[websocket] Cleaning up {len(subscribed_channels)} subscribed channels")
        for ch in subscribed_channels:
            await pubsub.unsubscribe(ch)

        await pubsub.close()
        logger.info("[websocket] Connection cleanup complete")

@router.get("/{conversation_id}/personas")
async def get_personas(conversation_id: str):
    logger.debug(f"[get_personas] Fetching personas for conversation: conversation_id={conversation_id}")
    try:
        personas = PersonaStore().available_personas()
        logger.debug(f"[get_personas] Found {len(personas)} personas, fetching presence and messages")

        for p in personas:
            p["last_message"] = await service(p["id"]).get_last_message(conversation_id)
            p["presence"] = await service(p["id"]).get_presence()
        
        logger.debug(f"[get_personas] Successfully enriched {len(personas)} personas with presence and messages")
        return personas
    except Exception as exc:
        logger.error(f"[get_personas] Error fetching personas: conversation_id={conversation_id}, error={exc}", exc_info=True)
        raise HTTPException(502, detail=str(exc)) from exc