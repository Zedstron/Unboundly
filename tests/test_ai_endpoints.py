import asyncio
from app.core.config import settings
from app.infrastructure.ai import AIProvider

async def main():
    provider = AIProvider(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        settings.embedding_model,
        settings.embedding_base_url,
        settings.embedding_api_key
    )

    vectors = await provider.embed("hi there")
    print("First 5 Vectors", vectors[0:5], "Length", len(vectors))

if __name__ == "__main__":
    asyncio.run(main())