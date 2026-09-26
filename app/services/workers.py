import json
import time
import asyncio
from redis.asyncio import Redis
from app.core.logger import get_logger
from app.api.routes import service, services
from app.services.bridges.models import Operation
from app.services.bridges.registry import registry
from app.infrastructure.memory import ShortTermMemory
from app.infrastructure.sqlite import mark_message_seen, save_message, set_external_id

logger = get_logger(__name__)


async def bridge_signal(session: str, operation: Operation, source = None, **kwargs):
    print(session, operation, source, kwargs)
    if source and source != "local":
        future = registry.get(source).signal(session, operation, **kwargs)

        if future is None:
            logger.debug("Identity Bridge in corrupt state; skipping operation=%s", operation)
            return None

        return await future

    return None


def transport_message_id(result) -> str | None:
    if isinstance(result, str):
        return result

    if not isinstance(result, dict):
        return None

    candidate = result.get("id") or result.get("messageId")
    if isinstance(candidate, dict):
        candidate = candidate.get("_serialized") or candidate.get("id")

    return str(candidate) if candidate is not None else None

async def publish_presence(redis: Redis, persona_id: str, presence: dict) -> None:
    logger.debug(f"[publish_presence] Publishing presence for persona_id={persona_id}, presence={presence}")
    try:
        await redis.publish(
            f"persona:presence:{persona_id}",
            json.dumps({
                "type": "presence",
                "persona_id": persona_id,
                **presence,
            }),
        )
        logger.info(f"[publish_presence] Successfully published presence for persona_id={persona_id}")
    except Exception as e:
        logger.error(f"[publish_presence] Error publishing presence for persona_id={persona_id}: {e}", exc_info=True)


