"""
client/agent.py Yan2
===============
DLSlab student agent — runs on each Windows PC in the lab.

The agent connects to the teacher's DLSlab server over TCP, registers itself,
and then:

1. Sends a compressed screenshot every ``SCREENSHOT_INTERVAL`` seconds.
2. Responds to PING messages with PONG.
3. Executes REMOTE_INPUT commands received from the server.
4. Executes COMMAND messages (e.g. ``shutdown``, ``open_url``).
5. Automatically reconnects with exponential back-off if the connection drops.

Run from the repository root::

    python -m client.agent --server-ip 192.168.1.100

Optional flags::

    --server-ip   IP address of the teacher's server  (default: 127.0.0.1)
    --server-port TCP port                            (default: 9000)
    --client-id   Override the auto-generated client ID
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import pathlib
import socket
import sys
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from client.app_enforcer import AppEnforcer
from client.blank_screen import BlankScreenOverlay
from client.input_handler import InputHandler
from client.power_manager import PowerManager
from client.screen_capture import ScreenCapture, CAPTURE_INTERVAL
from client.student_display import StudentDisplay
from client.teacher_display import TeacherDisplay
from client.web_enforcer import WebEnforcer
from server.protocol import read_message, write_message
from shared.messages import (
    Message,
    MessageType,
    make_ping,
    make_pong,
    make_policy_violation,
    make_register,
    make_screenshot,
)

# ---------------------------------------------------------------------------
# Qt signal bridge (cross-thread UI calls)
# ---------------------------------------------------------------------------

class _AgentSignals(QObject):
    """Señales para actualizar la UI de Qt de forma segura desde el hilo asyncio."""
    show_teacher   = pyqtSignal()
    hide_teacher   = pyqtSignal()
    update_teacher = pyqtSignal(str)
    show_student   = pyqtSignal(str)   # presenter_name
    hide_student   = pyqtSignal()
    update_student = pyqtSignal(str)
    show_blank     = pyqtSignal(str)   # texto del overlay
    hide_blank     = pyqtSignal()


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

DEFAULT_SERVER_HOST: str = "127.0.0.1"
DEFAULT_SERVER_PORT: int = 9000
PING_INTERVAL: float = 5.0          # seconds between heartbeat PINGs
RECONNECT_BASE_DELAY: float = 2.0   # initial back-off delay in seconds
RECONNECT_MAX_DELAY: float = 60.0   # maximum back-off delay in seconds
DEFAULT_POWER_DELAY_SECONDS: int = 5
MIN_POWER_DELAY_SECONDS: int = 3

# High-resolution capture constants (for Show Student presenter mode)
HIRES_WIDTH: int = 1280
HIRES_HEIGHT: int = 720
HIRES_QUALITY: int = 60
HIRES_FPS: int = 10
HIRES_INTERVAL: float = 1.0 / HIRES_FPS  # ~100 ms between hires frames

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
# Agent class
# ---------------------------------------------------------------------------

class DLSlabAgent:
    """Student agent that connects to the DLSlab teacher server.

    Args:
        server_host:  IP address or hostname of the teacher server.
        server_port:  TCP port the server is listening on.
        client_id:    Unique identifier for this agent.  Auto-generated from
                      the machine's hostname + UUID if not provided.
    """

    def __init__(
        self,
        server_host: str = DEFAULT_SERVER_HOST,
        server_port: int = DEFAULT_SERVER_PORT,
        client_id: Optional[str] = None,
    ) -> None:
        self.server_host = server_host
        self.server_port = server_port
        self.client_id: str = client_id or self._generate_client_id()
        self.hostname: str = socket.gethostname()

        self._screen_capture = ScreenCapture()
        self._input_handler: Optional[InputHandler] = None
        try:
            self._input_handler = InputHandler()
        except RuntimeError as exc:
            logger.warning("Remote input disabled: %s", exc)

        self._blank_screen = BlankScreenOverlay()
        self._teacher_display = TeacherDisplay()
        self._student_display = StudentDisplay()
        self._app_enforcer = AppEnforcer(on_violation=self._on_policy_violation)
        self._web_enforcer = WebEnforcer(
            app_enforcer=self._app_enforcer,
            on_violation=self._on_policy_violation,
        )

        # Puente de señales Qt — permite llamar a la UI desde el hilo asyncio
        # de forma thread-safe (Qt encola automáticamente al hilo principal).
        self._signals = _AgentSignals()
        self._signals.show_teacher.connect(self._teacher_display.show)
        self._signals.hide_teacher.connect(self._teacher_display.hide)
        self._signals.update_teacher.connect(self._teacher_display.update_frame)
        self._signals.show_student.connect(self._student_display.show)
        self._signals.hide_student.connect(self._student_display.hide)
        self._signals.update_student.connect(self._student_display.update_frame)
        self._signals.show_blank.connect(self._blank_screen.show)
        self._signals.hide_blank.connect(self._blank_screen.hide)

        self._writer: Optional[asyncio.StreamWriter] = None
        self._running: bool = False
        self._hires_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Connect to the server and run indefinitely, reconnecting on failure."""
        self._running = True
        self._loop = asyncio.get_running_loop()
        delay = RECONNECT_BASE_DELAY

        while self._running:
            try:
                await self._connect_and_run()
                delay = RECONNECT_BASE_DELAY  # reset on clean disconnect
            except (ConnectionRefusedError, OSError) as exc:
                logger.warning(
                    "Connection failed (%s). Retrying in %.0f s…", exc, delay
                )
            except Exception as exc:
                logger.exception("Unexpected error: %s. Retrying in %.0f s…", exc, delay)

            if not self._running:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def stop(self) -> None:
        """Signal the agent to stop and close the connection."""
        self._running = False
        self._web_enforcer.clear_web_policy()
        self._app_enforcer.clear_policy()
        self._app_enforcer.stop()
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def _connect_and_run(self) -> None:
        """Open one TCP connection and drive the agent loop until it drops."""
        logger.info(
            "Connecting to %s:%d as %s …",
            self.server_host,
            self.server_port,
            self.client_id,
        )
        reader, writer = await asyncio.open_connection(
            self.server_host, self.server_port,
            limit=50 * 1024 * 1024  # 50 MB — matches MAX_MESSAGE_BYTES
        )
        self._writer = writer
        logger.info("Connected.")

        try:
            # Send registration message immediately.
            await self._send_register(writer)

            # Run background tasks concurrently.
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._receive_loop(reader, writer))
                tg.create_task(self._screenshot_loop(writer))
                tg.create_task(self._ping_loop(writer))
        finally:
            self._cancel_hires_task()
            self._web_enforcer.clear_web_policy()
            self._app_enforcer.clear_policy()
            self._app_enforcer.stop()
            self._writer = None
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Outgoing message helpers
    # ------------------------------------------------------------------

    async def _send_register(self, writer: asyncio.StreamWriter) -> None:
        local_ip = self._get_local_ip()
        mac = PowerManager.get_mac_address()
        sw, sh = ScreenCapture.get_screen_size()
        msg = make_register(
            self.client_id, self.hostname, local_ip,
            mac=mac, screen_width=sw, screen_height=sh,
        )
        await write_message(writer, msg)
        logger.info(
            "Sent REGISTER (hostname=%s, ip=%s, mac=%s, screen=%dx%d)",
            self.hostname,
            local_ip,
            mac,
            sw,
            sh,
        )

    async def _screenshot_loop(self, writer: asyncio.StreamWriter) -> None:
        """Periodically capture and send screenshots."""
        while True:
            await asyncio.sleep(CAPTURE_INTERVAL)
            image_b64 = self._screen_capture.capture()
            if image_b64:
                msg = make_screenshot(self.client_id, image_b64)
                await write_message(writer, msg)
                logger.debug("Sent SCREENSHOT (%d chars)", len(image_b64))

    async def _ping_loop(self, writer: asyncio.StreamWriter) -> None:
        """Send a PING heartbeat every ``PING_INTERVAL`` seconds."""
        while True:
            await asyncio.sleep(PING_INTERVAL)
            msg = make_ping(self.client_id)
            await write_message(writer, msg)
            logger.debug("Sent PING")

    # ------------------------------------------------------------------
    # Incoming message handling
    # ------------------------------------------------------------------

    async def _receive_loop(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Read and dispatch messages from the server until connection closes."""
        while True:
            message = await read_message(reader)
            if message is None:
                logger.info("Server closed the connection.")
                break
            await self._dispatch(message, writer)

    async def _dispatch(
        self, message: Message, writer: asyncio.StreamWriter
    ) -> None:
        """Route an incoming server message to the appropriate handler.

        Args:
            message: Parsed message from the server.
            writer:  Writer used to send replies.
        """
        if message.type == MessageType.PING:
            await write_message(writer, make_pong(self.client_id))
            logger.debug("Replied PONG to server PING")

        elif message.type == MessageType.REMOTE_INPUT:
            self._handle_remote_input(message)

        elif message.type == MessageType.COMMAND:
            self._handle_command(message)

        elif message.type == MessageType.BLANK_SCREEN:
            self._handle_blank_screen(message)

        elif message.type == MessageType.UNBLANK_SCREEN:
            self._handle_unblank_screen(message)

        elif message.type == MessageType.START_SHOW_TEACHER:
            self._handle_start_show_teacher(message)

        elif message.type == MessageType.TEACHER_FRAME:
            self._handle_teacher_frame(message)

        elif message.type == MessageType.STOP_SHOW_TEACHER:
            self._handle_stop_show_teacher(message)

        elif message.type == MessageType.REQUEST_HIRES_SCREENSHOT:
            await self._handle_request_hires_screenshot(writer)

        elif message.type == MessageType.STOP_HIRES_SCREENSHOT:
            self._handle_stop_hires_screenshot()

        elif message.type == MessageType.START_SHOW_STUDENT:
            self._handle_start_show_student(message)

        elif message.type == MessageType.STUDENT_FRAME:
            self._handle_student_frame(message)

        elif message.type == MessageType.STOP_SHOW_STUDENT:
            self._handle_stop_show_student(message)

        elif message.type == MessageType.SET_APP_POLICY:
            self._handle_set_app_policy(message)

        elif message.type == MessageType.CLEAR_APP_POLICY:
            self._handle_clear_app_policy()

        elif message.type == MessageType.SET_WEB_POLICY:
            self._handle_set_web_policy(message)

        elif message.type == MessageType.CLEAR_WEB_POLICY:
            self._handle_clear_web_policy()

        elif message.type == MessageType.SHUTDOWN:
            await self._handle_shutdown(message, writer)

        elif message.type == MessageType.RESTART:
            self._handle_restart(message)

        elif message.type == MessageType.LOGOUT:
            self._handle_logout()

        elif message.type == MessageType.LOCK_WORKSTATION:
            self._handle_lock_workstation()

        elif message.type == MessageType.OPEN_URL:
            self._handle_open_url(message)

        elif message.type == MessageType.RUN_APP:
            self._handle_run_app(message)

        elif message.type == MessageType.SEND_FILE:
            self._handle_send_file(message)

        else:
            logger.debug("Ignored message type: %s", message.type)

    def _handle_remote_input(self, message: Message) -> None:
        """Execute a remote input event on the local desktop.

        Args:
            message: A REMOTE_INPUT message from the server.
        """
        if self._input_handler is None:
            logger.warning("Remote input received but InputHandler is disabled.")
            return

        event_type: str = message.payload.get("event_type", "")
        event_data: dict = message.payload.get("event_data", {})
        self._input_handler.handle_event(event_type, event_data)

    def _handle_command(self, message: Message) -> None:
        """Execute a server-issued command on this machine.

        Args:
            message: A COMMAND message from the server.
        """
        command: str = message.payload.get("command", "")
        args: dict = message.payload.get("args", {})

        logger.info("Received COMMAND: %s  args=%s", command, args)

        if command == "shutdown":
            logger.warning("Shutting down by server command.")
            PowerManager.shutdown(30)

        elif command == "restart":
            logger.warning("Restarting by server command.")
            PowerManager.restart(30)

        elif command == "open_url":
            url: str = args.get("url", "")
            if url:
                PowerManager.open_url(url)
                logger.info("Opened URL: %s", url)
            else:
                logger.warning("open_url command missing 'url' argument.")

        else:
            logger.warning("Unknown command: %s", command)

    def _handle_blank_screen(self, message: Message) -> None:
        text: str = message.payload.get("message", "Atención al frente")
        logger.info("BLANK_SCREEN received — showing overlay (text=%r).", text)
        self._signals.show_blank.emit(text)

    def _handle_unblank_screen(self, message: Message) -> None:
        logger.info("UNBLANK_SCREEN received — hiding overlay.")
        self._signals.hide_blank.emit()

    def _handle_start_show_teacher(self, message: Message) -> None:
        logger.info("START_SHOW_TEACHER received — showing teacher display.")
        self._signals.show_teacher.emit()

    def _handle_teacher_frame(self, message: Message) -> None:
        frame_b64: str = message.payload.get("frame", "")
        if frame_b64:
            self._signals.update_teacher.emit(frame_b64)

    def _handle_stop_show_teacher(self, message: Message) -> None:
        logger.info("STOP_SHOW_TEACHER received — hiding teacher display.")
        self._signals.hide_teacher.emit()

    async def _handle_request_hires_screenshot(
        self, writer: asyncio.StreamWriter
    ) -> None:
        """Start the high-resolution screenshot streaming loop.

        Cancels any pre-existing hires task before creating a new one.

        Args:
            writer: The active connection writer used to send frames.
        """
        self._cancel_hires_task()
        loop = asyncio.get_event_loop()
        self._hires_task = loop.create_task(self._hires_screenshot_loop(writer))
        logger.info(
            "REQUEST_HIRES_SCREENSHOT received — hires capture started (%d FPS).",
            HIRES_FPS,
        )

    def _handle_stop_hires_screenshot(self) -> None:
        """Cancel the high-resolution screenshot loop.

        Safe to call even when no hires task is active.
        """
        self._cancel_hires_task()
        logger.info("STOP_HIRES_SCREENSHOT received — hires capture stopped.")

    def _handle_start_show_student(self, message: Message) -> None:
        presenter_name: str = message.payload.get("presenter_name", "Alumno")
        logger.info(
            "START_SHOW_STUDENT received — showing student display for %r.",
            presenter_name,
        )
        self._signals.show_student.emit(presenter_name)

    def _handle_student_frame(self, message: Message) -> None:
        frame_b64: str = message.payload.get("frame", "")
        if frame_b64:
            self._signals.update_student.emit(frame_b64)

    def _handle_stop_show_student(self, message: Message) -> None:
        logger.info("STOP_SHOW_STUDENT received — hiding student display.")
        self._signals.hide_student.emit()

    def _handle_set_app_policy(self, message: Message) -> None:
        """Apply app whitelist/blacklist policy sent by the server."""
        mode: str = message.payload.get("mode", "")
        apps: list[str] = message.payload.get("apps", [])
        if mode == "whitelist":
            self._app_enforcer.set_whitelist(apps)
        elif mode == "blacklist":
            self._app_enforcer.set_blacklist(apps)
        else:
            logger.warning("SET_APP_POLICY ignored: unknown mode=%r", mode)
            return
        self._app_enforcer.start()
        logger.info("SET_APP_POLICY applied: mode=%s apps=%d", mode, len(apps))

    def _handle_clear_app_policy(self) -> None:
        """Clear app policy and stop app monitor."""
        self._app_enforcer.clear_policy()
        self._app_enforcer.stop()
        logger.info("CLEAR_APP_POLICY applied.")

    def _handle_set_web_policy(self, message: Message) -> None:
        """Apply web policy sent by the server."""
        mode: str = message.payload.get("mode", "")
        urls: list[str] = message.payload.get("urls", [])
        if mode == "block_all":
            self._web_enforcer.block_browsers()
        elif mode == "whitelist":
            self._web_enforcer.set_url_whitelist(urls)
        else:
            logger.warning("SET_WEB_POLICY ignored: unknown mode=%r", mode)
            return
        logger.info("SET_WEB_POLICY applied: mode=%s urls=%d", mode, len(urls))

    def _handle_clear_web_policy(self) -> None:
        """Clear web policy restrictions."""
        self._web_enforcer.clear_web_policy()
        logger.info("CLEAR_WEB_POLICY applied.")

    def _on_policy_violation(self, process_name: str, mode: str) -> None:
        """Send POLICY_VIOLATION asynchronously to the connected server."""
        writer = self._writer
        if writer is None or self._loop is None:
            return
        message = make_policy_violation(self.client_id, process_name, mode)
        self._loop.call_soon_threadsafe(
            lambda: asyncio.create_task(write_message(writer, message))
        )

    async def _handle_shutdown(
        self,
        message: Message,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Confirm shutdown reception and trigger delayed power-off."""
        requested_delay = self._parse_power_delay(message.payload.get("delay"))
        safe_delay = max(MIN_POWER_DELAY_SECONDS, requested_delay)
        await write_message(writer, make_pong(self.client_id))
        logger.warning("SHUTDOWN received — powering off in %d seconds.", safe_delay)
        PowerManager.shutdown(safe_delay)

    def _handle_restart(self, message: Message) -> None:
        """Trigger delayed restart."""
        requested_delay = self._parse_power_delay(message.payload.get("delay"))
        safe_delay = max(MIN_POWER_DELAY_SECONDS, requested_delay)
        logger.warning("RESTART received — restarting in %d seconds.", safe_delay)
        PowerManager.restart(safe_delay)

    def _handle_logout(self) -> None:
        """Log out current user session."""
        logger.warning("LOGOUT received.")
        PowerManager.logout()

    def _handle_lock_workstation(self) -> None:
        """Lock workstation session."""
        logger.info("LOCK_WORKSTATION received.")
        PowerManager.lock_workstation()

    def _handle_open_url(self, message: Message) -> None:
        """Open a URL in the default browser."""
        url = str(message.payload.get("url", "")).strip()
        if not url:
            logger.warning("OPEN_URL ignored: missing url.")
            return
        PowerManager.open_url(url)
        logger.info("OPEN_URL executed: %s", url)

    def _handle_run_app(self, message: Message) -> None:
        """Launch an application with optional arguments."""
        path = str(message.payload.get("path", "")).strip()
        raw_args = message.payload.get("args", [])
        args = [str(arg) for arg in raw_args] if isinstance(raw_args, list) else []
        if not path:
            logger.warning("RUN_APP ignored: missing path.")
            return
        PowerManager.run_app(path, args=args)
        logger.info("RUN_APP executed: %s %s", path, args)

    def _handle_send_file(self, message: Message) -> None:
        """Receive a file from the server and save it to the Windows Desktop.

        Args:
            message: A SEND_FILE message containing ``filename`` and base64
                     ``data`` fields in the payload.
        """
        filename: str = message.payload.get("filename", "documento")
        data_b64: str = message.payload.get("data", "")
        if not data_b64:
            logger.warning("SEND_FILE received with empty data — ignored.")
            return

        # Sanitize: strip any directory components to prevent path traversal.
        safe_name = pathlib.Path(filename).name or "documento"

        # Resolve the Windows Desktop path, honouring OneDrive redirection.
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            ) as key:
                desktop = pathlib.Path(winreg.QueryValueEx(key, "Desktop")[0])
        except Exception:
            desktop = pathlib.Path.home() / "Desktop"

        try:
            desktop.mkdir(parents=True, exist_ok=True)
            dest = desktop / safe_name
            data = base64.b64decode(data_b64)
            dest.write_bytes(data)
            logger.info("SEND_FILE saved — %s (%d bytes)", dest, len(data))
        except Exception as exc:
            logger.error("SEND_FILE failed to save %r: %s", safe_name, exc)

    @staticmethod
    def _parse_power_delay(value: object) -> int:
        """Parse a requested power delay and return a safe integer."""
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return DEFAULT_POWER_DELAY_SECONDS

    async def _hires_screenshot_loop(self, writer: asyncio.StreamWriter) -> None:
        """Capture and transmit high-resolution frames at :data:`HIRES_FPS`.

        Each frame is sent as a ``SCREENSHOT`` message with ``hires=True`` in
        the payload so the server can distinguish it from normal thumbnails.

        Args:
            writer: The active connection writer used to send frames.
        """
        hires_capture = ScreenCapture(
            width=HIRES_WIDTH, height=HIRES_HEIGHT, quality=HIRES_QUALITY
        )
        while True:
            await asyncio.sleep(HIRES_INTERVAL)
            image_b64 = hires_capture.capture()
            if image_b64:
                msg = Message(
                    type=MessageType.SCREENSHOT,
                    client_id=self.client_id,
                    payload={"image": image_b64, "hires": True},
                )
                try:
                    await write_message(writer, msg)
                except (ConnectionResetError, BrokenPipeError, OSError):
                    logger.warning("Hires capture: connection lost, stopping loop.")
                    break
                logger.debug("Sent hires SCREENSHOT (%d chars)", len(image_b64))

    def _cancel_hires_task(self) -> None:
        """Cancel the hires screenshot task if it is currently running."""
        if self._hires_task and not self._hires_task.done():
            self._hires_task.cancel()
        self._hires_task = None

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_client_id() -> str:
        """Generate a stable client ID based on hostname + MAC address.

        The ID is derived from the machine's MAC address so it remains
        consistent across agent and server restarts, preventing duplicate
        thumbnails in the teacher console.

        Returns:
            A string in the form ``"<hostname>-<last6hex_of_MAC>"``.
            Falls back to a hostname-based MD5 suffix if no MAC is available.
        """
        import hashlib
        host = socket.gethostname()
        mac = PowerManager.get_mac_address()
        if mac:
            # Use the last 6 hex chars of the MAC (e.g. "AA:BB:CC:DD:EE:FF" → "DDEEFF")
            suffix = mac.replace(":", "").replace("-", "")[-6:].upper()
        else:
            # Stable fallback: deterministic hash of the hostname
            suffix = hashlib.md5(host.encode()).hexdigest()[:6].upper()
        return f"{host}-{suffix}"

    @staticmethod
    def _get_local_ip() -> str:
        """Determine the local machine's primary IP address.

        Returns:
            The local IP as a dotted-decimal string, or ``"127.0.0.1"`` if
            it cannot be determined.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DLSlab student agent — connects to the teacher server."
    )
    parser.add_argument(
        "--server-ip",
        default=DEFAULT_SERVER_HOST,
        help=f"Teacher server IP address (default: {DEFAULT_SERVER_HOST})",
    )
    parser.add_argument(
        "--server-port",
        type=int,
        default=DEFAULT_SERVER_PORT,
        help=f"Teacher server TCP port (default: {DEFAULT_SERVER_PORT})",
    )
    parser.add_argument(
        "--client-id",
        default=None,
        help="Override the auto-generated client ID.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    agent = DLSlabAgent(
        server_host=args.server_ip,
        server_port=args.server_port,
        client_id=args.client_id,
    )
    try:
        await agent.run()
    except KeyboardInterrupt:
        await agent.stop()


def main() -> None:
    """Entry point: Qt en hilo principal, asyncio en hilo secundario."""
    import threading
    from PyQt6.QtWidgets import QApplication

    # QApplication y DLSlabAgent DEBEN crearse en el hilo principal de Qt
    # para que los QObject y sus señales pertenezcan al hilo correcto.
    # Así Qt usará QueuedConnection automáticamente al emitir desde asyncio.
    app = QApplication(sys.argv)
    args = _parse_args()
    agent = DLSlabAgent(
        server_host=args.server_ip,
        server_port=args.server_port,
        client_id=args.client_id,
    )

    # asyncio corre en un hilo secundario, usando el agente ya creado
    def _run_asyncio() -> None:
        asyncio.run(agent.run())

    asyncio_thread = threading.Thread(
        target=_run_asyncio, daemon=True, name="dlslab-asyncio"
    )
    asyncio_thread.start()

    # El hilo principal queda bloqueado en el event loop de Qt
    app.exec()


if __name__ == "__main__":
    main()
