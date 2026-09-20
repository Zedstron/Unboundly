from __future__ import annotations

import os
import asyncio, inspect
from typing import Callable
from app.core.config import settings
from app.core.logger import get_logger
from app.services.bridges.models import SocialMessage
from app.services.bridges.base import SocialBridge, MessageHandler
from app.services.bridges.providers.instagram import InstagramBridge
from app.services.bridges.providers.whatsapp import WhatsAppBridge

logger = get_logger(__name__)

class BridgeRegistry:
    def __init__(self):
        self._bridges: dict[str, SocialBridge] = {}
        self._handlers: list[MessageHandler] = []
        self.enabled = settings.identity_mode in ( "bridge", "both" )
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _get_loop(self) -> asyncio.AbstractEventLoop | None:
        if self._loop and self._loop.is_running():
            return self._loop

        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def register(self, name: str, bridge: SocialBridge) -> None:
        if name in self._bridges:
            raise ValueError(f"bridge already registered: {name}")

        self._bridges[name] = bridge
        bridge.on_message(self.__dispatch_message)

    def on_message(self, handler: MessageHandler) -> Callable[[], None]:
        self._handlers.append(handler)

        def unsubscribe():
            if handler in self._handlers:
                self._handlers.remove(handler)

        return unsubscribe

    def __dispatch_message(self, message: SocialMessage) -> None:
        loop = self._get_loop()

        for handler in tuple(self._handlers):
            try:
                if inspect.iscoroutinefunction(handler):
                    if loop and loop.is_running():
                        asyncio.run_coroutine_threadsafe(handler(message), loop)
                    else:
                        logger.error("Cannot execute async handler: No running event loop available in BridgeRegistry.")
                else:
                    handler(message)
            except BaseException:
                logger.exception("social message handler failed")

    def get(self, name: str) -> SocialBridge:
        try:
            return self._bridges[name]
        except KeyError:
            raise KeyError(f"unknown bridge: {name}") from None

    def close(self) -> None:
        for bridge in self._bridges.values():
            bridge.close()

registry = BridgeRegistry()

if registry.enabled:
    if os.getenv("WPPBRIDGE_ENABLED") == "1":
        registry.register("whatsapp", WhatsAppBridge())

    if os.getenv("INSTABRIDGE_ENABLED") == "1":
        registry.register("instagram", InstagramBridge())