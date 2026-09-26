from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import asyncio
import inspect
import os
import threading
from datetime import datetime, timezone
from concurrent.futures import Future
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import janus
from WPP_Whatsapp import Create

from app.services.bridges.base import SocialBridge
from app.services.bridges.future import AwaitableFuture
from app.services.bridges.models import Operation, SocialMessage

from app.core.logger import get_logger

logger = get_logger("wppbridge")


class _SafeCreate(Create):
    def _onStateChange(self, state):
        self.state = state

        if state == "CONNECTED":
            self.logger.info("Ready ....")
        elif state in {"browserClose", "serverClose"}:
            self.state = "CLOSED"
            self.client = None
            self.logger.info("client.close - session.state: CLOSED")

        if self.onStateChange:
            self.onStateChange(state)

def _print_code(**payload: Any) -> None:
    print(payload)

def _print_qr(**payload: Any) -> None:
    ascii_qr = payload.get("asciiQR") or payload.get("qrCode")
    attempt = payload.get("attempt", "?")

    if ascii_qr:
        print(
            f"\n[WPPConnect] Scan this QR code with WhatsApp "
            f"(attempt {attempt}):\n{ascii_qr}\n",
            flush=True,
        )
    else:
        logger.warning("WPPConnect generated a QR code without terminal data (attempt %s)", attempt)


@dataclass(slots=True)
class Command:
    session: str
    operation: Operation
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    future: Future


