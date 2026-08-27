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
import ctypes
import ctypes.wintypes
import io
import logging
from typing import TYPE_CHECKING

import mss
from PIL import Image

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
# Cursor overlay helpers (Windows only)
# ---------------------------------------------------------------------------

def _get_cursor_pos() -> tuple[int, int] | None:
    """Return the current cursor position in screen coordinates, or None."""
    try:
        pt = ctypes.wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return pt.x, pt.y
    except Exception:
        pass
    return None


def _draw_cursor_on_image(img: Image.Image, monitor: dict) -> Image.Image:
    """Overlay the current Windows cursor onto *img* (RGBA).

    The image coordinates are adjusted by the monitor origin so multi-monitor
    setups are handled correctly.

    Args:
        img:     PIL Image (RGB or RGBA) representing the captured monitor.
        monitor: The mss monitor dict used for the capture (contains ``left``
                 and ``top`` fields).

    Returns:
        The same image with the cursor composited in, or the original image
        if the cursor cannot be retrieved.
    """
    pos = _get_cursor_pos()
    if pos is None:
        return img

    cx, cy = pos
    # Convert from absolute screen coords to monitor-relative coords
    cx -= monitor.get("left", 0)
    cy -= monitor.get("top", 0)

    if cx < 0 or cy < 0 or cx >= img.width or cy >= img.height:
        return img

    try:
        # CURSORINFO structure
        class CURSORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.wintypes.DWORD),
                ("flags", ctypes.wintypes.DWORD),
                ("hCursor", ctypes.wintypes.HANDLE),
                ("ptScreenPos", ctypes.wintypes.POINT),
            ]

        ci = CURSORINFO()
        ci.cbSize = ctypes.sizeof(CURSORINFO)
        if not ctypes.windll.user32.GetCursorInfo(ctypes.byref(ci)):
            return img

        CURSOR_SHOWING = 0x00000001
        if not (ci.flags & CURSOR_SHOWING):
            return img

        # ICONINFO structure
        class ICONINFO(ctypes.Structure):
            _fields_ = [
                ("fIcon", ctypes.wintypes.BOOL),
                ("xHotspot", ctypes.wintypes.DWORD),
                ("yHotspot", ctypes.wintypes.DWORD),
                ("hbmMask", ctypes.wintypes.HANDLE),
                ("hbmColor", ctypes.wintypes.HANDLE),
            ]

        ii = ICONINFO()
        if not ctypes.windll.user32.GetIconInfo(ci.hCursor, ctypes.byref(ii)):
            return img

        hotspot_x = ii.xHotspot
        hotspot_y = ii.yHotspot

        # DrawIconEx draws the cursor onto a device context; we use a
        # temporary memory DC via win32 GDI to extract a PIL image.
        try:
            import win32ui
            import win32gui
            import win32con

            screen_dc = win32gui.GetDC(0)
            mem_dc = win32ui.CreateDCFromHandle(screen_dc)
            save_dc = mem_dc.CreateCompatibleDC()

            cursor_size = 32  # standard cursor size
            save_bitmap = win32ui.CreateBitmap()
            save_bitmap.CreateCompatibleBitmap(mem_dc, cursor_size, cursor_size)
            save_dc.SelectObject(save_bitmap)
            save_dc.FillSolidRect((0, 0, cursor_size, cursor_size), 0x00000000)

            ctypes.windll.user32.DrawIconEx(
                save_dc.GetSafeHdc(),
                0, 0,
                ci.hCursor,
                cursor_size, cursor_size,
                0, None,
                0x0003,  # DI_NORMAL
            )

            bmp_info = save_bitmap.GetInfo()
            bmp_str = save_bitmap.GetBitmapBits(True)
            cursor_img = Image.frombuffer(
                "RGBA",
                (bmp_info["bmWidth"], bmp_info["bmHeight"]),
                bmp_str,
                "raw",
                "BGRA",
                0, 1,
            )

            save_dc.DeleteDC()
            mem_dc.DeleteDC()
            win32gui.ReleaseDC(0, screen_dc)
            win32gui.DeleteObject(save_bitmap.GetHandle())
            if ii.hbmMask:
                win32gui.DeleteObject(ii.hbmMask)
            if ii.hbmColor:
                win32gui.DeleteObject(ii.hbmColor)

            # Paste cursor at its hotspot-adjusted position
            paste_x = cx - hotspot_x
            paste_y = cy - hotspot_y
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            img.paste(cursor_img, (paste_x, paste_y), cursor_img)
            return img

        except ImportError:
            # pywin32 not available — fall back to a simple cross-hair pointer
            pass
        finally:
            try:
                if ii.hbmMask:
                    ctypes.windll.gdi32.DeleteObject(ii.hbmMask)
                if ii.hbmColor:
                    ctypes.windll.gdi32.DeleteObject(ii.hbmColor)
            except Exception:
                pass

    except Exception:
        pass

    # Fallback: draw a small black arrow (filled triangle) using PIL
    from PIL import ImageDraw
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    draw = ImageDraw.Draw(img)
    # Simple arrow: filled triangle 12×16 px
    arrow = [
        (cx, cy),
        (cx, cy + 14),
        (cx + 4, cy + 10),
        (cx + 8, cy + 16),
        (cx + 10, cy + 15),
        (cx + 6, cy + 9),
        (cx + 11, cy + 9),
    ]
    draw.polygon(arrow, fill=(0, 0, 0, 220))
    return img


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
                img = _draw_cursor_on_image(img, monitor)
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
