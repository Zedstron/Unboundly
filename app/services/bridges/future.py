from __future__ import annotations

import asyncio
from concurrent.futures import Future
from typing import Any


class AwaitableFuture:
    def __init__(self, future: Future):
        self._future = future

    def result(self, timeout: float | None = None) -> Any:
        return self._future.result(timeout)

    def done(self) -> bool:
        return self._future.done()

    def __await__(self):
        async def wait():
            loop = asyncio.get_running_loop()
            return await asyncio.wrap_future(
                self._future,
                loop=loop,
            )

        return wait().__await__()