async def worker_task(redis: Redis) -> None:
    life_tick_at = 0.0
    logger.info("[worker_task] Worker task started")
    try:
        memory = ShortTermMemory(redis)

        while True:
            try:
                now = asyncio.get_running_loop().time()

                if now >= life_tick_at:
                    logger.debug(f"[worker_task] Running life_tick at {now}")

                    for conversation_service in services():
                        persona_id = conversation_service.persona["id"]
                        logger.debug(f"[worker_task/life_tick] Processing life_tick for persona_id={persona_id}")
                        
                        try:
                            result = await conversation_service.life_tick()
                            logger.debug(f"[worker_task/life_tick] life_tick result for persona_id={persona_id}: changed={result['changed']}, became_online={result['became_online']}")

                            if result["changed"]:
                                logger.info(f"[worker_task/life_tick] Presence changed for persona_id={persona_id}, publishing update")
                                await publish_presence(redis, persona_id, result["presence"])
                                try:
                                    # TODO: possibly decide per bridge type e.g. online in insta but not in whatsapp, for now its always insta
                                    # Also thread_id needed which is not currently being handeled
                                    # await bridge_signal(persona_id, Operation.SET_ONLINE, source="instagram", online=bool(result["presence"].get("online")))
                                    pass
                                except Exception:
                                    logger.exception("Failed to update online presence for persona_id=%s", persona_id)

                            if result["became_online"]:
                                logger.info(f"[worker_task/life_tick] Persona became online: persona_id={persona_id}, processing unread messages")
                                unread_actions = await conversation_service.process_unread_messages()
                                logger.debug(f"[worker_task/life_tick] Processing {len(unread_actions)} unread message actions for persona_id={persona_id}")
                                
                                for action in unread_actions:
                                    channel = f"persona:out:{conversation_service.persona['id']}:{action['conversation_id']}"
                                    logger.debug(f"[worker_task/life_tick] Processing seen status for message_id={action['message_id']}, conversation_id={action['conversation_id']}")

                                    # mark seen in local db for persistence
                                    await mark_message_seen(action["conversation_id"], action["message_id"])

                                    source = action.get("source")
                                    if source and source != "local":
                                        # mark seen in actual identity bridge e.g. Whatsapp|Instagram
                                        await bridge_signal(persona_id, Operation.MARK_SEEN, source=source, chat_id=action['conversation_id'], message_id=action["message_id"])
                                    else:
                                        # mark seen in local UI (Realtime pub/sub)
                                        await redis.publish(channel, json.dumps({
                                            "type": "status",
                                            "persona_id": conversation_service.persona["id"],
                                            "conversation_id": action["conversation_id"],
                                            "message_id": action["message_id"],
                                            "status": "seen",
                                        }))

                            scheduled = await conversation_service.schedule_self_follow_ups()
                            if scheduled:
                                logger.info("[worker_task/life_tick] Scheduled %s self follow-up(s) for persona_id=%s", scheduled, persona_id)

                        except Exception as e:
                            logger.error(f"[worker_task/life_tick] Error processing life_tick for persona_id={persona_id}: {e}", exc_info=True)

                    life_tick_at = now + 30
                    logger.debug(f"[worker_task] Next life_tick scheduled for {life_tick_at}")

                logger.debug(f"[worker_task] Fetching due tasks from memory")
                due_items = await memory.due(limit=50)
                logger.debug(f"[worker_task] Got {len(due_items)} due tasks from memory")
                
                for item in due_items:
                    payload = item["payload"]
                    item_key = item["key"]
                    logger.debug(f"[worker_task/process_due_item] Processing task: key={item_key}, payload={payload}")

                    try:
                        if item_key == "presence_offline":
                            logger.info("[worker_task/presence_offline] Ignoring legacy presence timeout")
                            continue

                        logger.debug(f"[worker_task/reply] Getting conversation service for persona_id={payload['persona_id']}")
                        conversation_service = service(payload["persona_id"])

                        persona_id = payload["persona_id"]
                        conversation_id = payload["conversation_id"]
                        source = payload.get("source")

                        if "reply" in item_key:
                            user_mid = payload.get("user_message_id")
                            logger.debug(f"[worker_task/reply] Marking conversation messages as seen: conversation_id={conversation_id}, user_message_id={user_mid}")
                            try:
                                await conversation_service.mark_conversation_seen(
                                    conversation_id,
                                    source=source,
                                    up_to_message_id=int(user_mid) if user_mid is not None else None,
                                )
                            except Exception:
                                logger.exception("Failed to mark conversation seen: conversation_id=%s", conversation_id)

                        if "text" not in payload:
                            logger.debug(f"[worker_task/reply] Generating reply for conversation_id={conversation_id}, persona_id={persona_id}")
                            try:
                                start_time = time.time()
                                payload["text"] = await conversation_service.generate_reply(
                                    conversation_id,
                                    initiative=payload.get("trigger"),
                                    source=source,
                                    sender_id=payload.get("sender_id"),
                                    sender_name=payload.get("sender_name"),
                                )
                                elapsed = time.time() - start_time
                                logger.info(f"[worker_task/reply] Reply generated successfully in {elapsed:.2f}s: conversation_id={conversation_id}, text_length={len(payload['text'])}")
                            except Exception as e:
                                logger.error(f"[worker_task/reply] Error generating reply for conversation_id={conversation_id}: {e}", exc_info=True)
                                payload["text"] = "hmmm"
                        else:
                            logger.debug(f"[worker_task/reply] Text already in payload, skipping generation")

                        logger.debug(f"[worker_task/reply] Saving bot message: persona_id={persona_id}, conversation_id={conversation_id}, text_length={len(payload['text'])}")
                        bot_message_id = await save_message(payload['persona_id'], conversation_id, 'bot', payload["text"], 'seen', source=source)
                        logger.info(f"[worker_task/reply] Bot message saved: message_id={bot_message_id}, conversation_id={conversation_id}")
                        try:
                            await conversation_service.mark_conversation_seen(conversation_id, source=source)
                        except Exception:
                            logger.exception("Failed to mark conversation seen after bot reply: conversation_id=%s", conversation_id)

                        if source and source != "local":
                            # Route reply directly to bridge
                            try:
                                result = await bridge_signal(payload["persona_id"], Operation.SEND_MESSAGE, source=source, to=conversation_id, text=payload["text"])
                                external_id = transport_message_id(result)
                                if external_id:
                                    await set_external_id(bot_message_id, source, external_id)
                            except Exception:
                                logger.exception("Failed to send bot message through bridge %s: conversation_id=%s", source, conversation_id)
                        else:
                            # Route reply to local UI pub/sub
                            channel = f"persona:out:{payload['persona_id']}:{conversation_id}"
                            logger.debug(f"[worker_task/reply] Publishing bot message to UI: channel={channel}, message_id={bot_message_id}")

                            await redis.publish(channel, json.dumps({
                                "type": "message",
                                "persona_id": payload["persona_id"],
                                "conversation_id": conversation_id,
                                "message_id": bot_message_id,
                                "text": payload["text"],
                            }))
                            logger.debug(f"[worker_task/reply] Bot message published: conversation_id={conversation_id}")


                    except Exception as e:
                        logger.error(f"[worker_task/process_due_item] Error processing item key={item_key}: {e}", exc_info=True)

                logger.debug(f"[worker_task] Sleeping for 1 second before next iteration")
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"[worker_task] Unexpected error in worker loop: {e}", exc_info=True)
                await asyncio.sleep(1)
                
    except asyncio.CancelledError:
        logger.info("[worker_task] Worker task cancelled")
    except Exception as e:
        logger.error(f"[worker_task] Fatal error in worker task: {e}", exc_info=True)
