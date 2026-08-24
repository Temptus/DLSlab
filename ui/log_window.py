"""
ui/log_window.py
================
Non-modal policy-violation log window for the DLSlab teacher console.

Displays a live table of app/web policy violations reported by student agents,
with timestamp, hostname, process name and policy mode.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import qtawesome as qta
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget, QStyle,
)

# Maximum rows kept in the table before the oldest are dropped.
MAX_LOG_ROWS: int = 500

# Column indices
COL_TIME = 0
COL_HOST = 1
COL_PROCESS = 2
COL_MODE = 3

_MODE_LABELS: dict[str, str] = {
    "whitelist":     "Lista Blanca (Apps)",
    "blacklist":     "Lista Negra (Apps)",
    "block_all":     "Bloqueo Web Total",
    "web_whitelist": "Lista Blanca (Web)",
}


class PolicyLogWindow(QMainWindow):
    """Floating, non-modal window that accumulates policy-violation events.

    The window can be opened/closed freely without affecting the main console.
    Unread events are counted while the window is hidden; the count is exposed
    via :attr:`unread_count` so the caller can update a toolbar badge.

    Args:
        parent: Optional parent widget (used only for positioning).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self._unread: int = 0
        self.setWindowTitle("Log de Violaciones de Política")
        self.setMinimumSize(740, 420)
        icono = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)
        self.setWindowIcon(icono)
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def unread_count(self) -> int:
        """Number of events added while the window was not visible."""
        return self._unread

    def reset_unread(self) -> None:
        """Reset the unread counter (call after the user opens the window)."""
        self._unread = 0

    def add_violation(self, hostname: str, process_name: str, mode: str) -> None:
        """Append one violation row to the table.

        Args:
            hostname:     Human-readable name of the student's PC.
            process_name: Name of the process that was blocked.
            mode:         Policy mode string (``"whitelist"``, ``"blacklist"``…).
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        mode_label = _MODE_LABELS.get(mode, mode)

        row = self._table.rowCount()
        self._table.insertRow(row)

        for col, text in enumerate([timestamp, hostname, process_name, mode_label]):
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if col == COL_MODE:
                # Colour-code by mode
                if mode == "whitelist":
                    item.setForeground(Qt.GlobalColor.yellow)
                elif mode == "blacklist":
                    item.setForeground(Qt.GlobalColor.red)
                elif mode == "web_whitelist":
                    item.setForeground(Qt.GlobalColor.cyan)
                elif mode == "block_all":
                    item.setForeground(Qt.GlobalColor.magenta)
                else:
                    item.setForeground(Qt.GlobalColor.white)
            self._table.setItem(row, col, item)

        # Auto-scroll to newest row
        self._table.scrollToBottom()

        # Trim oldest rows if over the limit
        while self._table.rowCount() > MAX_LOG_ROWS:
            self._table.removeRow(0)

        # Update counter label
        total = self._table.rowCount()
        self._count_label.setText(f"Total: {total} evento(s)")

        # Increment unread if window is hidden/minimised
        if not self.isVisible() or self.isMinimized():
            self._unread += 1

    def clear_log(self) -> None:
        """Remove all rows from the table."""
        self._table.setRowCount(0)
        self._count_label.setText("Total: 0 evento(s)")
        self._unread = 0

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Table
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Hora", "Equipo", "Proceso", "Modo"])
        self._table.horizontalHeader().setSectionResizeMode(
            COL_HOST, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            COL_PROCESS, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            COL_TIME, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            COL_MODE, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

        # Bottom bar
        bottom = QHBoxLayout()
        self._count_label = QLabel("Total: 0 evento(s)")
        bottom.addWidget(self._count_label)
        bottom.addStretch()

        clear_btn = QPushButton(qta.icon("fa6s.trash", color="#ffc107"), " Limpiar")
        clear_btn.setProperty("class", "warning")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self.clear_log)
        bottom.addWidget(clear_btn)

        close_btn = QPushButton(qta.icon("fa6s.xmark", color="#dc3545"), " Cerrar")
        close_btn.setProperty("class", "danger")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        bottom.addWidget(close_btn)

        layout.addLayout(bottom)

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802
        """Reset unread counter whenever the window becomes visible."""
        super().showEvent(event)
        self._unread = 0

