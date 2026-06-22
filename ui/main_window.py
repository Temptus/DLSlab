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
import base64
import logging
import pathlib
import sys
import threading
from typing import Optional

from PyQt6.QtCore import QSettings, QTimer, Qt, pyqtSlot, QUrl
from PyQt6.QtGui import QAction, QFont, QIcon, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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
    QWidget, QStyle,
)

from server.main_server import DLSlabServer
from ui.log_window import PolicyLogWindow
from ui.policy_dialog import PolicyDialog
from ui.power_dialog import PowerDialog
from ui.thumbnail_widget import ThumbnailWidget

from qt_material import apply_stylesheet
import qtawesome as qta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

WINDOW_TITLE: str = "DLSlab — Consola del Profesor"
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
        self._streaming_clients: set[str] = set()  # client IDs receiving teacher screen
        self._presenting_client: Optional[str] = None  # client ID of active presenter
        self._watching_clients: set[str] = set()  # client IDs watching a peer presenter
        self._app_policy_state: dict[str, str | None] = {}
        self._web_policy_state: dict[str, str | None] = {}
        self._pending_policy_violations: list[tuple[str, str, str]] = []
        self._known_macs: dict[str, str] = {}

        self._server: Optional[DLSlabServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self._log_window: PolicyLogWindow = PolicyLogWindow()

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
        # window_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(QIcon("../icon.png"))

        # ---- Menu bar ----
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("Archivo")
        theme_menu = file_menu.addMenu("Tema")
        light_action = QAction("Claro", self)
        light_action.setShortcut("Ctrl+C")
        light_action.triggered.connect(self._apply_theme_light)
        theme_menu.addAction(light_action)
        dark_action = QAction("Oscuro", self)
        dark_action.setShortcut("Ctrl+O")
        dark_action.triggered.connect(self._apply_theme_dark)
        theme_menu.addAction(dark_action)
        file_menu.addSeparator()
        quit_action = QAction("Salir", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)
        help_menu = menu_bar.addMenu("Ayuda")
        help_action = QAction("Ayuda", self)
        help_action.setShortcut("Ctrl+H")
        help_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl("https://www.python.org")))
        help_menu.addAction(help_action)
        help_menu.addSeparator()
        about_action = QAction("Acerca de...", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)
        # menu_bar.setStyleSheet(
        #     "QMenuBar::item:hover { background: #e1e1e1; }"
        #     "QMenu { background: #f0f0f0; } QMenu::item {padding: 8px 8px;}"
        #     "QMenu::item:selected { background: #0078d7; color: #fff; border-radius: 5px; padding: 8px 8px; }"
        # )



        # ---- Toolbar ----
        toolbar = QToolBar("Controls", self)
        toolbar.setMovable(False)
        # toolbar.setStyleSheet(
        #     "QToolBar { background: #f0f0f0; border-bottom: 1px solid #b9b9b9; border-top: 1px solid #fff; spacing: 6px; }"
        #     "QToolButton { background: #f0f0f0;  color: #000; padding: 5px 5px; border-radius: 4px; font: 18pt; }"
        #     "QToolButton:hover { background: #dadada; }"
        # )
        self.addToolBar(toolbar)

        # Icons
        icon_lock = qta.icon('fa6s.lock', color='#ba0c2f')
        icon_unlock = qta.icon('fa6s.lock-open', color='#ba0c2f')
        icon_present = qta.icon('fa6s.person-chalkboard', color='#ba0c2f')
        icon_stop_stream = qta.icon('fa6s.circle-stop', color='#ba0c2f')
        icon_stop_student_stream = qta.icon('fa6s.stop', color='#ba0c2f')
        icon_policies = qta.icon('fa6s.building-shield', color='#ba0c2f')
        icon_energy = qta.icon('fa6s.bolt', color='#ba0c2f')
        icon_power_off = qta.icon('fa6s.power-off', color='#ba0c2f')
        icon_log = qta.icon('fa6s.clipboard-list', color='#ba0c2f')

        self._lock_action = QAction("", self)
        self._lock_action.setIcon(icon_lock)
        self._lock_action.setToolTip("Bloquear Pantalla")
        # self._lock_action.setDisabled(True)  # Disabled until at least one client connects
        self._lock_action.triggered.connect(self._open_blank_screen_dialog)
        toolbar.addAction(self._lock_action)
        action_widget = toolbar.widgetForAction(self._lock_action)
        if action_widget is not None:
            action_widget.setCursor(Qt.CursorShape.PointingHandCursor)


        self._unlock_action = QAction("", self)
        self._unlock_action.setIcon(icon_unlock)
        self._unlock_action.setToolTip("Desbloquear Pantallas")
        self._unlock_action.triggered.connect(self._unblank_all)
        toolbar.addAction(self._unlock_action)
        action_widget = toolbar.widgetForAction(self._unlock_action)
        if action_widget is not None:
            action_widget.setCursor(Qt.CursorShape.PointingHandCursor)

        toolbar.addSeparator()

        self._stream_action = QAction("", self)
        self._stream_action.setIcon(icon_present)
        self._stream_action.setToolTip("Transmitir Mi Pantalla")
        self._stream_action.triggered.connect(self._open_show_teacher_dialog)
        toolbar.addAction(self._stream_action)
        action_widget = toolbar.widgetForAction(self._stream_action)
        if action_widget is not None:
            action_widget.setCursor(Qt.CursorShape.PointingHandCursor)

        self._stop_stream_action = QAction("️", self)
        self._stop_stream_action.setIcon(icon_stop_stream)
        self._stop_stream_action.setToolTip("Detener la transmisión de pantalla activa")
        self._stop_stream_action.setEnabled(False)
        self._stop_stream_action.triggered.connect(self._stop_show_teacher)
        toolbar.addAction(self._stop_stream_action)
        action_widget = toolbar.widgetForAction(self._stop_stream_action)
        if action_widget is not None:
            action_widget.setCursor(Qt.CursorShape.PointingHandCursor)

        # Live indicator label (hidden until streaming starts)
        self._live_label = QLabel("  🔴 EN VIVO")
        # self._live_label.setStyleSheet(
        #     "color: #ff4444; font-weight: bold; padding: 0 8px;"
        # )
        self._live_label.hide()
        toolbar.addWidget(self._live_label)

        toolbar.addSeparator()

        self._stop_student_action = QAction("", self)
        self._stop_student_action.setIcon(icon_stop_student_stream)
        self._stop_student_action.setToolTip(
            "Detener la presentación de alumno activa"
        )
        self._stop_student_action.setEnabled(False)
        self._stop_student_action.triggered.connect(self._stop_show_student)
        toolbar.addAction(self._stop_student_action)
        action_widget = toolbar.widgetForAction(self._stop_student_action)
        if action_widget is not None:
            action_widget.setCursor(Qt.CursorShape.PointingHandCursor)

        # Presentation indicator label (hidden until a student is presenting)
        self._student_presentation_label = QLabel()
        # self._student_presentation_label.setStyleSheet(
        #     "color: #66bb6a; font-weight: bold; padding: 0 8px;"
        # )
        self._student_presentation_label.hide()
        toolbar.addWidget(self._student_presentation_label)

        toolbar.addSeparator()

        self._policy_action = QAction("", self)
        self._policy_action.setIcon(icon_policies)
        self._policy_action.setToolTip("Configurar políticas de aplicaciones y web")
        self._policy_action.triggered.connect(self._open_policy_dialog)
        toolbar.addAction(self._policy_action)
        action_widget = toolbar.widgetForAction(self._policy_action)
        if action_widget is not None:
            action_widget.setCursor(Qt.CursorShape.PointingHandCursor)

        self._power_action = QAction("", self)
        self._power_action.setIcon(icon_energy)
        self._power_action.setToolTip("Abrir control de energía y Wake-on-LAN")
        self._power_action.triggered.connect(self._open_power_dialog)
        toolbar.addAction(self._power_action)
        action_widget = toolbar.widgetForAction(self._power_action)
        if action_widget is not None:
            action_widget.setCursor(Qt.CursorShape.PointingHandCursor)

        self._emergency_shutdown_action = QAction("", self)
        self._emergency_shutdown_action.setIcon(icon_power_off)
        self._emergency_shutdown_action.setToolTip("Apagar todos los equipos")
        self._emergency_shutdown_action.triggered.connect(self._emergency_shutdown_all)
        toolbar.addAction(self._emergency_shutdown_action)
        action_widget = toolbar.widgetForAction(self._emergency_shutdown_action)
        if action_widget is not None:
            action_widget.setCursor(Qt.CursorShape.PointingHandCursor)

        toolbar.addSeparator()

        icon_send_file = qta.icon('fa6s.file-export', color='#ba0c2f')
        self._send_file_action = QAction("", self)
        self._send_file_action.setIcon(icon_send_file)
        self._send_file_action.setToolTip("Enviar documento al Escritorio de los alumnos")
        self._send_file_action.triggered.connect(self._open_send_file_dialog)
        toolbar.addAction(self._send_file_action)
        action_widget = toolbar.widgetForAction(self._send_file_action)
        if action_widget is not None:
            action_widget.setCursor(Qt.CursorShape.PointingHandCursor)

        toolbar.addSeparator()

        self._log_action = QAction("", self)
        self._log_action.setIcon(icon_log)
        self._log_action.setToolTip("Ver log de violaciones de política")
        self._log_action.triggered.connect(self._open_log_window)
        toolbar.addAction(self._log_action)
        action_widget = toolbar.widgetForAction(self._log_action)
        if action_widget is not None:
            action_widget.setCursor(Qt.CursorShape.PointingHandCursor)

        # Badge label shown next to log button when there are unread events
        self._log_badge = QLabel()
        self._log_badge.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._log_badge.hide()
        toolbar.addWidget(self._log_badge)

        # ---- Central widget ----
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Title
        title = QLabel("Monitor Estudiantes")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        # title.setStyleSheet("color: #000;")
        main_layout.addWidget(title)

        # Scroll area containing the thumbnail grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # scroll.setStyleSheet("border: none;")
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
        # self._status_bar.setStyleSheet("border-top: 1px solid #b9b9b9;")

    # ------------------------------------------------------------------
    # Server integration
    # ------------------------------------------------------------------

    def _start_server(self) -> None:
        """Start the asyncio DLSlab server in a background thread."""
        self._loop = asyncio.new_event_loop()
        self._server = DLSlabServer(
            on_screenshot=self._on_screenshot_received,
            on_policy_violation=self._on_policy_violation,
        )

        def _run() -> None:
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._server.start())
            except asyncio.CancelledError:
                pass  # Cierre normal al detener el servidor
            except RuntimeError as exc:
                if "Event loop stopped before Future completed" not in str(exc):
                    logger.exception("Server error: %s", exc)
            except Exception as exc:
                logger.exception("Server error: %s", exc)
            finally:
                try:
                    self._loop.close()
                except Exception:
                    pass

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
            widget.set_power_state("online")
            widget.set_policy_active(
                self._app_policy_state.get(client_id),
                self._web_policy_state.get(client_id),
            )
            if self._server:
                info = self._server.clients.get(client_id)
                if info and info.mac:
                    self._known_macs[client_id] = info.mac

        if self._server:
            connected_ids = {info.client_id for info in self._server.clients.all_clients()}
            for client_id, widget in self._thumbnails.items():
                if client_id not in connected_ids:
                    widget.set_connected(False)
                    widget.set_power_state("offline")
                elif client_id not in pending:
                    widget.set_connected(True)
            self._known_macs.update(self._server.wol_manager.get_known_macs())
            for client_id in sorted(self._known_macs):
                if client_id not in self._thumbnails:
                    widget = self._get_or_create_thumbnail(client_id)
                    widget.set_connected(False)
                    widget.set_power_state("offline")

        # Update status bar with connected count.
        if self._server:
            count = len(self._server.clients)
            self._status_bar.showMessage(
                f"Servidor activo — {count} Estudiante(s) conectado(s)"
            )
        self._process_policy_violations()

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
            widget.present_requested.connect(self._on_present_requested)
            widget.wol_requested.connect(self._wake_single_client)
            widget.lock_requested.connect(
                lambda cid: self._send_blank_screen([cid], BlankScreenDialog._DEFAULT_MESSAGE)
            )
            widget.unblock_requested.connect(self._send_unblank_single)
            widget.send_file_requested.connect(self._on_send_file_requested)
            self._thumbnails[client_id] = widget
            widget.set_policy_active(
                self._app_policy_state.get(client_id),
                self._web_policy_state.get(client_id),
            )

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

    def _send_unblank_single(self, client_id: str) -> None:
        """Send UNBLANK_SCREEN to a single client and restore its thumbnail."""
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.unblank_screen([client_id]),
                self._loop,
            )
        self._blocked_clients.discard(client_id)
        widget = self._thumbnails.get(client_id)
        if widget:
            widget.set_blocked(False)
        logger.info("Unblank-screen sent to client %r.", client_id)

    # ------------------------------------------------------------------
    # Show-teacher control
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _open_show_teacher_dialog(self) -> None:
        """Open the Show Teacher configuration dialog."""
        connected_ids = list(self._thumbnails.keys())
        dialog = ShowTeacherDialog(connected_ids, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        target_ids = dialog.get_selected_client_ids()
        fps = dialog.get_fps()
        quality = dialog.get_quality()

        if not target_ids:
            QMessageBox.warning(
                self,
                "Sin clientes",
                "No hay alumnos conectados o ninguno fue seleccionado.",
            )
            return

        self._start_show_teacher(target_ids, fps, quality)

    def _start_show_teacher(
        self,
        client_ids: list[str],
        fps: int,
        quality: int,
    ) -> None:
        """Dispatch START_SHOW_TEACHER + start the streamer; update UI.

        Args:
            client_ids: Clients that will receive the teacher's screen.
            fps:        Desired frame rate.
            quality:    JPEG compression quality.
        """
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.start_show_teacher(client_ids, fps=fps, quality=quality),
                self._loop,
            )
        for cid in client_ids:
            self._streaming_clients.add(cid)
            widget = self._thumbnails.get(cid)
            if widget:
                widget.set_receiving_teacher(True)

        self._stop_stream_action.setEnabled(True)
        self._stream_action.setEnabled(False)
        self._live_label.show()
        logger.info(
            "Show-teacher started — %d client(s) fps=%d quality=%d",
            len(client_ids), fps, quality,
        )

    @pyqtSlot()
    def _stop_show_teacher(self) -> None:
        """Stop the teacher screen broadcast and update the UI."""
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.stop_show_teacher(None),
                self._loop,
            )
        for cid in list(self._streaming_clients):
            widget = self._thumbnails.get(cid)
            if widget:
                widget.set_receiving_teacher(False)
        self._streaming_clients.clear()

        self._stop_stream_action.setEnabled(False)
        self._stream_action.setEnabled(True)
        self._live_label.hide()
        logger.info("Show-teacher stopped.")

    # ------------------------------------------------------------------
    # Policy control
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _open_policy_dialog(self) -> None:
        """Open the app/web policy configuration dialog."""
        connected_ids = list(self._thumbnails.keys())
        dialog = PolicyDialog(connected_ids, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        app_policy = dialog.get_app_policy()
        web_policy = dialog.get_web_policy()
        if dialog.clear_all_requested():
            app_policy["mode"] = None
            web_policy["mode"] = None

        self._apply_app_policy(app_policy)
        self._apply_web_policy(web_policy)

    def _apply_app_policy(self, policy: dict[str, object]) -> None:
        mode = policy.get("mode")
        apps = list(policy.get("apps", []))
        target_ids = self._resolve_target_ids(
            bool(policy.get("all", True)),
            list(policy.get("clients", [])),
        )
        if not target_ids:
            return

        if self._server and self._loop:
            if mode in {"whitelist", "blacklist"}:
                asyncio.run_coroutine_threadsafe(
                    self._server.set_app_policy(target_ids, str(mode), apps),
                    self._loop,
                )
            else:
                asyncio.run_coroutine_threadsafe(
                    self._server.clear_app_policy(target_ids),
                    self._loop,
                )

        for cid in target_ids:
            self._app_policy_state[cid] = str(mode) if mode else None
            widget = self._thumbnails.get(cid)
            if widget:
                widget.set_policy_active(
                    self._app_policy_state.get(cid),
                    self._web_policy_state.get(cid),
                )

    def _apply_web_policy(self, policy: dict[str, object]) -> None:
        mode = policy.get("mode")
        urls = list(policy.get("urls", []))
        target_ids = self._resolve_target_ids(
            bool(policy.get("all", True)),
            list(policy.get("clients", [])),
        )
        if not target_ids:
            return

        if self._server and self._loop:
            if mode in {"block_all", "whitelist"}:
                asyncio.run_coroutine_threadsafe(
                    self._server.set_web_policy(target_ids, str(mode), urls),
                    self._loop,
                )
            else:
                asyncio.run_coroutine_threadsafe(
                    self._server.clear_web_policy(target_ids),
                    self._loop,
                )

        for cid in target_ids:
            self._web_policy_state[cid] = str(mode) if mode else None
            widget = self._thumbnails.get(cid)
            if widget:
                widget.set_policy_active(
                    self._app_policy_state.get(cid),
                    self._web_policy_state.get(cid),
                )

    def _resolve_target_ids(self, apply_all: bool, selected: list[str]) -> list[str]:
        if apply_all:
            return list(self._thumbnails.keys())
        return selected

    def _on_policy_violation(
        self,
        client_id: str,
        process_name: str,
        mode: str,
    ) -> None:
        """Thread-safe callback invoked by server when policy violation arrives."""
        with self._lock:
            self._pending_policy_violations.append((client_id, process_name, mode))

    def _process_policy_violations(self) -> None:
        """Feed pending policy-violation events into the log window."""
        with self._lock:
            pending = list(self._pending_policy_violations)
            self._pending_policy_violations.clear()

        for client_id, process_name, mode in pending:
            hostname = client_id
            if self._server:
                info = self._server.clients.get(client_id)
                if info:
                    hostname = info.hostname
            self._log_window.add_violation(hostname, process_name, mode)

        # Update badge on the toolbar
        unread = self._log_window.unread_count
        if unread > 0:
            self._log_badge.setText(f"🔴 {unread}")
            self._log_badge.show()
        else:
            self._log_badge.hide()

    @pyqtSlot()
    def _open_log_window(self) -> None:
        """Show (or bring to front) the policy log window."""
        self._log_window.show()
        self._log_window.raise_()
        self._log_window.activateWindow()
        self._log_window.reset_unread()
        self._log_badge.hide()

    # ------------------------------------------------------------------
    # Power control
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _open_power_dialog(self) -> None:
        """Open the energy control dialog."""
        client_ids = list(self._thumbnails.keys())
        disconnected = self._get_disconnected_with_mac()
        dialog = PowerDialog(
            client_ids=client_ids,
            disconnected_clients=disconnected,
            on_shutdown=self._send_shutdown,
            on_restart=self._send_restart,
            on_lock=self._send_lock_workstation,
            on_logout=self._send_logout,
            on_open_url=self._send_open_url,
            on_run_app=self._send_run_app,
            on_wol_selected=self._send_wol_selected,
            on_wol_all=self._send_wol_all,
            on_wol_manual=self._send_wol_manual,
            parent=self,
        )
        dialog.exec()

    @pyqtSlot()
    def _emergency_shutdown_all(self) -> None:
        """Toolbar quick action for global emergency shutdown."""
        confirm = QMessageBox.question(
            self,
            "Confirmar apagado total",
            "¿Seguro que deseas apagar TODO el laboratorio?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        targets = list(self._thumbnails.keys())
        self._send_shutdown(targets, 5)

    def _send_shutdown(self, client_ids: list[str], delay: int) -> None:
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.shutdown(client_ids, delay=delay),
                self._loop,
            )
        for client_id in client_ids:
            widget = self._thumbnails.get(client_id)
            if widget:
                widget.set_power_state("shutting_down")

    def _send_restart(self, client_ids: list[str], delay: int) -> None:
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.restart(client_ids, delay=delay),
                self._loop,
            )
        for client_id in client_ids:
            widget = self._thumbnails.get(client_id)
            if widget:
                widget.set_power_state("restarting")

    def _send_logout(self, client_ids: list[str]) -> None:
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.logout(client_ids),
                self._loop,
            )

    def _send_lock_workstation(self, client_ids: list[str]) -> None:
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.lock_workstation(client_ids),
                self._loop,
            )

    def _send_open_url(self, client_ids: list[str], url: str) -> None:
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.open_url(client_ids, url),
                self._loop,
            )

    def _send_run_app(self, client_ids: list[str], path: str, args: list[str]) -> None:
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.run_app(client_ids, path, args),
                self._loop,
            )

    @pyqtSlot()
    def _open_send_file_dialog(self) -> None:
        """Open the send-file workflow: pick a file then choose target students."""
        if not self._server:
            return
        connected_ids = [info.client_id for info in self._server.clients.all_clients()]
        if not connected_ids:
            QMessageBox.warning(
                self,
                "Sin clientes",
                "No hay alumnos conectados.",
            )
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar documento para enviar",
            "",
            "Documentos (*.pdf *.docx *.doc *.xlsx *.xls *.pptx *.ppt "
            "*.png *.jpg *.jpeg *.bmp *.gif);;Todos los archivos (*.*)",
        )
        if not path:
            return

        file_path = pathlib.Path(path)
        file_size = file_path.stat().st_size
        MAX_FILE_BYTES = 35 * 1024 * 1024  # 35 MB — base64 ≈ 47 MB < 50 MB limit
        if file_size > MAX_FILE_BYTES:
            QMessageBox.warning(
                self,
                "Archivo demasiado grande",
                f"El archivo supera el límite de 35 MB "
                f"({file_size / 1024 / 1024:.1f} MB).\n"
                "Por favor, comprime el archivo antes de enviarlo.",
            )
            return

        data_b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
        filename = file_path.name

        dialog = SendFileDialog(connected_ids, filename, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        target_ids = dialog.get_selected_client_ids()
        if not target_ids:
            QMessageBox.warning(
                self,
                "Sin clientes",
                "Ningún alumno fue seleccionado.",
            )
            return

        self._send_file_to_clients(target_ids, filename, data_b64)
        QMessageBox.information(
            self,
            "Documento enviado",
            f"'{filename}' fue enviado a {len(target_ids)} estación(es).\n"
            "El archivo quedará guardado en el Escritorio de cada alumno.",
        )

    def _send_file_to_clients(
        self, client_ids: list[str], filename: str, data_b64: str
    ) -> None:
        """Dispatch SEND_FILE to the given clients.

        Args:
            client_ids: Client identifiers to send the file to.
            filename:   Original filename.
            data_b64:   Base64-encoded file contents.
        """
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.send_file(client_ids, filename, data_b64),
                self._loop,
            )
        logger.info(
            "send_file dispatched — %r to %d client(s)", filename, len(client_ids)
        )

    @pyqtSlot(str)
    def _on_send_file_requested(self, client_id: str) -> None:
        """Slot called from a thumbnail context menu — send a file to one student.

        Opens a file picker directly; no student-selection dialog is shown
        because the target is already determined by which thumbnail was clicked.

        Args:
            client_id: The client identifier of the target student.
        """
        hostname = client_id
        if self._server:
            info = self._server.clients.get(client_id)
            if info:
                hostname = info.hostname

        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Enviar documento a {hostname}",
            "",
            "Documentos (*.pdf *.docx *.doc *.xlsx *.xls *.pptx *.ppt "
            "*.png *.jpg *.jpeg *.bmp *.gif);;Todos los archivos (*.*)",
        )
        if not path:
            return

        file_path = pathlib.Path(path)
        file_size = file_path.stat().st_size
        MAX_FILE_BYTES = 35 * 1024 * 1024
        if file_size > MAX_FILE_BYTES:
            QMessageBox.warning(
                self,
                "Archivo demasiado grande",
                f"El archivo supera el límite de 35 MB "
                f"({file_size / 1024 / 1024:.1f} MB).\n"
                "Por favor, comprime el archivo antes de enviarlo.",
            )
            return

        data_b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
        filename = file_path.name

        self._send_file_to_clients([client_id], filename, data_b64)
        QMessageBox.information(
            self,
            "Documento enviado",
            f"'{filename}' fue enviado a {hostname}.\n"
            "El archivo quedará guardado en su Escritorio.",
        )

    def _send_wol_selected(self, client_ids: list[str]) -> None:
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.wake_on_lan(client_ids),
                self._loop,
            )

    def _send_wol_all(self) -> None:
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.wake_on_lan(None),
                self._loop,
            )

    def _send_wol_manual(self, mac: str) -> None:
        if self._server:
            try:
                self._server.wol_manager.wake_by_mac(mac)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "WoL", f"No se pudo enviar WoL: {exc}")

    @pyqtSlot(str)
    def _wake_single_client(self, client_id: str) -> None:
        """Wake one client from its thumbnail button."""
        self._send_wol_selected([client_id])

    def _get_disconnected_with_mac(self) -> dict[str, str]:
        """Return disconnected clients that have known MAC addresses."""
        if not self._server:
            return {}
        connected_ids = {info.client_id for info in self._server.clients.all_clients()}
        known = self._server.wol_manager.get_known_macs()
        return {
            client_id: mac
            for client_id, mac in known.items()
            if client_id not in connected_ids and mac
        }

    # ------------------------------------------------------------------
    # Show-student control
    # ------------------------------------------------------------------

    @pyqtSlot(str)
    def _on_present_requested(self, client_id: str) -> None:
        """Slot called when the teacher right-clicks a thumbnail and requests
        to present that student's screen to the rest of the class.

        Args:
            client_id: The client ID of the student to present.
        """
        self._start_show_student(client_id)

    def _start_show_student(self, presenter_id: str) -> None:
        """Dispatch start-show-student commands and update the UI.

        Args:
            presenter_id: Client ID of the student who will present.
        """
        # Stop any existing student presentation first.
        if self._presenting_client is not None:
            self._stop_show_student()

        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.start_show_student(presenter_id, None),
                self._loop,
            )

        # Update local tracking state.
        all_ids = list(self._thumbnails.keys())
        self._presenting_client = presenter_id
        self._watching_clients = {cid for cid in all_ids if cid != presenter_id}

        # Update badges on each thumbnail.
        for cid, widget in self._thumbnails.items():
            if cid == presenter_id:
                widget.set_presenting(True)
            elif cid in self._watching_clients:
                widget.set_watching_student(True)

        # Update toolbar and status.
        self._stop_student_action.setEnabled(True)
        presenter_name = presenter_id
        if self._server:
            info = self._server.clients.get(presenter_id)
            if info:
                presenter_name = info.hostname
        self._student_presentation_label.setText(
            f"  🎓 {presenter_name} está presentando"
        )
        self._student_presentation_label.show()
        logger.info("Show-student started — presenter=%s", presenter_id)

    @pyqtSlot()
    def _stop_show_student(self) -> None:
        """Stop the student presentation session and update the UI."""
        if self._server and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._server.stop_show_student(),
                self._loop,
            )

        # Clear presenter badge.
        if self._presenting_client:
            widget = self._thumbnails.get(self._presenting_client)
            if widget:
                widget.set_presenting(False)

        # Clear audience badges.
        for cid in self._watching_clients:
            widget = self._thumbnails.get(cid)
            if widget:
                widget.set_watching_student(False)

        self._presenting_client = None
        self._watching_clients.clear()

        self._stop_student_action.setEnabled(False)
        self._student_presentation_label.hide()
        logger.info("Show-student stopped.")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event: "QCloseEvent") -> None:  # noqa: N802
        """Stop the server thread when the window is closed."""
        self._timer.stop()
        if self._server and self._loop and self._loop.is_running():
            if self._server._streamer.is_streaming:
                asyncio.run_coroutine_threadsafe(
                    self._server.stop_show_teacher(None), self._loop
                )
            if self._server._student_streamer.is_streaming:
                asyncio.run_coroutine_threadsafe(
                    self._server.stop_show_student(), self._loop
                )
            # Señalizar al servidor que se detenga; start() retornará limpiamente
            stop_future = asyncio.run_coroutine_threadsafe(
                self._server.stop(), self._loop
            )
            try:
                stop_future.result(timeout=5)
            except Exception:
                pass
        if hasattr(self, "_server_thread") and self._server_thread.is_alive():
            self._server_thread.join(timeout=5)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Theme control
    # ------------------------------------------------------------------

    def _get_extra(self) -> dict:
        """Return the shared qt-material extra configuration dict."""
        return {
            'danger': '#dc3545',
            'warning': '#ffc107',
            'success': '#4caf50',
            'info': '#17a2b8',
            'density_scale': '-2',
            'font_family': 'Roboto',
        }

    @pyqtSlot()
    def _apply_theme_light(self) -> None:
        """Switch the application theme to light_red."""
        app = QApplication.instance()
        apply_stylesheet(app, theme='light_red.xml', invert_secondary=True, extra=self._get_extra())
        QSettings("DLSlab", "TeacherConsole").setValue("theme", "light")

    @pyqtSlot()
    def _apply_theme_dark(self) -> None:
        """Switch the application theme to dark_red."""
        app = QApplication.instance()
        apply_stylesheet(app, theme='dark_red.xml', invert_secondary=False, extra=self._get_extra())
        QSettings("DLSlab", "TeacherConsole").setValue("theme", "dark")

    # ------------------------------------------------------------------
    # About Dialog
    # ------------------------------------------------------------------

    def show_about_dialog(self):
        QMessageBox.information(
            self,
            "Acerca de",
            "DLSlab Teacher Console\nVersión 1.0\n© 2026 by Temptus\nhttps://github.com/Temptus/\n\nAplicación para monitoreo y control de estudiantes.\n"
        )

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
    extra = {
        # Button colors
        'danger': '#dc3545',
        'warning': '#ffc107',
        'success': '#4caf50',
        'info': '#17a2b8',
        'density_scale': '-2',

        # Font
        'font_family': 'Roboto',
    }

    # Restaurar el último tema seleccionado por el usuario (por defecto: dark)
    saved_theme = QSettings("DLSlab", "TeacherConsole").value("theme", "dark")
    if saved_theme == "light":
        apply_stylesheet(app, theme='light_red.xml', invert_secondary=True, extra=extra)
    else:
        apply_stylesheet(app, theme='dark_red.xml', invert_secondary=False, extra=extra)

    app.setApplicationName("DLSlab")

    from ui.splash_screen import create_splash
    splash = create_splash()
    splash.show()
    app.processEvents()

    window = MainWindow()
    splash.finish(window)
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
        # self.setStyleSheet(
        #     "QRadioButton::indicator:unchecked { border: 1px solid #555; border-radius: 8px; background: #dadada; }"
        #     "QRadioButton::indicator:checked { border: 3px solid #555; border-radius: 8px; background: #7bf279; }"
        # )

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
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty('class', 'success')
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


