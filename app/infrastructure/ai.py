from collections.abc import Sequence
from openai import AsyncOpenAI

class OpenAICompatibleAI:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    async def chat(self, messages: Sequence[dict[str, str]], *, temperature: float = 0.8) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=list(messages),
            temperature=temperature,
        )
        return response.choices[0].message.content or "hmmm"
