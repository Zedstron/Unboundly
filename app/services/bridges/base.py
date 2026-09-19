from __future__ import annotations

from typing import Any, Awaitable
from abc import ABC, abstractmethod
from collections.abc import Callable
from app.services.bridges.models import SocialMessage, Operation

MessageHandler = Callable[ [SocialMessage], Any | Awaitable[Any] ]

class AwaitableResult:
    def result(self, timeout: float | None = None) -> Any:
        raise NotImplementedError

    def done(self) -> bool:
        raise NotImplementedError

    def __await__(self):
        raise NotImplementedError


class SocialBridge(ABC):
    @abstractmethod
    def signal(self, session: str, operation: Operation, *args: Any, **kwargs: Any) -> AwaitableResult:
        raise NotImplementedError

    @abstractmethod
    def on_message(self, handler: MessageHandler) -> Callable[[], None]:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError