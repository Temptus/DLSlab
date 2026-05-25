"""
server/main_server.py
=====================
DLSlab asyncio TCP server — teacher / professor console backend.

Run with::

    python -m server.main_server

The server listens on TCP port 9000 (configurable via ``SERVER_PORT``) and
accepts connections from student agents.  Each connected agent can:

- **REGISTER** — announce hostname and IP.
- **SCREENSHOT** — send a base64-encoded JPEG thumbnail every few seconds.
- **PING** — send a heartbeat (server replies with PONG).

The server can push messages back to individual clients or broadcast to all.

Architecture note
-----------------
A single asyncio event loop handles all I/O.  The :class:`ClientManager`
keeps track of who is connected.  The :mod:`server.protocol` module handles
framing (newline-delimited JSON).
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Callable

from server.client_manager import ClientManager
from server.protocol import read_message, write_message
from shared.messages import (
    Message,
    MessageType,
    make_blank_screen,
    make_pong,
    make_unblank_screen,
)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

SERVER_HOST: str = "0.0.0.0"
SERVER_PORT: int = 9000
HEARTBEAT_TIMEOUT: float = 30.0   # seconds before a silent client is dropped

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Screenshot callback type
# ---------------------------------------------------------------------------

# Signature: (client_id: str, image_b64: str) -> None
ScreenshotCallback = Callable[[str, str], None]


# ---------------------------------------------------------------------------
# Server class
# ---------------------------------------------------------------------------

class DLSlabServer:
    """Central asyncio TCP server for DLSlab.

    Args:
        host: Interface to bind to (default: ``"0.0.0.0"``).
        port: TCP port to listen on (default: ``9000``).
        on_screenshot: Optional callback invoked whenever a SCREENSHOT message
                       arrives.  Signature: ``(client_id, image_b64) -> None``.
    """

    def __init__(
        self,
        host: str = SERVER_HOST,
        port: int = SERVER_PORT,
        on_screenshot: ScreenshotCallback | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._on_screenshot = on_screenshot
        self.clients = ClientManager()
        self._server: asyncio.AbstractServer | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start listening for incoming client connections."""
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.host,
            port=self.port,
        )
        addr = self._server.sockets[0].getsockname()
        logger.info("DLSlab server listening on %s:%s", addr[0], addr[1])
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Gracefully shut down the server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("DLSlab server stopped.")

    async def send_to_client(self, client_id: str, message: Message) -> bool:
        """Send *message* to a specific client.

        Args:
            client_id: Target client identifier.
            message:   Message to deliver.

        Returns:
            ``True`` if the message was sent, ``False`` if the client is
            unknown or disconnected.
        """
        client = self.clients.get(client_id)
        if client is None or not client.is_connected:
            logger.warning("send_to_client: client %s not available.", client_id)
            return False
        try:
            await write_message(client.writer, message)
            return True
        except (ConnectionResetError, BrokenPipeError, OSError) as exc:
            logger.warning("Failed to send to %s: %s", client_id, exc)
            return False

    async def blank_screen(
        self,
        client_ids: list[str] | None,
        message: str = "Atención al frente",
    ) -> None:
        """Send a BLANK_SCREEN command to one or more connected clients.

        Args:
            client_ids: List of client identifiers to target.  Pass ``None``
                        to broadcast to **all** currently connected clients.
            message:    Text that will be displayed on the student's screen.
        """
        msg = make_blank_screen("server", message)
        targets = (
            list(self.clients.all_client_ids())
            if client_ids is None
            else client_ids
        )
        for cid in targets:
            await self.send_to_client(cid, msg)
        logger.info(
            "blank_screen sent to %d client(s) — message=%r", len(targets), message
        )

    async def unblank_screen(self, client_ids: list[str] | None) -> None:
        """Send an UNBLANK_SCREEN command to one or more connected clients.

        Args:
            client_ids: List of client identifiers to target.  Pass ``None``
                        to broadcast to **all** currently connected clients.
        """
        msg = make_unblank_screen("server")
        targets = (
            list(self.clients.all_client_ids())
            if client_ids is None
            else client_ids
        )
        for cid in targets:
            await self.send_to_client(cid, msg)
        logger.info("unblank_screen sent to %d client(s).", len(targets))

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle the full lifecycle of a single client connection.

        This coroutine is spawned by asyncio for every new TCP connection.
        """
        peer = writer.get_extra_info("peername", ("unknown", 0))
        peer_str = f"{peer[0]}:{peer[1]}"
        logger.info("New connection from %s", peer_str)

        client_id: str | None = None

        try:
            while True:
                message = await asyncio.wait_for(
                    read_message(reader),
                    timeout=HEARTBEAT_TIMEOUT,
                )
                if message is None:
                    logger.info("Connection closed by %s", peer_str)
                    break

                client_id = client_id or message.client_id
                await self._dispatch(message, writer, peer[0])

        except asyncio.TimeoutError:
            logger.warning(
                "Client %s timed out (no message for %.0fs).",
                peer_str,
                HEARTBEAT_TIMEOUT,
            )
        except (ConnectionResetError, BrokenPipeError):
            logger.info("Client %s disconnected unexpectedly.", peer_str)
        finally:
            if client_id:
                self.clients.remove(client_id)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _dispatch(
        self,
        message: Message,
        writer: asyncio.StreamWriter,
        peer_ip: str,
    ) -> None:
        """Route an incoming message to the appropriate handler.

        Args:
            message:  Parsed message from the client.
            writer:   Writer to use for replies.
            peer_ip:  IP address of the remote peer (from TCP layer).
        """
        self.clients.touch(message.client_id)

        handlers = {
            MessageType.REGISTER: self._handle_register,
            MessageType.SCREENSHOT: self._handle_screenshot,
            MessageType.PING: self._handle_ping,
        }

        handler = handlers.get(message.type)
        if handler:
            await handler(message, writer, peer_ip)
        else:
            logger.debug("Unhandled message type: %s", message.type)

    async def _handle_register(
        self,
        message: Message,
        writer: asyncio.StreamWriter,
        peer_ip: str,
    ) -> None:
        hostname = message.payload.get("hostname", message.client_id)
        ip = message.payload.get("ip", peer_ip)
        self.clients.register(message.client_id, hostname, ip, writer)
        logger.info(
            "REGISTER  client_id=%s  hostname=%s  ip=%s",
            message.client_id,
            hostname,
            ip,
        )

    async def _handle_screenshot(
        self,
        message: Message,
        writer: asyncio.StreamWriter,
        peer_ip: str,
    ) -> None:
        image_b64: str = message.payload.get("image", "")
        logger.debug(
            "SCREENSHOT from %s (%d bytes base64)",
            message.client_id,
            len(image_b64),
        )
        if self._on_screenshot:
            self._on_screenshot(message.client_id, image_b64)

    async def _handle_ping(
        self,
        message: Message,
        writer: asyncio.StreamWriter,
        peer_ip: str,
    ) -> None:
        pong = make_pong("server")
        await write_message(writer, pong)
        logger.debug("PING/PONG with %s", message.client_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main() -> None:
    server = DLSlabServer()

    loop = asyncio.get_running_loop()

    def _shutdown(*_: object) -> None:
        logger.info("Shutdown signal received.")
        loop.call_soon_threadsafe(loop.stop)

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGTERM, _shutdown)
        loop.add_signal_handler(signal.SIGINT, _shutdown)

    await server.start()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Server terminated by user.")
