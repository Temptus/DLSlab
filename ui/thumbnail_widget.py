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
