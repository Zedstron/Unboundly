import json
import asyncio
from redis.asyncio import Redis
from app.core.logger import get_logger
from app.domain.models import MessageIn
from app.services.conversation import ConversationService
from app.infrastructure.persona_store import PersonaStore
from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect


logger = get_logger(__name__)
router = APIRouter(tags=["chat"])
_services: dict[str: ConversationService] | None = {}
_redis: Redis | None = None


def init_services(memory, ai, redis: Redis) -> None:
    global _services, _redis
    _redis = redis

    store = PersonaStore()

    for persona in store.available_personas():
        personality = store.get_persona(persona["id"])
        _services[persona["id"]] = ConversationService(personality, memory, ai)

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
    try:
        return PersonaStore().get_persona(pid)
    except FileNotFoundError as exc:
        raise HTTPException(404, detail="Persona not found") from exc


@router.put("/{pid}/persona")
async def update_persona(pid: str, payload: dict = Body(...)):
    try:
        updated = PersonaStore().save_persona(pid, payload)
        current_service = service(pid)
        current_service.update_persona(updated)
        return updated
    except FileNotFoundError as exc:
        raise HTTPException(404, detail="Persona not found") from exc
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/{pid}/messages")
async def send_message(pid: str, payload: MessageIn):
    try:
        return await service(pid).ingest(payload.conversation_id, payload.text)
    except Exception as exc:
        raise HTTPException(502, detail=str(exc)) from exc

@router.get("/{pid}/conversation/{conversation_id}")
async def events(pid: str, conversation_id: str):
    return await service(pid).get_conversation(conversation_id)

@router.delete("/{pid}/conversation/{conversation_id}")
async def clear_chat(pid: str, conversation_id: str):
    try:
        deleted = await service(pid).clear_conversation(conversation_id)
        return {"deleted": deleted}
    except Exception as exc:
        raise HTTPException(502, detail=str(exc)) from exc

@router.delete("/{pid}/conversation/{conversation_id}/messages/{message_id}")
async def delete_chat_message(pid: str, conversation_id: str, message_id: int):
    try:
        deleted = await service(pid).delete_message(conversation_id, message_id)
        if not deleted:
            raise HTTPException(404, detail="Message not found")
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, detail=str(exc)) from exc

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    if _redis is None:
        await websocket.close(code=1008, reason="Redis not initialized")
        return
    
    pubsub = _redis.pubsub()
    subscribed_channels = set()
    
    async def handle_redis_messages():
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        await websocket.send_json(data)
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to decode message: {message['data']}")
        except Exception as e:
            logger.error(f"Redis listener error: {e}", exc_info=True)
    
    async def handle_client_commands():
        try:
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "subscribe":
                    channel = data.get("channel")
                    if channel and channel not in subscribed_channels:
                        await pubsub.subscribe(channel)
                        subscribed_channels.add(channel)

                elif data.get("type") == "unsubscribe":
                    channel = data.get("channel")
                    if channel and channel in subscribed_channels:
                        await pubsub.unsubscribe(channel)
                        subscribed_channels.discard(channel)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"Client command error: {e}", exc_info=True)
    
    try:
        await asyncio.gather(handle_redis_messages(), handle_client_commands())
    finally:
        for ch in subscribed_channels:
            await pubsub.unsubscribe(ch)

        await pubsub.close()

@router.get("/{conversation_id}/personas")
async def get_personas(conversation_id: str):
    personas = PersonaStore().available_personas()

    for p in personas:
        p["last_message"] = await service(p["id"]).get_last_message(conversation_id)
        p["presence"] = await service(p["id"]).get_presence()

    return personas