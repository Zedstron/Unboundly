import asyncio
from pathlib import Path
from fastapi import FastAPI
from app.core.config import settings
from app.core.logger import get_logger
from app.core.lifecycle import AppContext
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from app.web.routes import router as web_router
from app.api.routes import router as api_router

logger = get_logger(__name__)

context = AppContext()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await context.startup()
    app.state.context = context

    try:
        yield
    finally:
        await context.shutdown()

async def NoLocalServer():
    await context.startup()

    try:
        await asyncio.Event().wait()
    finally:
        await context.shutdown()

directory = Path(__file__).resolve().parent.parent
directory = directory / "ui" / "assets"

if settings.identity_mode in ( "local", "both" ):
    app = FastAPI(title="Persona Chat", lifespan=lifespan)
    app.mount("/assets", StaticFiles(directory=directory), name="assets")

    app.include_router(web_router)
    app.include_router(api_router, prefix="/api")
else:
    # No local UI serving, only bridge will be active
    asyncio.run(NoLocalServer())
