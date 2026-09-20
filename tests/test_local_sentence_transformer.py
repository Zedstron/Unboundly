import asyncio
from dotenv import load_dotenv
from app.infrastructure.ai import AIProvider

load_dotenv()

async def main():
    ai = AIProvider("url", "key", "model")
    result = await ai.embed("hello world")
    print(result)

if __name__ == "__main__":
    asyncio.run(main())