# ---------------------------------------------------------------------------
# Show-teacher dialog
# ---------------------------------------------------------------------------

class ShowTeacherDialog(QDialog):
    """Modal dialog for configuring and starting a teacher screen broadcast.

    Lets the teacher choose target students, frame rate, and JPEG quality before
    starting the stream.

    Args:
        client_ids: List of client identifiers currently visible in the grid.
        parent:     Optional parent widget.
    """

    _FPS_OPTIONS: list[int] = [5, 10, 15, 20]
    _QUALITY_OPTIONS: list[int] = [40, 60, 80]

    def __init__(
        self,
        client_ids: list[str],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._client_ids = client_ids
        self.setWindowTitle("📡 Transmitir Mi Pantalla")
        self.setMinimumWidth(440)
        self._setup_ui()
        # self.setStyleSheet(
        #     "QRadioButton::indicator:unchecked { border: 1px solid #555; border-radius: 8px; background: #dadada; }"
        #     "QRadioButton::indicator:checked { border: 3px solid #555; border-radius: 8px; background: #7bf279; }"
        # )

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the dialog layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Target selection
        layout.addWidget(QLabel("Transmitir a:"))

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

        # FPS selector
        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("Fotogramas por segundo (FPS):"))
        self._fps_combo = QComboBox()
        for fps in self._FPS_OPTIONS:
            self._fps_combo.addItem(str(fps), fps)
        self._fps_combo.setCurrentIndex(1)  # default 10 FPS
        fps_row.addWidget(self._fps_combo)
        fps_row.addStretch()
        layout.addLayout(fps_row)

        # Quality selector
        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("Calidad JPEG:"))
        self._quality_combo = QComboBox()
        for q in self._QUALITY_OPTIONS:
            self._quality_combo.addItem(f"{q}%", q)
        self._quality_combo.setCurrentIndex(1)  # default 60%
        quality_row.addWidget(self._quality_combo)
        quality_row.addStretch()
        layout.addLayout(quality_row)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Iniciar Transmisión"
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty('class', 'success')
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

    def get_selected_client_ids(self) -> list[str]:
        """Return the list of client IDs to stream to.

        If "Todos los alumnos" is selected, returns all client IDs.  Otherwise,
        returns only the IDs highlighted in the list widget.

        Returns:
            List of client identifier strings.
        """
        if self._radio_all.isChecked():
            return list(self._client_ids)
        return [item.text() for item in self._list_widget.selectedItems()]

    def get_fps(self) -> int:
        """Return the selected frame rate.

        Returns:
            Selected FPS as an integer.
        """
        return self._fps_combo.currentData()

    def get_quality(self) -> int:
        """Return the selected JPEG quality.

        Returns:
            Selected quality as an integer (0–100).
        """
        return self._quality_combo.currentData()


