from collections.abc import Sequence
from openai import AsyncOpenAI
from app.core.logger import get_logger

logger = get_logger(__name__)

class OpenAICompatibleAI:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        logger.debug(f"[OpenAICompatibleAI.__init__] Initialized AI service: model={model}, base_url={base_url}")

    async def chat(self, messages: Sequence[dict[str, str]], *, temperature: float = 0.8) -> str:
        logger.debug(f"[OpenAICompatibleAI.chat] Chat API call started: num_messages={len(messages)}, temperature={temperature}, model={self.model}")
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=list(messages),
                temperature=temperature,
            )

            logger.debug("Response received from model, returning")
            return response.choices[0].message.content or "hmmm"
        except Exception as e:
            logger.error(f"[OpenAICompatibleAI.chat] Error calling chat API: {e}", exc_info=True)
            raise
