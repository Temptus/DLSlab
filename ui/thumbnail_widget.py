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

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPixmap
from PyQt6.QtWidgets import (
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

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
PRESENTING_OVERLAY_COLOR: str = "rgba(0, 140, 60, 180)"  # semi-transparent green
PRESENTING_BORDER_COLOR: str = "#1b7a3a"                  # solid green border
PRESENTING_ICON: str = "🎤 PRESENTANDO"
WATCHING_OVERLAY_COLOR: str = "rgba(0, 100, 200, 160)"   # semi-transparent blue
WATCHING_BORDER_COLOR: str = "#0d5faa"                    # solid blue border
WATCHING_ICON: str = "👁️ VIENDO"
APP_WHITELIST_BADGE: str = "🟢 APP"
APP_BLACKLIST_BADGE: str = "🔴 APP"
WEB_BLOCK_BADGE: str = "🚫 WEB"
WEB_WHITELIST_BADGE: str = "✅ WEB"
POWER_SHUTTING_DOWN_OVERLAY_COLOR: str = "rgba(255, 140, 0, 170)"
POWER_RESTARTING_OVERLAY_COLOR: str = "rgba(33, 150, 243, 170)"
POWER_OFFLINE_OVERLAY_COLOR: str = "rgba(30, 30, 30, 210)"


class ThumbnailWidget(QWidget):
    """Widget that displays a student's live screenshot thumbnail.

    Args:
        client_id: Unique identifier for the student client.
        hostname:  Human-readable name shown below the thumbnail.
        parent:    Optional parent widget.
    """

    #: Emitted when the teacher right-clicks and requests to present this
    #: student's screen to the rest of the class.
    present_requested = pyqtSignal(str)  # str = client_id
    wol_requested = pyqtSignal(str)  # str = client_id

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
        self._presenting: bool = False
        self._watching_student: bool = False
        self._app_policy: str | None = None
        self._web_policy: str | None = None
        self._power_state: str = "online"
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

        # --- Presenting badge (top-left, green) — hidden by default ---
        self._presenting_overlay = QLabel(self._image_label)
        self._presenting_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._presenting_overlay.setStyleSheet(
            f"background-color: {PRESENTING_OVERLAY_COLOR}; color: white;"
            " border-radius: 4px; padding: 2px 6px;"
        )
        presenting_font = QFont()
        presenting_font.setPointSize(9)
        presenting_font.setBold(True)
        self._presenting_overlay.setFont(presenting_font)
        self._presenting_overlay.setText(PRESENTING_ICON)
        self._presenting_overlay.adjustSize()
        self._presenting_overlay.move(4, 4)
        self._presenting_overlay.hide()

        # --- Watching student badge (top-right, blue) — hidden by default ---
        self._watching_overlay = QLabel(self._image_label)
        self._watching_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._watching_overlay.setStyleSheet(
            f"background-color: {WATCHING_OVERLAY_COLOR}; color: white;"
            " border-radius: 4px; padding: 2px 6px;"
        )
        watching_font = QFont()
        watching_font.setPointSize(9)
        watching_font.setBold(True)
        self._watching_overlay.setFont(watching_font)
        self._watching_overlay.setText(WATCHING_ICON)
        self._watching_overlay.adjustSize()
        watching_w = self._watching_overlay.width()
        watching_h = self._watching_overlay.height()
        self._watching_overlay.setGeometry(
            THUMBNAIL_WIDTH - watching_w - 4,
            4,
            watching_w,
            watching_h,
        )
        self._watching_overlay.hide()

        # --- Policy badges (bottom corners) ---
        self._app_policy_overlay = QLabel(self._image_label)
        self._app_policy_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._app_policy_overlay.setStyleSheet(
            "background-color: rgba(20, 20, 20, 180); color: white;"
            " border-radius: 4px; padding: 2px 6px;"
        )
        app_font = QFont()
        app_font.setPointSize(8)
        app_font.setBold(True)
        self._app_policy_overlay.setFont(app_font)
        self._app_policy_overlay.hide()

        self._web_policy_overlay = QLabel(self._image_label)
        self._web_policy_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._web_policy_overlay.setStyleSheet(
            "background-color: rgba(20, 20, 20, 180); color: white;"
            " border-radius: 4px; padding: 2px 6px;"
        )
        web_font = QFont()
        web_font.setPointSize(8)
        web_font.setBold(True)
        self._web_policy_overlay.setFont(web_font)
        self._web_policy_overlay.hide()

        self._power_overlay = QLabel(self._image_label)
        self._power_overlay.setGeometry(0, 0, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)
        self._power_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        power_font = QFont()
        power_font.setPointSize(16)
        power_font.setBold(True)
        self._power_overlay.setFont(power_font)
        self._power_overlay.setStyleSheet(
            "background-color: rgba(0, 0, 0, 0); color: white;"
        )
        self._power_overlay.hide()

        self._wol_button = QPushButton("⚡ WoL", self._image_label)
        self._wol_button.setGeometry(THUMBNAIL_WIDTH - 76, THUMBNAIL_HEIGHT - 34, 72, 28)
        self._wol_button.setStyleSheet(
            "QPushButton { background-color: #ffb300; color: black; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #ffc947; }"
        )
        self._wol_button.clicked.connect(lambda: self.wol_requested.emit(self.client_id))
        self._wol_button.hide()

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
        if connected and self._power_state == "offline":
            self.set_power_state("online")

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

    def set_presenting(self, presenting: bool) -> None:
        """Show or hide the presenter badge on this thumbnail.

        When ``presenting`` is ``True``, a semi-transparent green 🎤 PRESENTANDO
        badge appears in the upper-left corner of the thumbnail and the widget
        border turns green.  When ``False``, the badge is removed.

        Args:
            presenting: ``True`` to mark this student as the active presenter.
        """
        self._presenting = presenting
        if presenting:
            self._presenting_overlay.show()
            self._presenting_overlay.raise_()
            self.setStyleSheet(
                f"background-color: #2b2b2b; border-radius: 4px;"
                f" border: 2px solid {PRESENTING_BORDER_COLOR};"
            )
        else:
            self._presenting_overlay.hide()
            self.setStyleSheet("background-color: #2b2b2b; border-radius: 4px;")

    def set_watching_student(self, watching: bool) -> None:
        """Show or hide the audience-watching badge on this thumbnail.

        When ``watching`` is ``True``, a semi-transparent blue 👁️ VIENDO badge
        appears in the upper-right corner of the thumbnail and the widget border
        turns blue.  When ``False``, the badge is removed.

        Args:
            watching: ``True`` to mark this student as watching a peer presenter.
        """
        self._watching_student = watching
        if watching:
            self._watching_overlay.show()
            self._watching_overlay.raise_()
            self.setStyleSheet(
                f"background-color: #2b2b2b; border-radius: 4px;"
                f" border: 2px solid {WATCHING_BORDER_COLOR};"
            )
        else:
            self._watching_overlay.hide()
            self.setStyleSheet("background-color: #2b2b2b; border-radius: 4px;")

    def set_policy_active(
        self,
        app_policy: str | None,
        web_policy: str | None,
    ) -> None:
        """Update compact app/web policy badges on the thumbnail."""
        self._app_policy = app_policy
        self._web_policy = web_policy

        if app_policy == "whitelist":
            self._app_policy_overlay.setText(APP_WHITELIST_BADGE)
            self._app_policy_overlay.adjustSize()
            self._app_policy_overlay.move(4, THUMBNAIL_HEIGHT - self._app_policy_overlay.height() - 4)
            self._app_policy_overlay.show()
            self._app_policy_overlay.raise_()
        elif app_policy == "blacklist":
            self._app_policy_overlay.setText(APP_BLACKLIST_BADGE)
            self._app_policy_overlay.adjustSize()
            self._app_policy_overlay.move(4, THUMBNAIL_HEIGHT - self._app_policy_overlay.height() - 4)
            self._app_policy_overlay.show()
            self._app_policy_overlay.raise_()
        else:
            self._app_policy_overlay.hide()

        if web_policy == "block_all":
            self._web_policy_overlay.setText(WEB_BLOCK_BADGE)
            self._web_policy_overlay.adjustSize()
            self._web_policy_overlay.move(
                THUMBNAIL_WIDTH - self._web_policy_overlay.width() - 4,
                THUMBNAIL_HEIGHT - self._web_policy_overlay.height() - 4,
            )
            self._web_policy_overlay.show()
            self._web_policy_overlay.raise_()
        elif web_policy == "whitelist":
            self._web_policy_overlay.setText(WEB_WHITELIST_BADGE)
            self._web_policy_overlay.adjustSize()
            self._web_policy_overlay.move(
                THUMBNAIL_WIDTH - self._web_policy_overlay.width() - 4,
                THUMBNAIL_HEIGHT - self._web_policy_overlay.height() - 4,
            )
            self._web_policy_overlay.show()
            self._web_policy_overlay.raise_()
        else:
            self._web_policy_overlay.hide()

    def set_power_state(self, state: str) -> None:
        """Set power state visual overlay.

        Supported states:
            - ``online``
            - ``shutting_down``
            - ``restarting``
            - ``offline``
        """
        self._power_state = state
        self._wol_button.hide()
        if state == "shutting_down":
            self._power_overlay.setText("⏳ Apagando...")
            self._power_overlay.setStyleSheet(
                f"background-color: {POWER_SHUTTING_DOWN_OVERLAY_COLOR}; color: white;"
            )
            self._power_overlay.show()
            self._power_overlay.raise_()
        elif state == "restarting":
            self._power_overlay.setText("🔄 Reiniciando...")
            self._power_overlay.setStyleSheet(
                f"background-color: {POWER_RESTARTING_OVERLAY_COLOR}; color: white;"
            )
            self._power_overlay.show()
            self._power_overlay.raise_()
        elif state == "offline":
            self._power_overlay.setText("💤 APAGADO")
            self._power_overlay.setStyleSheet(
                f"background-color: {POWER_OFFLINE_OVERLAY_COLOR}; color: #e0e0e0;"
            )
            self._power_overlay.show()
            self._power_overlay.raise_()
            self._wol_button.show()
            self._wol_button.raise_()
        else:
            self._power_overlay.hide()

    def contextMenuEvent(self, event: "QContextMenuEvent") -> None:  # noqa: N802
        """Show a context menu with presentation options on right-click.

        Args:
            event: The Qt context-menu event triggered by a right-click.
        """
        menu = QMenu(self)
        present_action = menu.addAction("📺 Presentar al resto ")
        block_action = menu.addAction("🔒 Bloquear")
        menu.setStyleSheet("""
                QMenu {
                    background-color: #f0f0f0;
                    border: 1px solid #ccc;
                }
                QMenu::item {
                    padding: 8px 12px;
                }
                QMenu::item:selected {
                    background-color: #0078d7;   /* azul Windows 11 */
                    color: white;
                }
            """)
        action = menu.exec(event.globalPos())

        if action == present_action:
            self.present_requested.emit(self.client_id)


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
