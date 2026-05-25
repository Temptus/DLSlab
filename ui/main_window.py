"""
ui/main_window.py
=================
PyQt6 main window for the DLSlab teacher console.

The window displays a scrollable grid of :class:`~ui.thumbnail_widget.ThumbnailWidget`
instances — one per connected student.  It integrates with the asyncio server
by running a :class:`~server.main_server.DLSlabServer` in a background thread
(via :mod:`asyncio`).

Usage::

    from PyQt6.QtWidgets import QApplication
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Optional

from PyQt6.QtCore import QTimer, Qt, pyqtSlot
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QRadioButton,
    QScrollArea,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from server.main_server import DLSlabServer
from ui.thumbnail_widget import ThumbnailWidget

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

WINDOW_TITLE: str = "DLSlab — Teacher Console"
WINDOW_WIDTH: int = 1280
WINDOW_HEIGHT: int = 720
GRID_COLUMNS: int = 4           # number of thumbnails per row
UI_REFRESH_INTERVAL_MS: int = 100  # how often the UI polls for screenshot updates


class MainWindow(QMainWindow):
    """Main teacher console window.

    Opens a DLSlab asyncio server in a background thread and updates the
    thumbnail grid whenever new screenshots arrive from student clients.
    """

    def __init__(self) -> None:
        super().__init__()
        self._thumbnails: dict[str, ThumbnailWidget] = {}
        self._pending_screenshots: dict[str, str] = {}  # client_id -> base64 image
        self._lock = threading.Lock()
        self._blocked_clients: set[str] = set()  # client IDs currently screen-locked

        self._server: Optional[DLSlabServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self._setup_ui()
        self._start_server()
        self._start_refresh_timer()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the main window UI."""
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setStyleSheet("background-color: #1e1e1e;")

        # ---- Menu bar ----
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # ---- Toolbar ----
        toolbar = QToolBar("Controls", self)
        toolbar.setMovable(False)
        toolbar.setStyleSheet(
            "QToolBar { background: #2b2b2b; border-bottom: 1px solid #444; spacing: 6px; }"
            "QToolButton { color: #ffffff; padding: 4px 10px; border-radius: 4px; }"
            "QToolButton:hover { background: #444; }"
        )
        self.addToolBar(toolbar)

        self._lock_action = QAction("🔒 Bloquear Pantallas", self)
        self._lock_action.setToolTip("Oscurecer pantallas de los alumnos y bloquear input")
        self._lock_action.triggered.connect(self._open_blank_screen_dialog)
        toolbar.addAction(self._lock_action)

        self._unlock_action = QAction("🔓 Desbloquear Pantallas", self)
        self._unlock_action.setToolTip("Restaurar pantallas de todos los alumnos")
        self._unlock_action.triggered.connect(self._unblank_all)
        toolbar.addAction(self._unlock_action)

        # ---- Central widget ----
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Title
        title = QLabel("Student Monitor")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        title.setStyleSheet("color: #ffffff;")
        main_layout.addWidget(title)

        # Scroll area containing the thumbnail grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none;")
        main_layout.addWidget(scroll)

        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setSpacing(12)
        self._grid_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        scroll.setWidget(self._grid_container)

        # ---- Status bar ----
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Server starting…")

    # ------------------------------------------------------------------
    # Server integration
    # ------------------------------------------------------------------

    def _start_server(self) -> None:
        """Start the asyncio DLSlab server in a background thread."""
        self._loop = asyncio.new_event_loop()
        self._server = DLSlabServer(on_screenshot=self._on_screenshot_received)

        def _run() -> None:
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._server.start())
            except Exception as exc:
                logger.exception("Server error: %s", exc)

        self._server_thread = threading.Thread(target=_run, daemon=True, name="dlslab-server")
        self._server_thread.start()
        self._status_bar.showMessage("Server listening on port 9000")
        logger.info("Server thread started.")

    def _on_screenshot_received(self, client_id: str, image_b64: str) -> None:
        """Callback invoked by the server thread when a screenshot arrives.

        This method is called from the asyncio thread; it stores the image
        in a thread-safe buffer so the Qt UI thread can pick it up.

        Args:
            client_id:  Source client identifier.
            image_b64:  Base64-encoded JPEG thumbnail.
        """
        with self._lock:
            self._pending_screenshots[client_id] = image_b64

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _start_refresh_timer(self) -> None:
        """Start the Qt timer that refreshes thumbnails from the buffer."""
        self._timer = QTimer(self)
        self._timer.setInterval(UI_REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self._refresh_thumbnails)
        self._timer.start()

    @pyqtSlot()
    def _refresh_thumbnails(self) -> None:
        """Pull pending screenshots from the buffer and update widgets.

        This slot runs in the Qt main thread.
        """
        with self._lock:
            pending = dict(self._pending_screenshots)
            self._pending_screenshots.clear()

        for client_id, image_b64 in pending.items():
            widget = self._get_or_create_thumbnail(client_id)
            widget.update_screenshot(image_b64)
            widget.set_connected(True)

        # Update status bar with connected count.
        if self._server:
            count = len(self._server.clients)
            self._status_bar.showMessage(
                f"Server running — {count} student(s) connected"
            )

    def _get_or_create_thumbnail(self, client_id: str) -> ThumbnailWidget:
        """Return the thumbnail widget for *client_id*, creating it if needed.

        Args:
            client_id: Unique client identifier.

        Returns:
            The existing or newly created :class:`ThumbnailWidget`.
        """
        if client_id not in self._thumbnails:
            # Try to get the hostname from the server's client registry.
            hostname = client_id
            if self._server:
                info = self._server.clients.get(client_id)
                if info:
                    hostname = info.hostname

            widget = ThumbnailWidget(client_id=client_id, hostname=hostname)
            self._thumbnails[client_id] = widget

            # Insert into the next grid cell.
            index = len(self._thumbnails) - 1
            row, col = divmod(index, GRID_COLUMNS)
            self._grid_layout.addWidget(widget, row, col)

        return self._thumbnails[client_id]

    # ------------------------------------------------------------------
    # Blank-screen control
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _open_blank_screen_dialog(self) -> None:
        """Open the lock-screen configuration dialog."""
        connected_ids = list(self._thumbnails.keys())
        dialog = BlankScreenDialog(connected_ids, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        message = dialog.get_message()
        target_ids = dialog.get_selected_client_ids()

        if not target_ids:
            QMessageBox.warning(
                self,
                "Sin clientes",
                "No hay alumnos conectados o ninguno fue seleccionado.",
            )
            return

        self._send_blank_screen(target_ids, message)

    def _send_blank_screen(self, client_ids: list[str], message: str) -> None:
        """Dispatch BLANK_SCREEN to the given clients and update the UI.

        Args:
            client_ids: Client identifiers to lock.
            message:    Text to display on the overlay.
        """
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.blank_screen(client_ids, message),
                self._loop,
            )
        for cid in client_ids:
            self._blocked_clients.add(cid)
            widget = self._thumbnails.get(cid)
            if widget:
                widget.set_blocked(True)
        logger.info(
            "Blank-screen sent to %d client(s): %r", len(client_ids), client_ids
        )

    @pyqtSlot()
    def _unblank_all(self) -> None:
        """Send UNBLANK_SCREEN to all clients and restore thumbnail indicators."""
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.unblank_screen(None),
                self._loop,
            )
        for cid in list(self._blocked_clients):
            widget = self._thumbnails.get(cid)
            if widget:
                widget.set_blocked(False)
        self._blocked_clients.clear()
        logger.info("Unblank-screen sent to all clients.")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event: "QCloseEvent") -> None:  # noqa: N802
        """Stop the server thread when the window is closed."""
        self._timer.stop()
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(self._server.stop(), self._loop)
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Launch the DLSlab teacher console."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("DLSlab")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


