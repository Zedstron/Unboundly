import json
import os
from collections.abc import Sequence

from openai import AsyncOpenAI

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

from app.core.logger import get_logger
from app.core.prompts import get_prompt
from app.domain.models import AgentResponse

logger = get_logger(__name__)

_MAX_TOOL_ITERATIONS = 17


class AIProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        embedding_model: str | None = None,
        embedding_base_url: str | None = None,
        embedding_api_key: str | None = None,
        local_embedding_generator: bool | None = None,
        local_embedding_model: str | None = None,
    ) -> None:
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key or "")
        self.model = model

        if embedding_base_url is None and isinstance(embedding_model, str) and embedding_model.startswith("http"):
            embedding_base_url = embedding_model
            embedding_model = None

        self.embedding_base_url = embedding_base_url or base_url
        self.embedding_api_key = embedding_api_key or api_key or ""

        self.local_embedding_generator = self._resolve_bool_setting(
            local_embedding_generator,
            os.getenv("LOCAL_EMBEDDING_GENERATOR", "0"),
        )
        self.local_embedding_model_name = local_embedding_model or os.getenv(
            "LOCAL_EMBEDDING_MODEL",
            "sentence-transformers/all-MiniLM-L6-v2",
        )

        self.embedding_client = AsyncOpenAI(
            base_url=self.embedding_base_url,
            api_key=self.embedding_api_key,
        )

        self.embedding_model = embedding_model or model
        self.local_embedding_model = None

        if self.local_embedding_generator:
            if SentenceTransformer is None:
                raise RuntimeError(
                    "sentence-transformers is required when LOCAL_EMBEDDING_GENERATOR=1. "
                    "Install the project dependencies or enable the cloud embedding generator."
                )
            self.local_embedding_model = SentenceTransformer(self.local_embedding_model_name)
            logger.info(
                "[OpenAICompatibleAI.__init__] Using local sentence-transformer embeddings: %s",
                self.local_embedding_model_name,
            )

        logger.debug("[OpenAICompatibleAI.__init__] Initialized AI service: model=%s", model)

    @staticmethod
    def _resolve_bool_setting(setting: bool | str | None, env_value: str) -> bool:
        if setting is not None:
            return str(setting).strip().lower() in {"1", "true", "yes", "on"}
        return env_value.strip().lower() in {"1", "true", "yes", "on"}


    async def chat(self, messages: Sequence[dict[str, str]], *, temperature: float = 0.8) -> AgentResponse:
        logger.debug(f"[OpenAICompatibleAI.chat] Chat API call started: num_messages={len(messages)}, temperature={temperature}, model={self.model}")
        try:
            schema = AgentResponse.model_json_schema()
            schema["additionalProperties"] = False

            for definition in schema.get("$defs", {}).values():
                definition["additionalProperties"] = False

            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=list(messages),
                    temperature=temperature,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "persona_response",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                )
            except Exception:
                logger.warning("Structured JSON schema was rejected; retrying with JSON mode")
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=list(messages),
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )

            content = response.choices[0].message.content or "{}"
            result = AgentResponse.model_validate(json.loads(content))
            logger.debug("Structured response received from model, returning")

            return result
        except Exception as e:
            logger.error(f"[OpenAICompatibleAI.chat] Error calling chat API: {e}", exc_info=True)
            raise


    async def chat_with_tools(
        self,
        messages: Sequence[dict],
        tools: list[dict],
        *,
        temperature: float = 0.8,
        mcp_registry=None,
    ) -> AgentResponse:

        from app.infrastructure.mcp import registry as _default_registry

        mcp = mcp_registry or _default_registry
        conversation: list[dict] = list(messages)

        logger.debug("[AIProvider.chat_with_tools] Starting tool-use loop: %d message(s), %d tool(s)", len(conversation), len(tools))

        for iteration in range(_MAX_TOOL_ITERATIONS):
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=conversation,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None,
                temperature=temperature,
            )

            choice = response.choices[0]
            msg = choice.message

            if not msg.tool_calls:
                content = msg.content or ""
                logger.debug("[AIProvider.chat_with_tools] Final reply after %d iteration(s): %.120s", iteration + 1, content)

                try:
                    return AgentResponse.model_validate(json.loads(content))
                except Exception:
                    return AgentResponse(response=content, memories=[])

            conversation.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in msg.tool_calls
                ]
            })

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    raw_args = tc.function.arguments or "{}"
                    arguments = json.loads(raw_args)
                except json.JSONDecodeError:
                    arguments = {}

                logger.info(
                    "\n╔══════════════════════════════════════════════════════╗"
                    "\n║  🔧 TOOL CALL  [iter %d/%d]"
                    "\n║  Name : %s"
                    "\n║  Args : %s"
                    "\n╚══════════════════════════════════════════════════════╝",
                    iteration + 1,
                    _MAX_TOOL_ITERATIONS,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False)
                )

                try:
                    result_text = await mcp.call_tool(tool_name, arguments)
                except Exception as exc:
                    result_text = f"[Tool error] {exc}"
                    logger.warning("[AIProvider.chat_with_tools] Tool '%s' raised: %s", tool_name, exc)

                logger.info(
                    "\n╔══════════════════════════════════════════════════════╗"
                    "\n║  📦 TOOL RESULT"
                    "\n║  Name : %s"
                    "\n║  Result (%.80s...)"
                    "\n╚══════════════════════════════════════════════════════╝",
                    tool_name,
                    result_text,
                )

                conversation.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                })

        logger.warning("[AIProvider.chat_with_tools] Reached max tool iterations (%d)", _MAX_TOOL_ITERATIONS)
        last_content = conversation[-1].get("content") or ""
        try:
            return AgentResponse.model_validate(json.loads(last_content))
        except Exception:
            return AgentResponse(response=last_content or "(no reply)", memories=[])


    async def classify_event(self, text: str, allowed_events: Sequence[str]) -> str:
        if not allowed_events:
            raise ValueError("allowed_events must not be empty")

        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "event": {
                    "type": "string",
                    "enum": list(allowed_events),
                }
            },
            "required": ["event"],
        }

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": get_prompt("event_classification", {
                        "text": text,
                        "allowed_events": list(allowed_events),
                    }),
                },
                {"role": "user", "content": text},
            ],
            temperature=0.2,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "persona_event",
                    "strict": True,
                    "schema": schema,
                },
            },
        )

        content = response.choices[0].message.content
        if not content:
            raise ValueError("empty chat response")

        payload = json.loads(content)
        event_name = payload.get("event")
        if isinstance(event_name, str) and event_name in allowed_events:
            return event_name

        raise ValueError(f"invalid event returned: {event_name!r}")


    def _embed_local(self, text: str) -> list[float]:
        if self.local_embedding_model is None:
            if SentenceTransformer is None:
                raise RuntimeError(
                    "sentence-transformers is required when LOCAL_EMBEDDING_GENERATOR=1. "
                    "Install the project dependencies or disable local embeddings."
                )
            self.local_embedding_model = SentenceTransformer(self.local_embedding_model_name)

        embedding = self.local_embedding_model.encode(
            text,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return [float(value) for value in list(embedding)]

    async def embed(self, text: str) -> list[float]:
        if self.local_embedding_generator:
            try:
                return self._embed_local(text)
            except Exception as e:
                logger.error("[OpenAICompatibleAI.embed] Error generating local embedding: %s", e, exc_info=True)
                raise

        try:
            response = await self.embedding_client.embeddings.create(
                model=self.embedding_model or self.model,
                input=text,
            )
            return list(response.data[0].embedding)
        except Exception as e:
            logger.error(f"[OpenAICompatibleAI.embed] Error calling embedding API: {e}", exc_info=True)
            raise
