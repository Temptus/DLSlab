"""
client/student_display.py
=========================
PyQt6 fullscreen window that renders a presenting student's live screen on the
audience's displays.

Unlike :class:`~client.blank_screen.BlankScreenOverlay`, this window does **not**
block keyboard or mouse input — the audience can still interact with the desktop
behind the overlay.  The window always stays on top so the student presenter's
content remains visible.

A semi-transparent banner in the upper-left corner shows the presenter's name.

Usage::

    display = StudentDisplay()
    display.show("PC-ALUMNO-03")
    display.update_frame(frame_b64)   # call repeatedly as frames arrive
    display.hide()
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

BACKGROUND_COLOR: str = "#000000"
BANNER_COLOR: str = "rgba(0, 0, 0, 160)"       # semi-transparent black
BANNER_TEXT_COLOR: str = "#ffffff"
BANNER_FONT_SIZE: int = 14                       # pt
BANNER_PADDING: int = 8                          # px


# ---------------------------------------------------------------------------
# StudentDisplay class
# ---------------------------------------------------------------------------


class StudentDisplay:
    """Fullscreen, always-on-top window that displays a student presenter's screen.

    The window is created lazily on the first call to :meth:`show` and reused
    for subsequent frames.  It covers all screens attached to the client.

    Notes:
        * Input events (keyboard, mouse) **pass through** — this differs from
          :class:`~client.blank_screen.BlankScreenOverlay` which suppresses all input.
        * The presenter's own machine does **not** receive this display — only the
          audience clients do.
        * If a blank-screen overlay is also active, the OS will stack it on top
          because it is shown *after* this window.
    """

    def __init__(self) -> None:
        self._window: Optional[_FullscreenPresenterWidget] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(self, presenter_name: str) -> None:
        """Open (or raise) the fullscreen student-display window.

        Args:
            presenter_name: Human-readable name of the presenting student shown
                            in the banner (e.g. ``"PC-ALUMNO-03"``).
        """
        if self._window is None:
            self._window = _FullscreenPresenterWidget()
        self._window.set_presenter_name(presenter_name)
        self._window.show_fullscreen()
        logger.info("StudentDisplay shown — presenter=%r", presenter_name)

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
                logger.warning("StudentDisplay: failed to decode JPEG frame.")
                return
            self._window.set_pixmap(pixmap)
        except Exception as exc:
            logger.exception("StudentDisplay.update_frame error: %s", exc)

    def hide(self) -> None:
        """Close the fullscreen window.

        Safe to call even when the window is not visible.
        """
        if self._window is not None:
            self._window.hide()
            logger.info("StudentDisplay hidden.")


# ---------------------------------------------------------------------------
# Internal widget
# ---------------------------------------------------------------------------


class _FullscreenPresenterWidget(QWidget):
    """Borderless, always-on-top, fullscreen QWidget backed by a QLabel.

    Displays the student presenter's screen with a semi-transparent banner
    in the upper-left corner indicating who is presenting.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # keeps it off the taskbar
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(f"background-color: {BACKGROUND_COLOR};")

        # --- Main image label ---
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(f"background-color: {BACKGROUND_COLOR};")

        # --- Presenter banner (upper-left corner) ---
        self._banner = QLabel(self)
        self._banner.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        self._banner.setStyleSheet(
            f"background-color: {BANNER_COLOR};"
            f" color: {BANNER_TEXT_COLOR};"
            " border-radius: 4px;"
            f" padding: {BANNER_PADDING}px;"
        )
        font = QFont()
        font.setPointSize(BANNER_FONT_SIZE)
        font.setBold(True)
        self._banner.setFont(font)
        self._banner.setText("🖥️ … está presentando")
        self._banner.adjustSize()

    def set_presenter_name(self, name: str) -> None:
        """Update the banner text to reflect the presenter's name.

        Args:
            name: Human-readable presenter name.
        """
        self._banner.setText(f"🖥️ {name} está presentando")
        self._banner.adjustSize()
        # Re-position in the top-left corner with a small margin.
        margin = BANNER_PADDING
        self._banner.move(margin, margin)

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
                # Re-position banner after geometry is set.
                margin = BANNER_PADDING
                self._banner.move(margin, margin)
        self.showFullScreen()
        self._banner.raise_()

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
        # Keep banner on top after every frame update.
        self._banner.raise_()
