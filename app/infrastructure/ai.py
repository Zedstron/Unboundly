import json
from openai import AsyncOpenAI
from collections.abc import Sequence
from app.core.logger import get_logger
from app.domain.models import AgentResponse

logger = get_logger(__name__)

class AIProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        embedding_model: str | None = None,
    ) -> None:
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.embedding_model = embedding_model or model
        logger.debug(f"[OpenAICompatibleAI.__init__] Initialized AI service: model={model}, base_url={base_url}")

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

    async def embed(self, text: str) -> list[float]:
        response = await self.client.embeddings.create(model=self.embedding_model, input=text)
        return list(response.data[0].embedding)
