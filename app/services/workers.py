import json
import time
import asyncio
from redis.asyncio import Redis
from app.core.logger import get_logger
from app.api.routes import service, services
from app.infrastructure.memory import ShortTermMemory
from app.infrastructure.sqlite import mark_message_seen, save_message

logger = get_logger(__name__)

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
        logger.debug(f"[publish_presence] Successfully published presence for persona_id={persona_id}")
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
                                await publish_presence(
                                    redis,
                                    persona_id,
                                    result["presence"],
                                )

                            if result["became_online"]:
                                logger.info(f"[worker_task/life_tick] Persona became online: persona_id={persona_id}, processing unread messages")
                                unread_actions = await conversation_service.process_unread_messages()
                                logger.debug(f"[worker_task/life_tick] Processing {len(unread_actions)} unread message actions for persona_id={persona_id}")
                                
                                for action in unread_actions:
                                    channel = f"persona:out:{conversation_service.persona['id']}{action['conversation_id']}"
                                    logger.debug(f"[worker_task/life_tick] Publishing seen status for message_id={action['message_id']}, conversation_id={action['conversation_id']}")

                                    await redis.publish(channel, json.dumps({
                                        "type": "status",
                                        "persona_id": conversation_service.persona["id"],
                                        "conversation_id": action["conversation_id"],
                                        "message_id": action["message_id"],
                                        "status": "seen",
                                    }))
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
                            logger.debug(f"[worker_task/presence_offline] Handling presence_offline for persona_id={payload['persona_id']}")
                            persona_id = payload["persona_id"]
                            presence = await memory.get_presence(persona_id) or {}
                            logger.debug(f"[worker_task/presence_offline] Current presence: {presence}")
                            
                            presence["online"] = False
                            presence["updated_at"] = time.time()

                            await memory.set_presence(persona_id, presence)
                            logger.debug(f"[worker_task/presence_offline] Updated presence to offline for persona_id={persona_id}")
                            
                            await publish_presence(redis, persona_id, presence)
                            logger.info(f"[worker_task/presence_offline] Persona marked offline: persona_id={persona_id}")

                            continue

                        logger.debug(f"[worker_task/reply] Getting conversation service for persona_id={payload['persona_id']}")
                        conversation_service = service(payload["persona_id"])

                        persona_id = payload["persona_id"]
                        conversation_id = payload["conversation_id"]

                        if "reply" in item_key:
                            logger.debug(f"[worker_task/reply_pending] Marking message as seen: conversation_id={conversation_id}, user_message_id={payload['user_message_id']}")
                            message_id = await mark_message_seen(conversation_id, payload["user_message_id"])

                            if not message_id:
                                logger.warning(f"[worker_task/reply_pending] Failed to mark message as seen: conversation_id={conversation_id}")
                                continue

                            channel = f"persona:out:{payload['persona_id']}{conversation_id}"
                            logger.debug(f"[worker_task/reply_pending] Publishing seen status: channel={channel}, message_id={message_id}")
                            
                            await redis.publish(channel, json.dumps({
                                "type": "status",
                                "persona_id": payload["persona_id"],
                                "conversation_id": conversation_id,
                                "message_id": message_id,
                                "status": "seen",
                            }))

                        logger.debug(f"[worker_task/reply] Updating presence to online for persona_id={persona_id}")
                        presence = {
                            "online": True,
                            "probability": 1.0,
                            "last_seen": time.time(),
                            "updated_at": time.time()
                        }

                        await memory.set_presence(persona_id, presence)
                        await publish_presence(redis, persona_id, presence)
                        logger.debug(f"[worker_task/reply] Presence updated to online for persona_id={persona_id}")

                        if "text" not in payload:
                            logger.info(f"[worker_task/reply] Generating reply for conversation_id={conversation_id}, persona_id={persona_id}")
                            try:
                                start_time = time.time()
                                payload["text"] = await conversation_service.generate_reply(conversation_id)
                                elapsed = time.time() - start_time
                                logger.info(f"[worker_task/reply] Reply generated successfully in {elapsed:.2f}s: conversation_id={conversation_id}, text_length={len(payload['text'])}")
                            except Exception as e:
                                logger.error(f"[worker_task/reply] Error generating reply for conversation_id={conversation_id}: {e}", exc_info=True)
                                payload["text"] = "hmmm"
                        else:
                            logger.debug(f"[worker_task/reply] Text already in payload, skipping generation")

                        logger.debug(f"[worker_task/reply] Saving bot message: persona_id={persona_id}, conversation_id={conversation_id}, text_length={len(payload['text'])}")
                        bot_message_id = await save_message(
                            payload['persona_id'],
                            conversation_id,
                            'bot',
                            payload["text"],
                            'seen'
                        )
                        logger.info(f"[worker_task/reply] Bot message saved: message_id={bot_message_id}, conversation_id={conversation_id}")

                        channel = f"persona:out:{payload['persona_id']}{conversation_id}"
                        logger.debug(f"[worker_task/reply] Publishing bot message: channel={channel}, message_id={bot_message_id}")

                        await redis.publish(channel, json.dumps({
                            "type": "message",
                            "persona_id": payload["persona_id"],
                            "conversation_id": conversation_id,
                            "message_id": bot_message_id,
                            "text": payload["text"],
                        }))
                        logger.debug(f"[worker_task/reply] Bot message published: conversation_id={conversation_id}")

                        if item_key in { "reply", "reply_pending" }:
                            logger.debug(f"[worker_task/reply] Scheduling presence_offline for persona_id={persona_id} in 30 seconds")
                            await memory.schedule("presence_offline", { "persona_id": payload["persona_id"] }, time.time() + 30)
                    
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