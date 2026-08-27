"""
server/screen_streamer.py
=========================
Captures the teacher's screen and streams JPEG frames to student clients.

The :class:`TeacherScreenStreamer` grabs the primary monitor at a configurable
frame rate, compresses each frame as JPEG, and broadcasts it to a list of
target clients via the DLSlab protocol.

Usage example::

    streamer = TeacherScreenStreamer(server)
    streamer.start(client_ids=None)   # None → all connected clients
    ...
    streamer.stop()
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from typing import TYPE_CHECKING

import mss
from PIL import Image

from client.screen_capture import draw_cursor_on_image
from shared.messages import make_teacher_frame

if TYPE_CHECKING:
    from server.main_server import DLSlabServer

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

DEFAULT_FPS: int = 10                   # frames per second
DEFAULT_QUALITY: int = 60              # JPEG compression quality (0-100)
FRAME_WIDTH: int = 1280                # output width in pixels
FRAME_HEIGHT: int = 720                # output height in pixels

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Streamer class
# ---------------------------------------------------------------------------

class TeacherScreenStreamer:
    """Captures the teacher's screen and broadcasts frames to student clients.

    Args:
        server:  The running :class:`~server.main_server.DLSlabServer` instance
                 used to send messages to connected clients.
        fps:     Target frame rate (frames per second).  Defaults to
                 :data:`DEFAULT_FPS`.
        quality: JPEG compression quality (1–100).  Defaults to
                 :data:`DEFAULT_QUALITY`.
    """

    def __init__(
        self,
        server: "DLSlabServer",
        fps: int = DEFAULT_FPS,
        quality: int = DEFAULT_QUALITY,
    ) -> None:
        self._server = server
        self._fps = fps
        self._quality = quality
        self._running: bool = False
        self._task: asyncio.Task | None = None
        self._target_ids: list[str] | None = None  # None → all connected

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_streaming(self) -> bool:
        """``True`` while a streaming task is active."""
        return self._running and self._task is not None and not self._task.done()

    def start(
        self,
        client_ids: list[str] | None = None,
        fps: int | None = None,
        quality: int | None = None,
    ) -> None:
        """Start streaming the teacher's screen to the specified clients.

        If a streaming session is already active it is stopped first.

        Args:
            client_ids: Explicit list of client IDs to target.  Pass ``None``
                        to broadcast to **all** currently connected clients.
            fps:        Override the default frame rate for this session.
            quality:    Override the default JPEG quality for this session.
        """
        if self.is_streaming:
            self.stop()

        if fps is not None:
            self._fps = fps
        if quality is not None:
            self._quality = quality

        self._target_ids = client_ids
        self._running = True
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._stream_loop())
        logger.info(
            "TeacherScreenStreamer started — fps=%d quality=%d targets=%s",
            self._fps,
            self._quality,
            "all" if client_ids is None else len(client_ids),
        )

    def stop(self) -> None:
        """Stop the streaming loop and cancel the background task."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        logger.info("TeacherScreenStreamer stopped.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _capture_frame(self) -> str:
        """Capture the primary monitor and return a base64-encoded JPEG string.

        Returns:
            Base64-encoded JPEG string of the current screen, or an empty
            string if the capture fails.
        """
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[0]  # combined/primary monitor
                screenshot = sct.grab(monitor)
                img = Image.frombytes(
                    "RGB",
                    screenshot.size,
                    screenshot.bgra,
                    "raw",
                    "BGRX",
                )
                img = draw_cursor_on_image(img, monitor)
                img = img.resize((FRAME_WIDTH, FRAME_HEIGHT), Image.LANCZOS)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=self._quality)
                return base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception as exc:
            logger.warning("Screen capture failed: %s", exc)
            return ""

    async def _broadcast(self, frame_b64: str) -> None:
        """Send *frame_b64* to all target clients.

        Args:
            frame_b64: Base64-encoded JPEG string to broadcast.
        """
        if not frame_b64:
            return

        msg = make_teacher_frame("server", frame_b64)

        targets: list[str] = (
            list(self._server.clients.all_client_ids())
            if self._target_ids is None
            else self._target_ids
        )

        for cid in targets:
            await self._server.send_to_client(cid, msg)

    async def _stream_loop(self) -> None:
        """Main capture-and-broadcast loop; runs until :meth:`stop` is called."""
        interval = 1.0 / self._fps
        loop = asyncio.get_event_loop()

        while self._running:
            start = loop.time()

            # Capture is synchronous / CPU-bound; run in executor to avoid
            # blocking the event loop on slow hardware.
            frame_b64 = await loop.run_in_executor(None, self._capture_frame)
            await self._broadcast(frame_b64)

            elapsed = loop.time() - start
            await asyncio.sleep(max(0.0, interval - elapsed))
