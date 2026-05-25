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
import io
import logging
from typing import Optional

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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _capture_with_mss(self) -> str:
        """Use mss to grab the primary monitor and encode the thumbnail."""
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

        # Resize to thumbnail dimensions using high-quality Lanczos resampling.
        img = img.resize((self._width, self._height), Image.Resampling.LANCZOS)

        # Compress to JPEG in memory.
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=self._quality, optimize=True)
        jpeg_bytes = buffer.getvalue()

        # Encode as base64.
        return base64.b64encode(jpeg_bytes).decode("ascii")
