"""
ui/thumbnail_widget.py
======================
PyQt6 widget that displays a single student's live thumbnail.

Each :class:`ThumbnailWidget` shows:
- A live screenshot image (320×180 px default).
- The student's hostname below the image.
- A coloured status indicator (green = connected, red = disconnected).
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPixmap
from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

THUMBNAIL_WIDTH: int = 320
THUMBNAIL_HEIGHT: int = 180
STATUS_INDICATOR_SIZE: int = 10   # px — diameter of the coloured dot
BLOCKED_OVERLAY_COLOR: str = "rgba(180, 0, 0, 160)"  # semi-transparent red
BLOCKED_BORDER_COLOR: str = "#cc0000"                 # solid red border
BLOCKED_ICON: str = "🔒"
TEACHER_OVERLAY_COLOR: str = "rgba(0, 80, 200, 160)"  # semi-transparent blue
TEACHER_BORDER_COLOR: str = "#1565c0"                  # solid blue border
TEACHER_ICON: str = "📡 EN VIVO"


class ThumbnailWidget(QWidget):
    """Widget that displays a student's live screenshot thumbnail.

    Args:
        client_id: Unique identifier for the student client.
        hostname:  Human-readable name shown below the thumbnail.
        parent:    Optional parent widget.
    """

    def __init__(
        self,
        client_id: str,
        hostname: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.client_id = client_id
        self._hostname = hostname
        self._connected: bool = False
        self._blocked: bool = False
        self._receiving_teacher: bool = False
        self._pixmap: Optional[QPixmap] = None

        self._setup_ui()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the widget layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # --- Thumbnail image ---
        self._image_label = QLabel()
        self._image_label.setFixedSize(THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet(
            "background-color: #1e1e1e; border: 1px solid #444;"
        )
        self._image_label.setText("No signal")
        layout.addWidget(self._image_label)

        # --- Block overlay (hidden by default) ---
        self._block_overlay = QLabel(self._image_label)
        self._block_overlay.setGeometry(0, 0, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)
        self._block_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._block_overlay.setStyleSheet(
            f"background-color: {BLOCKED_OVERLAY_COLOR}; color: white;"
        )
        font = QFont()
        font.setPointSize(28)
        self._block_overlay.setFont(font)
        self._block_overlay.setText(BLOCKED_ICON)
        self._block_overlay.hide()

        # --- Teacher live overlay (hidden by default) ---
        self._teacher_overlay = QLabel(self._image_label)
        # Positioned in the top-right corner; size adapts to content
        self._teacher_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._teacher_overlay.setStyleSheet(
            f"background-color: {TEACHER_OVERLAY_COLOR}; color: white;"
            " border-radius: 4px; padding: 2px 6px;"
        )
        teacher_font = QFont()
        teacher_font.setPointSize(9)
        teacher_font.setBold(True)
        self._teacher_overlay.setFont(teacher_font)
        self._teacher_overlay.setText(TEACHER_ICON)
        self._teacher_overlay.adjustSize()
        # Place in top-right corner with a small margin
        overlay_w = self._teacher_overlay.width()
        overlay_h = self._teacher_overlay.height()
        self._teacher_overlay.setGeometry(
            THUMBNAIL_WIDTH - overlay_w - 4,
            4,
            overlay_w,
            overlay_h,
        )
        self._teacher_overlay.hide()

        # --- Status row (dot + hostname) ---
        status_layout = _HBoxLayout()
        status_layout.setSpacing(6)

        self._status_dot = _StatusDot()
        status_layout.addWidget(self._status_dot)

        self._hostname_label = QLabel(self._hostname)
        font = QFont()
        font.setPointSize(9)
        self._hostname_label.setFont(font)
        self._hostname_label.setStyleSheet("color: #cccccc;")
        status_layout.addWidget(self._hostname_label)
        status_layout.addStretch()

        layout.addLayout(status_layout)

        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.setFixedSize(
            THUMBNAIL_WIDTH + 8,
            THUMBNAIL_HEIGHT + 36,
        )
        self.setStyleSheet("background-color: #2b2b2b; border-radius: 4px;")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_screenshot(self, image_b64: str) -> None:
        """Replace the displayed thumbnail with a new base64-encoded JPEG.

        Args:
            image_b64: Base64-encoded JPEG string from the server.
        """
        try:
            jpeg_bytes = base64.b64decode(image_b64)
            pixmap = QPixmap()
            pixmap.loadFromData(jpeg_bytes, "JPEG")
            self._pixmap = pixmap
            self._image_label.setPixmap(pixmap)
            self._image_label.setText("")
        except Exception as exc:
            logger.exception("Failed to decode thumbnail for %s: %s", self.client_id, exc)

    def set_connected(self, connected: bool) -> None:
        """Update the connection status indicator.

        Args:
            connected: ``True`` if the student is currently connected.
        """
        self._connected = connected
        self._status_dot.set_connected(connected)

    def set_hostname(self, hostname: str) -> None:
        """Update the displayed hostname label.

        Args:
            hostname: New hostname string.
        """
        self._hostname = hostname
        self._hostname_label.setText(hostname)

    def set_blocked(self, blocked: bool) -> None:
        """Show or hide the blocked-screen visual indicator on this thumbnail.

        When ``blocked`` is ``True``, a semi-transparent red overlay with a
        🔒 icon is drawn over the thumbnail and the widget border turns red.
        When ``False``, the overlay is removed and the widget returns to its
        normal appearance.

        Args:
            blocked: ``True`` to mark the student as screen-locked;
                     ``False`` to restore normal appearance.
        """
        self._blocked = blocked
        if blocked:
            self._block_overlay.show()
            self._block_overlay.raise_()
            self.setStyleSheet(
                f"background-color: #2b2b2b; border-radius: 4px;"
                f" border: 2px solid {BLOCKED_BORDER_COLOR};"
            )
        else:
            self._block_overlay.hide()
            self.setStyleSheet("background-color: #2b2b2b; border-radius: 4px;")

    def set_receiving_teacher(self, receiving: bool) -> None:
        """Show or hide the teacher-live indicator on this thumbnail.

        When ``receiving`` is ``True``, a 📡 EN VIVO badge with a blue
        background appears in the upper-right corner of the thumbnail and the
        widget border turns blue.  When ``False``, the badge is removed and the
        widget returns to its normal appearance.

        Args:
            receiving: ``True`` to mark the student as receiving the teacher's
                       screen; ``False`` to restore normal appearance.
        """
        self._receiving_teacher = receiving
        if receiving:
            self._teacher_overlay.show()
            self._teacher_overlay.raise_()
            self.setStyleSheet(
                f"background-color: #2b2b2b; border-radius: 4px;"
                f" border: 2px solid {TEACHER_BORDER_COLOR};"
            )
        else:
            self._teacher_overlay.hide()
            self.setStyleSheet("background-color: #2b2b2b; border-radius: 4px;")


# ---------------------------------------------------------------------------
# Helper widgets
# ---------------------------------------------------------------------------

class _StatusDot(QWidget):
    """Small coloured circle indicating connection status."""

    _SIZE = STATUS_INDICATOR_SIZE

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._connected = False
        self.setFixedSize(self._SIZE, self._SIZE)

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#4caf50") if self._connected else QColor("#f44336")
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, self._SIZE, self._SIZE)


def _HBoxLayout() -> "QHBoxLayout":  # noqa: N802
    """Create a zero-margin horizontal box layout (avoids circular import)."""
    from PyQt6.QtWidgets import QHBoxLayout as _QHBoxLayout
    layout = _QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    return layout
