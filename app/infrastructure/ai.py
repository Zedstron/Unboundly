import json
from openai import AsyncOpenAI
from collections.abc import Sequence
from app.core.logger import get_logger
from app.core.prompts import get_prompt
from app.domain.models import AgentResponse

logger = get_logger(__name__)

class AIProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        embedding_model: str | None = None,
        embedding_base_url: str | None = None,
        embedding_api_key: str | None = None,
    ) -> None:
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key or "")
        self.model = model

        if embedding_base_url is None and isinstance(embedding_model, str) and embedding_model.startswith("http"):
            embedding_base_url = embedding_model
            embedding_model = None

        self.embedding_base_url = embedding_base_url or base_url
        self.embedding_api_key = embedding_api_key or api_key or ""

        self.embedding_client = AsyncOpenAI(
            base_url=self.embedding_base_url,
            api_key=self.embedding_api_key,
        )

        self.embedding_model = embedding_model or model
        logger.debug("[OpenAICompatibleAI.__init__] Initialized AI service: model=%s", model)

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

    async def embed(self, text: str) -> list[float]:
        try:
            response = await self.embedding_client.embeddings.create(
                model=self.embedding_model or self.model,
                input=text,
            )
            return list(response.data[0].embedding)
        except Exception as e:
            logger.error(f"[OpenAICompatibleAI.embed] Error calling embedding API: {e}", exc_info=True)
            raise
