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
from server.screen_streamer import TeacherScreenStreamer
from server.student_streamer import StudentStreamer
from server.wol_manager import WolManager
from shared.messages import (
    Message,
    MessageType,
    make_blank_screen,
    make_lock_workstation,
    make_logout,
    make_open_url,
    make_pong,
    make_request_hires_screenshot,
    make_restart,
    make_run_app,
    make_shutdown,
    make_start_show_student,
    make_start_show_teacher,
    make_stop_hires_screenshot,
    make_stop_show_student,
    make_stop_show_teacher,
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
# Signature: (client_id: str, process_name: str, mode: str) -> None
PolicyViolationCallback = Callable[[str, str, str], None]


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
        on_policy_violation: PolicyViolationCallback | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._on_screenshot = on_screenshot
        self._on_policy_violation = on_policy_violation
        self.clients = ClientManager()
        self.wol_manager = WolManager(self.clients)
        self._server: asyncio.AbstractServer | None = None
        self._streamer = TeacherScreenStreamer(self)
        self._student_streamer = StudentStreamer(self)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start listening for incoming client connections."""
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.host,
            port=self.port,
            limit=10 * 1024 * 1024  # 10 MB maximum buffer size for StreamReader (matches MAX_MESSAGE_BYTES)
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

    async def start_show_teacher(
        self,
        client_ids: list[str] | None,
        fps: int | None = None,
        quality: int | None = None,
    ) -> None:
        """Begin broadcasting the teacher's screen to one or more clients.

        Sends a ``START_SHOW_TEACHER`` notification so clients can prepare
        their display, then starts the :class:`~server.screen_streamer.TeacherScreenStreamer`.

        Args:
            client_ids: List of client identifiers to target.  Pass ``None``
                        to broadcast to **all** currently connected clients.
            fps:        Optional frame-rate override for this session.
            quality:    Optional JPEG quality override for this session.
        """
        targets = (
            list(self.clients.all_client_ids())
            if client_ids is None
            else client_ids
        )
        msg = make_start_show_teacher("server")
        for cid in targets:
            await self.send_to_client(cid, msg)

        self._streamer.start(client_ids=client_ids, fps=fps, quality=quality)
        logger.info(
            "start_show_teacher — targets=%s fps=%s quality=%s",
            "all" if client_ids is None else len(targets),
            fps,
            quality,
        )

    async def stop_show_teacher(self, client_ids: list[str] | None) -> None:
        """Stop broadcasting the teacher's screen and notify clients.

        Stops the :class:`~server.screen_streamer.TeacherScreenStreamer`, then
        sends a ``STOP_SHOW_TEACHER`` notification to the relevant clients.

        Args:
            client_ids: List of client identifiers to notify.  Pass ``None``
                        to notify **all** currently connected clients.
        """
        self._streamer.stop()

        targets = (
            list(self.clients.all_client_ids())
            if client_ids is None
            else client_ids
        )
        msg = make_stop_show_teacher("server")
        for cid in targets:
            await self.send_to_client(cid, msg)
        logger.info("stop_show_teacher sent to %d client(s).", len(targets))

    async def start_show_student(
        self,
        presenter_id: str,
        audience_ids: list[str] | None,
    ) -> None:
        """Begin a Show Student session for the given presenter.

        Sends ``REQUEST_HIRES_SCREENSHOT`` to the presenter so it starts
        streaming high-resolution frames, and sends ``START_SHOW_STUDENT`` to
        the audience so they open the :class:`~client.student_display.StudentDisplay`.

        If a Show Student session is already active it is stopped first.

        Args:
            presenter_id: Client ID of the student who will present.
            audience_ids: Explicit list of audience client IDs.  Pass ``None``
                          to target **all** currently connected clients except
                          the presenter.
        """
        if self._student_streamer.is_streaming:
            await self.stop_show_student()

        # Resolve audience list before starting.
        all_ids = list(self.clients.all_client_ids())
        resolved_audience: list[str] = (
            [cid for cid in all_ids if cid != presenter_id]
            if audience_ids is None
            else audience_ids
        )

        # Look up presenter hostname for the banner text.
        presenter_info = self.clients.get(presenter_id)
        presenter_name: str = (
            presenter_info.hostname if presenter_info else presenter_id
        )

        # Tell the presenter to start sending hires frames.
        await self.send_to_client(
            presenter_id, make_request_hires_screenshot("server")
        )

        # Tell the audience to open the student display.
        msg = make_start_show_student("server", presenter_name, presenter_id)
        for cid in resolved_audience:
            await self.send_to_client(cid, msg)

        # Activate the relay streamer.
        self._student_streamer.start(
            presenter_id=presenter_id,
            audience_ids=resolved_audience,
        )
        logger.info(
            "start_show_student — presenter=%s name=%r audience=%d client(s)",
            presenter_id,
            presenter_name,
            len(resolved_audience),
        )

    async def stop_show_student(self) -> None:
        """Stop the active Show Student session and notify all parties.

        Sends ``STOP_HIRES_SCREENSHOT`` to the presenter and
        ``STOP_SHOW_STUDENT`` to the audience, then deactivates the streamer.

        Safe to call even when no session is active.
        """
        if not self._student_streamer.is_streaming:
            return

        presenter_id = self._student_streamer.presenter_id
        audience_ids: list[str] = (
            [
                cid
                for cid in self.clients.all_client_ids()
                if cid != presenter_id
            ]
            if self._student_streamer._audience_ids is None  # noqa: SLF001
            else list(self._student_streamer._audience_ids)  # noqa: SLF001
        )

        # Stop the relay before sending messages so no stale frames are forwarded.
        self._student_streamer.stop()

        # Tell the presenter to stop sending hires frames.
        if presenter_id:
            await self.send_to_client(
                presenter_id, make_stop_hires_screenshot("server")
            )

        # Tell the audience to close their student displays.
        msg = make_stop_show_student("server")
        for cid in audience_ids:
            await self.send_to_client(cid, msg)

        logger.info(
            "stop_show_student — notified presenter=%s and %d audience client(s).",
            presenter_id,
            len(audience_ids),
        )

    async def set_app_policy(
        self,
        client_ids: list[str] | None,
        mode: str,
        apps: list[str],
    ) -> None:
        """Send SET_APP_POLICY to target clients."""
        msg = Message(
            type=MessageType.SET_APP_POLICY,
            client_id="server",
            payload={"mode": mode, "apps": apps},
        )
        targets = list(self.clients.all_client_ids()) if client_ids is None else client_ids
        for cid in targets:
            await self.send_to_client(cid, msg)

    async def clear_app_policy(self, client_ids: list[str] | None) -> None:
        """Send CLEAR_APP_POLICY to target clients."""
        msg = Message(type=MessageType.CLEAR_APP_POLICY, client_id="server", payload={})
        targets = list(self.clients.all_client_ids()) if client_ids is None else client_ids
        for cid in targets:
            await self.send_to_client(cid, msg)

    async def set_web_policy(
        self,
        client_ids: list[str] | None,
        mode: str,
        urls: list[str],
    ) -> None:
        """Send SET_WEB_POLICY to target clients."""
        msg = Message(
            type=MessageType.SET_WEB_POLICY,
            client_id="server",
            payload={"mode": mode, "urls": urls},
        )
        targets = list(self.clients.all_client_ids()) if client_ids is None else client_ids
        for cid in targets:
            await self.send_to_client(cid, msg)

    async def clear_web_policy(self, client_ids: list[str] | None) -> None:
        """Send CLEAR_WEB_POLICY to target clients."""
        msg = Message(type=MessageType.CLEAR_WEB_POLICY, client_id="server", payload={})
        targets = list(self.clients.all_client_ids()) if client_ids is None else client_ids
        for cid in targets:
            await self.send_to_client(cid, msg)

    async def shutdown(
        self,
        client_ids: list[str] | None,
        delay: int = 5,
    ) -> None:
        """Send SHUTDOWN to target clients."""
        safe_delay = self._sanitize_power_delay(delay)
        msg = make_shutdown("server", safe_delay)
        targets = self._resolve_targets(client_ids)
        for cid in targets:
            await self.send_to_client(cid, msg)
        logger.warning("shutdown sent to %d client(s) delay=%d", len(targets), safe_delay)

    async def restart(
        self,
        client_ids: list[str] | None,
        delay: int = 5,
    ) -> None:
        """Send RESTART to target clients."""
        safe_delay = self._sanitize_power_delay(delay)
        msg = make_restart("server", safe_delay)
        targets = self._resolve_targets(client_ids)
        for cid in targets:
            await self.send_to_client(cid, msg)
        logger.warning("restart sent to %d client(s) delay=%d", len(targets), safe_delay)

    async def logout(self, client_ids: list[str] | None) -> None:
        """Send LOGOUT to target clients."""
        msg = make_logout("server")
        targets = self._resolve_targets(client_ids)
        for cid in targets:
            await self.send_to_client(cid, msg)

    async def lock_workstation(self, client_ids: list[str] | None) -> None:
        """Send LOCK_WORKSTATION to target clients."""
        msg = make_lock_workstation("server")
        targets = self._resolve_targets(client_ids)
        for cid in targets:
            await self.send_to_client(cid, msg)

    async def open_url(self, client_ids: list[str] | None, url: str) -> None:
        """Send OPEN_URL to target clients."""
        msg = make_open_url("server", url)
        targets = self._resolve_targets(client_ids)
        for cid in targets:
            await self.send_to_client(cid, msg)

    async def run_app(
        self,
        client_ids: list[str] | None,
        path: str,
        args: list[str],
    ) -> None:
        """Send RUN_APP to target clients."""
        msg = make_run_app("server", path, args=args)
        targets = self._resolve_targets(client_ids)
        for cid in targets:
            await self.send_to_client(cid, msg)

    async def wake_on_lan(self, client_ids: list[str] | None) -> dict[str, bool]:
        """Wake clients via Wake-on-LAN."""
        if client_ids is None:
            return self.wol_manager.wake_all()
        results: dict[str, bool] = {}
        for client_id in client_ids:
            results[client_id] = self.wol_manager.wake(client_id)
        return results

    def _resolve_targets(self, client_ids: list[str] | None) -> list[str]:
        """Resolve target client IDs from optional explicit list."""
        return list(self.clients.all_client_ids()) if client_ids is None else client_ids

    @staticmethod
    def _sanitize_power_delay(delay: int) -> int:
        """Normalize shutdown/restart delay and enforce minimum confirmation window."""
        try:
            return max(3, int(delay))
        except (TypeError, ValueError):
            return 5

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
                # If the disconnecting client is the active presenter, stop the
                # show-student session so the audience windows close automatically.
                if (
                    self._student_streamer.is_streaming
                    and self._student_streamer.presenter_id == client_id
                ):
                    logger.info(
                        "Presenter %s disconnected — stopping show-student session.",
                        client_id,
                    )
                    await self.stop_show_student()
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
            MessageType.POLICY_VIOLATION: self._handle_policy_violation,
            MessageType.CLIENT_MAC: self._handle_client_mac,
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
        mac = str(message.payload.get("mac", "")).strip()
        self.clients.register(message.client_id, hostname, ip, mac, writer)
        if mac:
            self.wol_manager.register_mac(message.client_id, mac)
        logger.info(
            "REGISTER  client_id=%s  hostname=%s  ip=%s  mac=%s",
            message.client_id,
            hostname,
            ip,
            mac or "unknown",
        )

    async def _handle_client_mac(
        self,
        message: Message,
        writer: asyncio.StreamWriter,
        peer_ip: str,
    ) -> None:
        """Handle standalone CLIENT_MAC updates from clients."""
        del writer, peer_ip
        mac = str(message.payload.get("mac", "")).strip()
        if not mac:
            return
        client = self.clients.get(message.client_id)
        if client:
            client.mac = mac
        self.wol_manager.register_mac(message.client_id, mac)

    async def _handle_screenshot(
        self,
        message: Message,
        writer: asyncio.StreamWriter,
        peer_ip: str,
    ) -> None:
        image_b64: str = message.payload.get("image", "")
        is_hires: bool = bool(message.payload.get("hires", False))

        if is_hires:
            # High-resolution frame from the presenter — relay to audience.
            if (
                self._student_streamer.is_streaming
                and message.client_id == self._student_streamer.presenter_id
            ):
                await self._student_streamer.relay_frame(image_b64)
        else:
            # Normal thumbnail — pass to the UI callback.
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

    async def _handle_policy_violation(
        self,
        message: Message,
        writer: asyncio.StreamWriter,
        peer_ip: str,
    ) -> None:
        """Handle POLICY_VIOLATION reports from clients."""
        process_name = message.payload.get("process_name", "")
        mode = message.payload.get("mode", "")
        logger.warning(
            "POLICY_VIOLATION from %s: process=%r mode=%r",
            message.client_id,
            process_name,
            mode,
        )
        if self._on_policy_violation:
            self._on_policy_violation(message.client_id, process_name, mode)


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