# ---------------------------------------------------------------------------
# Blank-screen dialog
# ---------------------------------------------------------------------------

class BlankScreenDialog(QDialog):
    """Modal dialog for configuring and applying a screen-lock command.

    Lets the teacher enter a custom message and choose whether to lock all
    connected students or a manually selected subset.

    Args:
        client_ids: List of client identifiers currently visible in the grid.
        parent:     Optional parent widget.
    """

    _DEFAULT_MESSAGE: str = "Atención al frente"

    def __init__(
        self,
        client_ids: list[str],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._client_ids = client_ids
        self.setWindowTitle("🔒 Bloquear Pantallas")
        self.setMinimumWidth(420)
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the dialog layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Message field
        layout.addWidget(QLabel("Mensaje a mostrar en pantalla:"))
        self._message_edit = QLineEdit(self._DEFAULT_MESSAGE)
        self._message_edit.setPlaceholderText(self._DEFAULT_MESSAGE)
        layout.addWidget(self._message_edit)

        # Target selection
        layout.addWidget(QLabel("Aplicar a:"))

        self._radio_all = QRadioButton("Todos los alumnos")
        self._radio_all.setChecked(True)
        self._radio_all.toggled.connect(self._toggle_list)
        layout.addWidget(self._radio_all)

        self._radio_selected = QRadioButton("Alumnos seleccionados:")
        layout.addWidget(self._radio_selected)

        self._list_widget = QListWidget()
        self._list_widget.setEnabled(False)
        self._list_widget.setSelectionMode(
            QListWidget.SelectionMode.MultiSelection
        )
        for cid in self._client_ids:
            item = QListWidgetItem(cid)
            self._list_widget.addItem(item)
        layout.addWidget(self._list_widget)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aplicar bloqueo")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @pyqtSlot(bool)
    def _toggle_list(self, all_selected: bool) -> None:
        """Enable or disable the client list based on the radio selection.

        Args:
            all_selected: ``True`` when "Todos los alumnos" is active.
        """
        self._list_widget.setEnabled(not all_selected)

    # ------------------------------------------------------------------
    # Public result accessors
    # ------------------------------------------------------------------

    def get_message(self) -> str:
        """Return the message text entered by the teacher.

        Returns:
            The trimmed message string, or the default if empty.
        """
        text = self._message_edit.text().strip()
        return text if text else self._DEFAULT_MESSAGE

    def get_selected_client_ids(self) -> list[str]:
        """Return the list of client IDs to lock.

        If "Todos los alumnos" is selected, returns all client IDs.  Otherwise,
        returns only the IDs highlighted in the list widget.

        Returns:
            List of client identifier strings.
        """
        if self._radio_all.isChecked():
            return list(self._client_ids)
        return [item.text() for item in self._list_widget.selectedItems()]


if __name__ == "__main__":
    main()