class WhatsAppBridge(SocialBridge):
    def __init__(self):
        self.session = os.getenv("WPPBRIDGE_SESSION", "wppbridge")
        self.token_dir = os.getenv("WPPBRIDGE_TOKEN_DIR") or os.path.join(os.getcwd(), "tokens")
        self.queue_size = int(os.getenv("WPPBRIDGE_QUEUE_SIZE", "1000"))
        self.headless = os.getenv("WPPBRIDGE_HEADLESS", "0") == "1"
        self.phoneNumber = os.getenv("WPPBRIDGE_NUMBER", None)

        self._queue: janus.Queue[Command] = janus.Queue(
            maxsize=self.queue_size
        )
        self._thread = threading.Thread(
            target=self._run,
            name="wppbridge",
            daemon=True,
        )
        self._started = threading.Event()
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._startup_error: BaseException | None = None

        self._client = None
        self._handlers: list[Callable[[str, dict[str, Any]], Any]] = []
        self._handlers_lock = threading.RLock()

        self._thread.start()
        self._started.wait()

    def signal(self, session, operation: Operation, *args: Any, **kwargs: Any) -> AwaitableFuture:
        if self._stopped.is_set():
            raise RuntimeError("wppbridge is stopped")

        future: Future = Future()

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

    def _remove_handler(self, handler: Callable[[dict[str, Any]], Any]) -> None:
        with self._handlers_lock:
            if handler in self._handlers:
                self._handlers.remove(handler)

    def _run(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._started.set()
            self._loop.run_until_complete(self._bootstrap())
            self._loop.run_until_complete(self._worker())
        except BaseException as exc:
            self._startup_error = exc
            self._started.set()
            self._ready.set()
            logger.exception("wppbridge stopped unexpectedly")
        finally:
            self._stopped.set()
            try:
                self._loop.close()
            except Exception:
                pass

    async def _bootstrap(self) -> None:
        Path(self.token_dir).mkdir(parents=True, exist_ok=True)

        creator = _SafeCreate(
            session=self.session,
            folderNameToken=self.token_dir,
            phoneNumber=self.phoneNumber,
            catchQR=_print_qr,
            catchLinkCode=_print_code,
            waitForLogin=True,
            logQR=True,
            headless=self.headless,
            no_viewport=True,
            bypass_csp=True,
            install=False,
            browser="chrome"
        )

        self._creator = creator
        client = await creator.start_()

        if client is None:
            raise RuntimeError("WPP_Whatsapp failed to create client")

        self._client = client

        client.onMessage(self._on_message)

        self._ready.set()

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

                logger.exception("wppbridge operation failed: %s", command.operation)
            finally:
                self._queue.async_q.task_done()

    async def _wait_ready(self) -> None:
        while not self._ready.is_set():
            if self._startup_error is not None:
                raise RuntimeError("wppbridge failed to initialize") from self._startup_error

            await asyncio.sleep(0.1)

    async def _execute(self, command: Command) -> Any:
        operation = command.operation
        args = command.args
        kwargs = command.kwargs

        if operation == Operation.SEND_MESSAGE:
            return await self._call(
                self._client.sendText,
                kwargs["to"],
                kwargs["text"],
                kwargs.get("options"),
            )

        if operation == Operation.MARK_SEEN:
            return await self._call(
                self._client.sendSeen,
                kwargs["chat_id"],
            )

        if operation == Operation.SET_ONLINE:
            return await self._call(
                self._client.setOnlinePresence,
                kwargs.get("online", True),
            )

        if operation == Operation.SEND_ATTACHMENT:
            return await self._send_attachment(**kwargs)

        if operation == Operation.SEND_REACTION:
            return await self._send_reaction(**kwargs)

        raise ValueError(f"unknown operation: {operation}")

    async def _send_attachment(
        self,
        *,
        to: str,
        path: str,
        caption: str = "",
        filename: str | None = None,
    ) -> Any:
        file_path = Path(path)

        if not file_path.exists():
            raise FileNotFoundError(str(file_path))

        name = filename or file_path.name

        return await self._call(
            self._client.sendFile,
            to,
            str(file_path),
            name,
            caption,
        )

    async def _send_reaction(
        self,
        *,
        message_id: str | int,
        reaction: str | bool,
        chat_id: str | int | None = None,
    ) -> Any:
        async def operation():
            return await self._client.ThreadsafeBrowser.page_evaluate(
                """({ messageId, reaction }) =>
                    WPP.chat.sendReactionToMessage(messageId, reaction)
                """,
                {
                    "messageId": message_id,
                    "reaction": reaction,
                },
                page=self._client.page,
            )

        return await self._call(operation)

    async def _call(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        result = function(*args, **kwargs)

        if inspect.isawaitable(result):
            return await result

        return result

    def _normalize_message(self, message: dict[str, Any]) -> SocialMessage | None:
        if not message:
            return None

        message_id = message.get("id")
        sender_id = message.get("author") or message.get("from")

        if not message_id or not sender_id:
            return None

        if message.get("fromMe"):
            return None

        return SocialMessage(
            provider="whatsapp",
            session=self.session,
            message_id=str(message_id),
            message_type=str(message.get("type") or "chat"),
            item_id=str(message.get("itemId") or message.get("mediaKey") or message_id),
            chat_id=str(message.get("from") or ""),
            is_disappearing=bool(message.get("isEphemeral") or message.get("isDisappearing")),
            sender_id=str(sender_id),
            sender_name=message.get("notifyName"),
            text=str(message.get("body") or message.get("caption") or ""),
            timestamp=self._parse_timestamp(message.get("timestamp") or message.get("t")),
            raw=message,
        )

    def _parse_timestamp(self, value: Any) -> datetime | None:
        if value is None:
            return None

        if isinstance(value, datetime):
            return value

        if isinstance(value, (int, float)):
            value = float(value)

            # Detect timestamp precision by magnitude
            if value > 1e14:
                value /= 1_000_000
            elif value > 1e11:
                value /= 1_000

            return datetime.fromtimestamp(value, tz=timezone.utc)

        return None

    def _on_message(self, message: dict[str, Any]) -> None:
        if message:
            event = self._normalize_message(message)
            if event:
                self._dispatch_message(event)

    def _dispatch_message(self, message: SocialMessage) -> None:
        with self._handlers_lock:
            handlers = tuple(self._handlers)

        for handler in handlers:
            try:
                result = handler(message)

                if inspect.isawaitable(result):
                    asyncio.create_task(self._run_handler(result))
            except BaseException:
                logger.exception("message handler failed")

    async def _run_handler(self, result: Awaitable[Any]) -> None:
        try:
            await result
        except BaseException:
            logger.exception("async message handler failed")

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