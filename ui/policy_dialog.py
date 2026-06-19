"""
ui/policy_dialog.py
===================
Policy configuration dialog for app/web restrictions.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSlot, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
import qtawesome as qta

COMMON_APP_SHORTCUTS: dict[str, str] = {
    "Chrome": "chrome.exe",
    "Firefox": "firefox.exe",
    "Word": "winword.exe",
    "Excel": "excel.exe",
    "PowerPoint": "powerpnt.exe",
    "Notepad": "notepad.exe",
}
COMMON_URL_SHORTCUTS: dict[str, str] = {
    "Google": "https://www.google.com",
    "Wikipedia": "https://www.wikipedia.org",
    "YouTube": "https://www.youtube.com",
}


class PolicyDialog(QDialog):
    """Dialog with app and web policy tabs."""

    def __init__(
        self,
        client_ids: list[str],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._client_ids = client_ids
        self._clear_all_requested = False
        self.setWindowTitle("🚦 Políticas de Aplicaciones y Web")
        self.setMinimumWidth(620)
        self.setMinimumHeight(520)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.setWindowIcon(QIcon("../icon.png"))

        # Icons
        icon_tab_0 = qta.icon('fa6s.desktop', color='#f44336')
        icon_tab_1 = qta.icon('fa6s.globe', color='#f44336')

        tabs = QTabWidget()
        tabs.addTab(self._build_app_tab(), "Aplicaciones")
        tabs.addTab(self._build_web_tab(), "Web")
        tabs.setTabIcon(0, icon_tab_0)
        tabs.setTabIcon(1, icon_tab_1)
        tabs.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(tabs)

        button_row = QHBoxLayout()
        self._clear_button = QPushButton("Limpiar Todo")
        self._clear_button.setProperty("class", "warning")
        self._clear_button.clicked.connect(self._on_clear_all_clicked)
        button_row.addWidget(self._clear_button)
        button_row.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aplicar")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("class", "success")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        button_row.addWidget(buttons)
        layout.addLayout(button_row)

    def _build_app_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Modo de política de aplicaciones:"))

        # icons
        icon_no_restrictions = qta.icon('fa6s.circle', color='#4CAF50')
        icon_white_list = qta.icon('fa6s.circle', color='#FF9800')
        icon_black_list = qta.icon('fa6s.circle', color='#f44336')

        self._app_none_radio = QRadioButton("Sin restricciones")
        self._app_none_radio.setChecked(True)
        self._app_none_radio.setIcon(icon_no_restrictions)
        self._app_whitelist_radio = QRadioButton("Lista Blanca (solo permitidas)")
        self._app_whitelist_radio.setIcon(icon_white_list)
        self._app_blacklist_radio = QRadioButton("Lista Negra (prohibidas)")
        self._app_blacklist_radio.setIcon(icon_black_list)
        layout.addWidget(self._app_none_radio)
        layout.addWidget(self._app_whitelist_radio)
        layout.addWidget(self._app_blacklist_radio)

        self._app_mode_group = QButtonGroup(widget)
        self._app_mode_group.addButton(self._app_none_radio)
        self._app_mode_group.addButton(self._app_whitelist_radio)
        self._app_mode_group.addButton(self._app_blacklist_radio)

        layout.addWidget(QLabel("Procesos (uno por línea):"))
        self._app_text = QTextEdit()
        self._app_text.setPlaceholderText("notepad.exe\nchrome.exe")
        layout.addWidget(self._app_text)

        quick_row = QHBoxLayout()
        for label, process in COMMON_APP_SHORTCUTS.items():
            button = QPushButton(f"+ {label}")
            button.clicked.connect(lambda _, p=process: self._append_line(self._app_text, p))
            quick_row.addWidget(button)
        quick_row.addStretch()
        layout.addLayout(quick_row)

        layout.addWidget(QLabel("Aplicar a:"))
        self._app_all_radio = QRadioButton("Todos")
        self._app_all_radio.setChecked(True)
        self._app_selected_radio = QRadioButton("Selección múltiple")
        self._app_all_radio.toggled.connect(self._toggle_app_client_list)
        layout.addWidget(self._app_all_radio)
        layout.addWidget(self._app_selected_radio)

        self._app_target_group = QButtonGroup(widget)
        self._app_target_group.addButton(self._app_all_radio)
        self._app_target_group.addButton(self._app_selected_radio)

        self._app_client_list = QListWidget()
        self._app_client_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self._app_client_list.setEnabled(False)
        for client_id in self._client_ids:
            self._app_client_list.addItem(QListWidgetItem(client_id))
        layout.addWidget(self._app_client_list)
        return widget

    def _build_web_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Modo de política web:"))

        # Icons
        icon_no_restrictions = qta.icon('fa6s.circle', color='#4CAF50')
        icon_web_block = qta.icon('fa6s.ban', color='#f44336')
        icon_white_list = qta.icon('fa6s.check', color='#FF9800')

        self._web_none_radio = QRadioButton("Sin restricciones")
        self._web_none_radio.setChecked(True)
        self._web_none_radio.setIcon(icon_no_restrictions)
        self._web_block_all_radio = QRadioButton("Bloquear toda navegación")
        self._web_block_all_radio.setIcon(icon_web_block)
        self._web_whitelist_radio = QRadioButton("Solo URLs permitidas")
        self._web_whitelist_radio.setIcon(icon_white_list)
        layout.addWidget(self._web_none_radio)
        layout.addWidget(self._web_block_all_radio)
        layout.addWidget(self._web_whitelist_radio)

        self._web_mode_group = QButtonGroup(widget)
        self._web_mode_group.addButton(self._web_none_radio)
        self._web_mode_group.addButton(self._web_block_all_radio)
        self._web_mode_group.addButton(self._web_whitelist_radio)

        layout.addWidget(QLabel("URLs permitidas (una por línea):"))
        self._web_text = QTextEdit()
        self._web_text.setPlaceholderText("https://ejemplo.com\nhttps://campus.universidad.edu")
        layout.addWidget(self._web_text)

        quick_row = QHBoxLayout()
        for label, url in COMMON_URL_SHORTCUTS.items():
            button = QPushButton(f"+ {label}")
            button.clicked.connect(lambda _, u=url: self._append_line(self._web_text, u))
            quick_row.addWidget(button)
        quick_row.addStretch()
        layout.addLayout(quick_row)

        layout.addWidget(QLabel("Aplicar a:"))
        self._web_all_radio = QRadioButton("Todos")
        self._web_all_radio.setChecked(True)
        self._web_selected_radio = QRadioButton("Selección múltiple")
        self._web_all_radio.toggled.connect(self._toggle_web_client_list)
        layout.addWidget(self._web_all_radio)
        layout.addWidget(self._web_selected_radio)

        self._web_target_group = QButtonGroup(widget)
        self._web_target_group.addButton(self._web_all_radio)
        self._web_target_group.addButton(self._web_selected_radio)

        self._web_client_list = QListWidget()
        self._web_client_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self._web_client_list.setEnabled(False)
        for client_id in self._client_ids:
            self._web_client_list.addItem(QListWidgetItem(client_id))
        layout.addWidget(self._web_client_list)
        return widget

    @pyqtSlot(bool)
    def _toggle_app_client_list(self, all_selected: bool) -> None:
        self._app_client_list.setEnabled(not all_selected)

    @pyqtSlot(bool)
    def _toggle_web_client_list(self, all_selected: bool) -> None:
        self._web_client_list.setEnabled(not all_selected)

    @pyqtSlot()
    def _on_clear_all_clicked(self) -> None:
        self._clear_all_requested = True
        self._app_none_radio.setChecked(True)
        self._web_none_radio.setChecked(True)
        self._app_text.clear()
        self._web_text.clear()
        self._app_all_radio.setChecked(True)
        self._web_all_radio.setChecked(True)
        self.accept()

    def clear_all_requested(self) -> bool:
        """Return whether the user pressed 'Limpiar Todo'."""
        return self._clear_all_requested

    def get_app_policy(self) -> dict[str, object]:
        """Return app policy selection."""
        mode: str | None = None
        if self._app_whitelist_radio.isChecked():
            mode = "whitelist"
        elif self._app_blacklist_radio.isChecked():
            mode = "blacklist"
        apps = self._parse_lines(self._app_text.toPlainText())
        return {
            "mode": mode,
            "apps": apps,
            "all": self._app_all_radio.isChecked(),
            "clients": [item.text() for item in self._app_client_list.selectedItems()],
        }

    def get_web_policy(self) -> dict[str, object]:
        """Return web policy selection."""
        mode: str | None = None
        if self._web_block_all_radio.isChecked():
            mode = "block_all"
        elif self._web_whitelist_radio.isChecked():
            mode = "whitelist"
        urls = self._parse_lines(self._web_text.toPlainText())
        return {
            "mode": mode,
            "urls": urls,
            "all": self._web_all_radio.isChecked(),
            "clients": [item.text() for item in self._web_client_list.selectedItems()],
        }

    @staticmethod
    def _append_line(text_edit: QTextEdit, value: str) -> None:
        lines = text_edit.toPlainText().splitlines()
        if value not in [line.strip() for line in lines]:
            lines.append(value)
            text_edit.setPlainText("\n".join(line for line in lines if line.strip()))

    @staticmethod
    def _parse_lines(text: str) -> list[str]:
        return [line.strip() for line in text.splitlines() if line.strip()]