# ---------------------------------------------------------------------------
# Send-file dialog
# ---------------------------------------------------------------------------

class SendFileDialog(QDialog):
    """Modal dialog for choosing which connected students receive a document.

    Shows the selected filename and lets the teacher send it to all connected
    students or to a specific subset.

    Args:
        client_ids: List of currently *connected* client identifiers.
        filename:   Name of the file about to be sent (display only).
        parent:     Optional parent widget.
    """

    def __init__(
        self,
        client_ids: list[str],
        filename: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._client_ids = client_ids
        self._filename = filename
        self.setWindowTitle("📄 Enviar Documento")
        self.setMinimumWidth(440)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        layout.addWidget(QLabel("Archivo seleccionado:"))
        file_label = QLabel(self._filename)
        file_label.setWordWrap(True)
        layout.addWidget(file_label)

        layout.addWidget(QLabel("Enviar a:"))

        self._radio_all = QRadioButton("Todos los alumnos conectados")
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
            self._list_widget.addItem(QListWidgetItem(cid))
        layout.addWidget(self._list_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Enviar")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty(
            "class", "success"
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @pyqtSlot(bool)
    def _toggle_list(self, all_selected: bool) -> None:
        self._list_widget.setEnabled(not all_selected)

    def get_selected_client_ids(self) -> list[str]:
        """Return the client IDs chosen by the teacher."""
        if self._radio_all.isChecked():
            return list(self._client_ids)
        return [item.text() for item in self._list_widget.selectedItems()]


if __name__ == "__main__":
    main()
