"""
client/screen_capture.py
========================
Captures the primary display, resizes the image to thumbnail dimensions,
compresses it as JPEG, and returns the result encoded in base64.

Designed for **Windows 10/11** but works on any platform supported by ``mss``
and ``Pillow``.

Usage example::

    from client.screen_capture import ScreenCapture

    cap = ScreenCapture()
    b64_image = cap.capture()   # -> str
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import io
import logging
from typing import Optional, Tuple

try:
    import mss
    import mss.tools
    _MSS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _MSS_AVAILABLE = False

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PIL_AVAILABLE = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cursor overlay helpers (Windows only)
# ---------------------------------------------------------------------------

def get_cursor_pos() -> tuple[int, int] | None:
    """Return the current cursor position in screen coordinates, or None."""
    try:
        pt = ctypes.wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return pt.x, pt.y
    except Exception:
        pass
    return None


def draw_cursor_on_image(img: "Image.Image", monitor: dict) -> "Image.Image":
    """Overlay the current Windows cursor onto *img* (RGBA).

    Args:
        img:     PIL Image (RGB or RGBA) representing the captured monitor.
        monitor: The mss monitor dict used for the capture (contains ``left``
                 and ``top`` fields).

    Returns:
        The same image with the cursor composited in, or the original image
        if the cursor cannot be retrieved.
    """
    pos = get_cursor_pos()
    if pos is None:
        return img

    cx, cy = pos
    cx -= monitor.get("left", 0)
    cy -= monitor.get("top", 0)

    if cx < 0 or cy < 0 or cx >= img.width or cy >= img.height:
        return img

    try:
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

        try:
            import win32ui
            import win32gui
            import win32con  # noqa: F401

            screen_dc = win32gui.GetDC(0)
            mem_dc = win32ui.CreateDCFromHandle(screen_dc)
            save_dc = mem_dc.CreateCompatibleDC()

            cursor_size = 32
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

            paste_x = cx - hotspot_x
            paste_y = cy - hotspot_y
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            img.paste(cursor_img, (paste_x, paste_y), cursor_img)
            return img

        except ImportError:
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

    # Fallback: simple arrow drawn with PIL
    from PIL import ImageDraw
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    draw = ImageDraw.Draw(img)
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
# Configuration constants
# ---------------------------------------------------------------------------

THUMBNAIL_WIDTH: int = 320
THUMBNAIL_HEIGHT: int = 180
JPEG_QUALITY: int = 40          # 0-95; lower = smaller file, lower quality
CAPTURE_INTERVAL: float = 2.0   # seconds between automatic captures


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class ScreenCapture:
    """Captures and encodes screenshots for transmission to the server.

    Args:
        width:    Width of the output thumbnail in pixels.
        height:   Height of the output thumbnail in pixels.
        quality:  JPEG compression quality (0–95).
    """

    def __init__(
        self,
        width: int = THUMBNAIL_WIDTH,
        height: int = THUMBNAIL_HEIGHT,
        quality: int = JPEG_QUALITY,
    ) -> None:
        self._width = width
        self._height = height
        self._quality = quality

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def capture(self) -> Optional[str]:
        """Capture the primary monitor and return a base64-encoded JPEG string.

        Returns:
            A base64-encoded JPEG string, or ``None`` if a dependency is
            missing or the capture fails.
        """
        if not _MSS_AVAILABLE or not _PIL_AVAILABLE:
            logger.error(
                "screen_capture: 'mss' and 'Pillow' are required. "
                "Install them with: pip install mss Pillow"
            )
            return None

        try:
            return self._capture_with_mss()
        except Exception as exc:
            logger.exception("Screen capture failed: %s", exc)
            return None

    @staticmethod
    def get_screen_size() -> Tuple[int, int]:
        """Return ``(width, height)`` of the primary monitor.

        Falls back to ``(1920, 1080)`` if ``mss`` is unavailable or the
        query fails.

        Returns:
            A tuple ``(width, height)`` in pixels.
        """
        if not _MSS_AVAILABLE:
            return (1920, 1080)
        try:
            with mss.mss() as sct:
                m = sct.monitors[1]
                return (m["width"], m["height"])
        except Exception:
            return (1920, 1080)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def capture_with_cursor(self) -> Optional[str]:
        """Capture the primary monitor with the mouse cursor overlaid.

        Identical to :meth:`capture` but composites the live Windows cursor
        onto the frame before encoding.  Use this when presenting to other
        clients so they can see the presenter's pointer.

        Returns:
            A base64-encoded JPEG string, or ``None`` if the capture fails.
        """
        if not _MSS_AVAILABLE or not _PIL_AVAILABLE:
            return None
        try:
            return self._capture_with_mss(include_cursor=True)
        except Exception as exc:
            logger.exception("Screen capture (with cursor) failed: %s", exc)
            return None

    def _capture_with_mss(self, include_cursor: bool = False) -> str:
        """Use mss to grab the primary monitor and encode the thumbnail.

        Args:
            include_cursor: When ``True`` the Windows cursor is composited
                            onto the image before encoding.
        """
        with mss.mss() as sct:
            # Grab the first monitor (index 1; index 0 is the virtual screen).
            monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)

        # Convert raw mss data to a Pillow Image.
        img: Image.Image = Image.frombytes(
            "RGB",
            (screenshot.width, screenshot.height),
            screenshot.rgb,
        )

        if include_cursor:
            img = draw_cursor_on_image(img, monitor)

        # Resize to thumbnail dimensions using high-quality Lanczos resampling.
        if img.mode != "RGB":
            img = img.convert("RGB")
        img = img.resize((self._width, self._height), Image.Resampling.LANCZOS)

        # Compress to JPEG in memory.
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=self._quality, optimize=True)
        jpeg_bytes = buffer.getvalue()

        # Encode as base64.
        return base64.b64encode(jpeg_bytes).decode("ascii")


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

def capture_frame(
    width: int = THUMBNAIL_WIDTH,
    height: int = THUMBNAIL_HEIGHT,
    quality: int = JPEG_QUALITY,
) -> Optional[str]:
    """Capture the primary monitor with the specified resolution and quality.

    Convenience wrapper around :class:`ScreenCapture` that creates a
    temporary instance for a single-shot capture.

    Args:
        width:   Width of the output image in pixels.
        height:  Height of the output image in pixels.
        quality: JPEG compression quality (0–95).

    Returns:
        A base64-encoded JPEG string, or ``None`` if the capture fails.
    """
    return ScreenCapture(width=width, height=height, quality=quality).capture()
