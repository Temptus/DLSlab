"""
client/teacher_display.py
=========================
PyQt6 fullscreen window that renders the teacher's live screen on the student's
display.

Unlike :class:`~client.blank_screen.BlankScreenOverlay`, this window does **not**
block keyboard or mouse input — the student can still interact with the desktop
behind the overlay.  The window always stays on top so the teacher's content
remains visible.

Usage::

    display = TeacherDisplay()
    display.show()
    display.update_frame(frame_b64)   # call repeatedly as frames arrive
    display.hide()
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

BACKGROUND_COLOR: str = "#000000"


# ---------------------------------------------------------------------------
# TeacherDisplay class
# ---------------------------------------------------------------------------

class TeacherDisplay:
    """Fullscreen, always-on-top window that displays the teacher's screen.

    The window is created lazily on the first call to :meth:`show` and reused
    for subsequent frames.  It covers all screens attached to the client.

    Notes:
        * Input events (keyboard, mouse) **pass through** — this differs from
          :class:`~client.blank_screen.BlankScreenOverlay` which suppresses all input.
        * If a blank-screen overlay is also active, the OS will stack it on top
          because it is shown *after* this window.
    """

    def __init__(self) -> None:
        self._window: Optional[_FullscreenLabel] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(self) -> None:
        """Open (or raise) the fullscreen teacher-display window."""
        if self._window is None:
            self._window = _FullscreenLabel()
        self._window.show_fullscreen()
        logger.info("TeacherDisplay shown.")

    def update_frame(self, jpeg_base64: str) -> None:
        """Render a new JPEG frame on the display.

        If the display is not currently visible the frame is silently dropped.

        Args:
            jpeg_base64: Base64-encoded JPEG string received from the server.
        """
        if self._window is None or not self._window.isVisible():
            return
        try:
            jpeg_bytes = base64.b64decode(jpeg_base64)
            pixmap = QPixmap()
            if not pixmap.loadFromData(jpeg_bytes, "JPEG"):
                logger.warning("TeacherDisplay: failed to decode JPEG frame.")
                return
            self._window.set_pixmap(pixmap)
        except Exception as exc:
            logger.exception("TeacherDisplay.update_frame error: %s", exc)

    def hide(self) -> None:
        """Close the fullscreen window.

        Safe to call even when the window is not visible.
        """
        if self._window is not None:
            self._window.hide()
            logger.info("TeacherDisplay hidden.")


# ---------------------------------------------------------------------------
# Internal widget
# ---------------------------------------------------------------------------

class _FullscreenLabel(QWidget):
    """Borderless, always-on-top, fullscreen QWidget backed by a QLabel."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # keeps it off the taskbar
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(f"background-color: {BACKGROUND_COLOR};")

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(f"background-color: {BACKGROUND_COLOR};")

    def show_fullscreen(self) -> None:
        """Cover all connected screens and show the window."""
        app = QApplication.instance()
        if app is not None:
            # Combine the geometry of all screens to cover multi-monitor setups.
            combined = None
            for screen in app.screens():
                geom = screen.geometry()
                combined = geom if combined is None else combined.united(geom)
            if combined is not None:
                self.setGeometry(combined)
                self._label.setGeometry(0, 0, combined.width(), combined.height())
        self.showFullScreen()

    def set_pixmap(self, pixmap: QPixmap) -> None:
        """Update the displayed image, scaling to fill the label.

        Args:
            pixmap: New frame to display.
        """
        scaled = pixmap.scaled(
            self._label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)
