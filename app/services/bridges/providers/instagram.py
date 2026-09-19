from __future__ import annotations

import inspect
import os
import threading
from concurrent.futures import Future
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import janus
from instagrapi import Client

from app.services.bridges.base import SocialBridge
from app.services.bridges.future import AwaitableFuture
from app.services.bridges.models import Operation, SocialMessage
from app.core.logger import get_logger


logger = get_logger("instabridge")


class Command:
    def __init__(self, session: str, operation: Operation, args: tuple[Any, ...], kwargs: dict[str, Any], future: Future):
        self.session = session
        self.operation = operation
        self.args = args
        self.kwargs = kwargs
        self.future = future


class InstagramBridge(SocialBridge):
    def __init__(self):
        self.session = os.getenv("INSTABRIDGE_SESSION", "instabridge")

        self.username = os.getenv("INSTABRIDGE_USERNAME")
        self.password = os.getenv("INSTABRIDGE_PASSWORD")

        self.session_file = os.getenv("INSTABRIDGE_SESSION_FILE", os.path.join(os.getcwd(), "instagram_session.json"))
        self.queue_size = int(os.getenv("INSTABRIDGE_QUEUE_SIZE", "1000"))

        self._queue: janus.Queue[Command] = janus.Queue(maxsize=self.queue_size)

        self._thread = threading.Thread(
            target=self._run,
            name="instabridge",
            daemon=True,
        )

        self._started = threading.Event()
        self._ready = threading.Event()
        self._stopped = threading.Event()

        self._startup_error: BaseException | None = None

        self._client: Client | None = None
        self._realtime = None

        self._handlers: list[Callable[[str, dict[str, Any]], Any]] = []

        self._handlers_lock = threading.RLock()

        self._thread.start()
        self._started.wait()

    def signal(self, session: str, operation: Operation, *args: Any, **kwargs: Any) -> AwaitableFuture:
        if self._stopped.is_set():
            raise RuntimeError("instabridge is stopped")

        future = Future()

        command = Command(
            session=session,
            operation=operation,
            args=args,
            kwargs=kwargs,
            future=future,
        )

        try:
            self._queue.sync_q.put(command, block=True)
        except BaseException as exc:
            future.set_exception(exc)

        return AwaitableFuture(future)

    def on_message(self, handler: Callable[[str, dict[str, Any]], Any]) -> Callable[[], None]:
        if not callable(handler):
            raise TypeError("handler must be callable")

        with self._handlers_lock:
            self._handlers.append(handler)

        return lambda: self._remove_handler(handler)

    def _remove_handler(
        self,
        handler: Callable[[str, dict[str, Any]], Any],
    ) -> None:
        with self._handlers_lock:
            if handler in self._handlers:
                self._handlers.remove(handler)

    def _run(self) -> None:
        try:
            self._loop = __import__("asyncio").new_event_loop()
            __import__("asyncio").set_event_loop(self._loop)

            self._started.set()

            self._loop.run_until_complete(
                self._bootstrap()
            )

            self._loop.run_until_complete(
                self._worker()
            )

        except BaseException as exc:
            self._startup_error = exc
            self._started.set()
            self._ready.set()

            logger.exception("instabridge stopped unexpectedly")

        finally:
            self._stopped.set()

            try:
                self._loop.close()
            except Exception:
                pass

    def _create_client(self) -> Client:
        client = Client(public_transport="requests", public_transport_impersonate="chrome136")
        client.set_app()
        return client

    def _is_upgrade_error(self, exc: BaseException) -> bool:
        error_type = str(getattr(exc, "error_type", "") or "").lower()
        message = str(exc).lower()
        return "needs_upgrade" in error_type or "needs_upgrade" in message

    def _clear_stale_session(self) -> None:
        session_path = Path(self.session_file)
        if not session_path.exists():
            return

        try:
            session_path.unlink()
        except OSError:
            logger.warning("failed to clear stale Instagram session file: %s", session_path, exc_info=True)

    async def _bootstrap(self) -> None:
        if not self.username:
            raise RuntimeError("INSTABRIDGE_USERNAME is not configured")

        if not self.password:
            raise RuntimeError("INSTABRIDGE_PASSWORD is not configured")

        client = self._create_client()

        if Path(self.session_file).exists():
            client.load_settings(self.session_file, override_app_version=True)

        try:
            client.login(self.username, self.password)
        except BaseException as exc:
            if not self._is_upgrade_error(exc):
                raise

            logger.warning(
                "Instagram reported a stale or outdated app profile; clearing cached session and retrying with a fresh app configuration. Error: %s",
                exc,
            )
            self._clear_stale_session()

            client = self._create_client()
            client.login(self.username, self.password)

        client.dump_settings(self.session_file)

        client.realtime_on("message", self._on_message)

        realtime = client.realtime_connect()
        realtime.direct_subscribe()

        self._client = client
        self._realtime = realtime

        self._ready.set()

        logger.info("Instagram bridge ready")

    async def _worker(self) -> None:
        while True:
            command = await self._queue.async_q.get()

            try:
                if command.operation == Operation.STOP_SERVICE:
                    command.future.set_result(None)
                    return

                if not self._ready.is_set():
                    await self._wait_ready()

                result = await self._execute(command)

                if not command.future.done():
                    command.future.set_result(result)

            except BaseException as exc:
                if not command.future.done():
                    command.future.set_exception(exc)

                logger.exception(
                    "instabridge operation failed: %s",
                    command.operation,
                )

            finally:
                self._queue.async_q.task_done()

    async def _wait_ready(self) -> None:
        while not self._ready.is_set():
            if self._startup_error is not None:
                raise RuntimeError("instabridge failed to initialize") from self._startup_error

            await __import__("asyncio").sleep(0.1)

    async def _execute(self, command: Command) -> Any:
        operation = command.operation
        kwargs = command.kwargs

        if operation == Operation.SEND_MESSAGE:
            return await self._send_message(**kwargs)

        if operation == Operation.MARK_SEEN:
            return await self._mark_seen(**kwargs)

        if operation == Operation.SET_ONLINE:
            return await self._set_online(**kwargs)

        if operation == Operation.SEND_ATTACHMENT:
            return await self._send_attachment(**kwargs)

        if operation == Operation.SEND_REACTION:
            return await self._send_reaction(**kwargs)

        raise ValueError(f"unknown operation: {operation}")

    async def _send_message(self, *, to: str | int, text: str) -> Any:
        client = self._require_client()
        return await self._call(client.direct_send, text, thread_ids=[int(to)])

    async def _mark_seen(self, *, chat_id: str | int, message_id: str | int | None = None) -> Any:
        realtime = self._require_realtime()

        if message_id is None:
            raise ValueError("Instagram mark_seen requires message_id")

        return await self._call(
            realtime.direct_mark_seen,
            int(chat_id),
            int(message_id),
        )

    async def _set_online(self, *, online: bool = True) -> Any:
        realtime = self._require_realtime()

        return await self._call(
            realtime.direct_indicate_activity,
            is_active=online,
        )

    async def _send_attachment(
        self,
        *,
        to: str | int,
        path: str,
        caption: str = "",
        filename: str | None = None,
    ) -> Any:
        client = self._require_client()

        file_path = Path(path)

        if not file_path.exists():
            raise FileNotFoundError(str(file_path))

        suffix = file_path.suffix.lower()

        thread_ids = [int(to)]

        if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            return await self._call(
                client.direct_send_photo,
                file_path,
                thread_ids=thread_ids,
            )

        if suffix in {".mp4", ".mov"}:
            return await self._call(
                client.direct_send_video,
                file_path,
                thread_ids=thread_ids,
            )

        if suffix in {".m4a", ".aac"}:
            return await self._call(
                client.direct_send_voice,
                file_path,
                thread_ids=thread_ids,
                waveform=None,
            )

        raise ValueError(
            f"unsupported Instagram attachment: {suffix}"
        )

    async def _send_reaction(
        self,
        *,
        message_id: str | int,
        reaction: str | bool,
        chat_id: str | int,
    ) -> Any:
        realtime = self._require_realtime()

        if reaction is False:
            return await self._call(
                self._require_client().direct_delete_reaction,
                int(chat_id),
                int(message_id),
            )

        return await self._call(
            realtime.direct_send_reaction,
            int(chat_id),
            int(message_id),
            emoji=str(reaction),
        )

    async def _call(
        self,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = function(
            *args,
            **kwargs,
        )

        if inspect.isawaitable(result):
            return await result

        return result

    def _normalize_message(self, message: dict[str, Any]) -> SocialMessage:
        print(message)
        return SocialMessage(
            provider="instagram",
            session=self.session,
            message_id=str(
                message.get("message_id", "")
            ),
            message_type="message",
            chat_id=str(
                message.get("thread_id", "")
            ),
            sender_id=str(
                message.get("user_id", "")
            ),
            sender_name=message.get("username"),
            text=message.get("text", ""),
            timestamp=self._parse_timestamp(
                message.get("timestamp")
            ),
            raw=message,
        )

    def _on_message(self, message: dict[str, Any]) -> None:
        event = self._normalize_message(message)
        self._dispatch_message(self.session, event)

    def _dispatch_message(self, session: str, message: SocialMessage) -> None:
        with self._handlers_lock:
            handlers = tuple(self._handlers)

        for handler in handlers:
            try:
                result = handler(session, message)

                if inspect.isawaitable(result):
                    import asyncio

                    asyncio.run_coroutine_threadsafe(
                        self._run_handler(result),
                        self._loop,
                    )

            except BaseException:
                logger.exception("message handler failed")

    async def _run_handler(self, result: Awaitable[Any]) -> None:
        try:
            await result
        except BaseException:
            logger.exception("async message handler failed")

    def _require_client(self) -> Client:
        if self._client is None:
            raise RuntimeError("Instagram client is not ready")

        return self._client

    def _require_realtime(self):
        if self._realtime is None:
            raise RuntimeError("Instagram realtime client is not ready")

        return self._realtime

    def close(self) -> None:
        if self._stopped.is_set():
            return

        future = Future()

        self._queue.sync_q.put(
            Command(
                session=self.session,
                operation=Operation.STOP_SERVICE,
                args=(),
                kwargs={},
                future=future,
            )
        )

        try:
            future.result(timeout=10)
        except Exception:
            pass

        self._stopped.set()

        if self._realtime is not None:
            try:
                self._realtime.disconnect()
            except Exception:
                logger.exception("failed to disconnect Instagram realtime")