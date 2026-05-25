"""
client/agent.py
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
import logging
import os
import socket
import sys
import uuid
import webbrowser
from typing import Optional

from client.app_enforcer import AppEnforcer
from client.blank_screen import BlankScreenOverlay
from client.input_handler import InputHandler
from client.screen_capture import ScreenCapture, CAPTURE_INTERVAL
from client.student_display import StudentDisplay
from client.teacher_display import TeacherDisplay
from client.web_enforcer import WebEnforcer
from server.protocol import read_message, write_message
from shared.messages import (
    Message,
    MessageType,
    make_ping,
    make_policy_violation,
    make_register,
    make_screenshot,
)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

DEFAULT_SERVER_HOST: str = "127.0.0.1"
DEFAULT_SERVER_PORT: int = 9000
PING_INTERVAL: float = 5.0          # seconds between heartbeat PINGs
RECONNECT_BASE_DELAY: float = 2.0   # initial back-off delay in seconds
RECONNECT_MAX_DELAY: float = 60.0   # maximum back-off delay in seconds

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
            self.server_host, self.server_port
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
        msg = make_register(self.client_id, self.hostname, local_ip)
        await write_message(writer, msg)
        logger.info("Sent REGISTER (hostname=%s, ip=%s)", self.hostname, local_ip)

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
            from shared.messages import make_pong
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
            os.system("shutdown /s /t 30")  # Windows: shutdown in 30 s

        elif command == "restart":
            logger.warning("Restarting by server command.")
            os.system("shutdown /r /t 30")  # Windows: restart in 30 s

        elif command == "open_url":
            url: str = args.get("url", "")
            if url:
                webbrowser.open(url)
                logger.info("Opened URL: %s", url)
            else:
                logger.warning("open_url command missing 'url' argument.")

        else:
            logger.warning("Unknown command: %s", command)

    def _handle_blank_screen(self, message: Message) -> None:
        """Activate the blank-screen overlay on this machine.

        Extracts the ``message`` key from the payload (defaulting to
        ``"Atención al frente"``) and calls
        :meth:`~client.blank_screen.BlankScreenOverlay.show`.

        Args:
            message: A BLANK_SCREEN message from the server.
        """
        text: str = message.payload.get("message", "Atención al frente")
        logger.info("BLANK_SCREEN received — showing overlay (text=%r).", text)
        self._blank_screen.show(text)

    def _handle_unblank_screen(self, message: Message) -> None:
        """Deactivate the blank-screen overlay on this machine.

        Args:
            message: An UNBLANK_SCREEN message from the server.
        """
        logger.info("UNBLANK_SCREEN received — hiding overlay.")
        self._blank_screen.hide()

    def _handle_start_show_teacher(self, message: Message) -> None:
        """Open the teacher-display fullscreen window.

        Args:
            message: A START_SHOW_TEACHER message from the server.
        """
        logger.info("START_SHOW_TEACHER received — showing teacher display.")
        self._teacher_display.show()

    def _handle_teacher_frame(self, message: Message) -> None:
        """Render an incoming teacher screen frame.

        Args:
            message: A TEACHER_FRAME message carrying a base64-encoded JPEG.
        """
        frame_b64: str = message.payload.get("frame", "")
        if frame_b64:
            self._teacher_display.update_frame(frame_b64)

    def _handle_stop_show_teacher(self, message: Message) -> None:
        """Close the teacher-display window.

        Args:
            message: A STOP_SHOW_TEACHER message from the server.
        """
        logger.info("STOP_SHOW_TEACHER received — hiding teacher display.")
        self._teacher_display.hide()

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
        """Open the student-display fullscreen window.

        Args:
            message: A START_SHOW_STUDENT message with ``presenter_name`` and
                     ``presenter_id`` in the payload.
        """
        presenter_name: str = message.payload.get("presenter_name", "Alumno")
        logger.info(
            "START_SHOW_STUDENT received — showing student display for %r.",
            presenter_name,
        )
        self._student_display.show(presenter_name)

    def _handle_student_frame(self, message: Message) -> None:
        """Render an incoming student screen frame.

        Args:
            message: A STUDENT_FRAME message carrying a base64-encoded JPEG.
        """
        frame_b64: str = message.payload.get("frame", "")
        if frame_b64:
            self._student_display.update_frame(frame_b64)

    def _handle_stop_show_student(self, message: Message) -> None:
        """Close the student-display window.

        Args:
            message: A STOP_SHOW_STUDENT message from the server.
        """
        logger.info("STOP_SHOW_STUDENT received — hiding student display.")
        self._student_display.hide()

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
        """Generate a unique client ID based on hostname + UUID.

        Returns:
            A string in the form ``"<hostname>-<short-uuid>"``.
        """
        host = socket.gethostname()
        short_uuid = str(uuid.uuid4())[:8]
        return f"{host}-{short_uuid}"

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


if __name__ == "__main__":
    asyncio.run(_main())
