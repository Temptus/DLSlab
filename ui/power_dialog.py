"""
ui/power_dialog.py
==================
Power-control and remote-execution dialog for DLSlab.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
import qtawesome as qta

DEFAULT_DELAY_SECONDS: int = 5
MAX_DELAY_SECONDS: int = 60
USER_ROLE: Qt.ItemDataRole = Qt.ItemDataRole.UserRole

URL_SHORTCUTS: dict[str, str] = {
    "Office 365": "https://login.microsoftonline.com/",
    "Google Classroom": "https://classroom.google.com",
    "Gmetrix": "https://www.gmetrix.net/Login.aspx",
    "Certiport": "https://app.certiport.com/portal/login",
    "IBEC Learning": "https://ibeclearning.com/login",
    "AMCO Aluzo": "https://idp.amco.me/signin",
}


class PowerDialog(QDialog):
    """Dialog for energy control, remote URL/app execution, and Wake-on-LAN."""

    def __init__(
        self,
        client_ids: list[str],
        disconnected_clients: dict[str, str],
        on_shutdown: Callable[[list[str], int], None],
        on_restart: Callable[[list[str], int], None],
        on_lock: Callable[[list[str]], None],
        on_logout: Callable[[list[str]], None],
        on_open_url: Callable[[list[str], str], None],
        on_run_app: Callable[[list[str], str, list[str]], None],
        on_wol_selected: Callable[[list[str]], None],
        on_wol_all: Callable[[], None],
        on_wol_manual: Callable[[str], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._client_ids = client_ids
        self._disconnected_clients = disconnected_clients
        self._on_shutdown = on_shutdown
        self._on_restart = on_restart
        self._on_lock = on_lock
        self._on_logout = on_logout
        self._on_open_url = on_open_url
        self._on_run_app = on_run_app
        self._on_wol_selected = on_wol_selected
        self._on_wol_all = on_wol_all
        self._on_wol_manual = on_wol_manual

        self.setWindowTitle("⚡ Control de Energía")
        self.setMinimumWidth(720)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(self._build_power_section())
        layout.addWidget(self._build_remote_exec_section())
        layout.addWidget(self._build_wol_section())

    def _build_power_section(self) -> QGroupBox:
        box = QGroupBox("⚡ Control de Energía")
        layout = QVBoxLayout(box)

        # Icons
        icon_power_off = qta.icon('fa6s.power-off', color='#fff')
        icon_restart = qta.icon('fa6s.arrows-rotate', color='#fff')
        icon_lock = qta.icon('fa6s.lock', color='#fff')
        icon_logout = qta.icon('fa6s.arrow-right-to-bracket', color='#fff')

        row = QHBoxLayout()
        self._shutdown_btn = QPushButton("Apagar Todo")
        self._shutdown_btn.setStyleSheet(
            "QPushButton { background: #f44336; border: 0; color: white; font-weight: bold; padding: 8px; }"
            "QPushButton:pressed { background: #c82333; padding-left: 10px; padding-top: 10px; }"
        )
        self._shutdown_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shutdown_btn.setIcon(icon_power_off)
        self._shutdown_btn.clicked.connect(self._on_shutdown_clicked)
        row.addWidget(self._shutdown_btn)

        self._restart_btn = QPushButton("Reiniciar Todo")
        self._restart_btn.setStyleSheet(
            "QPushButton { background: #FF9800; border: 0; color: white; font-weight: bold; padding: 8px; }"
            "QPushButton:pressed { background: #e0a800; padding-left: 10px; padding-top: 10px; }"
        )
        self._restart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restart_btn.setIcon(icon_restart)
        self._restart_btn.clicked.connect(self._on_restart_clicked)
        row.addWidget(self._restart_btn)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        self._lock_btn = QPushButton("Bloquear Estaciones")
        self._lock_btn.setStyleSheet(
            "QPushButton { background: #4CAF50; border: 0; color: white; font-weight: bold; padding: 8px; }"
            "QPushButton:pressed { background: #218838; padding-left: 10px; padding-top: 10px; }"
        )
        self._lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lock_btn.setIcon(icon_lock)
        self._lock_btn.clicked.connect(self._on_lock_clicked)
        row2.addWidget(self._lock_btn)
        self._logout_btn = QPushButton("Cerrar Sesión")
        self._logout_btn.setStyleSheet(
            "QPushButton { background: #00BCD4; border: 0; color: white; font-weight: bold; padding: 8px;}"
            "QPushButton:pressed { background: #138496; padding-left: 10px; padding-top: 10px; }"
        )
        self._logout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._logout_btn.setIcon(icon_logout)
        self._logout_btn.clicked.connect(self._on_logout_clicked)

        row2.addWidget(self._logout_btn)
        layout.addLayout(row2)

        form = QFormLayout()
        self._delay_spin = QSpinBox()
        self._delay_spin.setRange(0, MAX_DELAY_SECONDS)
        self._delay_spin.setValue(DEFAULT_DELAY_SECONDS)
        form.addRow("Delay (segundos):", self._delay_spin)
        layout.addLayout(form)

        layout.addWidget(QLabel("Aplicar a:"))
        self._all_radio = QRadioButton("Todos")
        self._all_radio.setChecked(True)
        self._all_radio.toggled.connect(self._toggle_client_list)
        layout.addWidget(self._all_radio)
        self._selected_radio = QRadioButton("Selección múltiple")
        layout.addWidget(self._selected_radio)

        self._client_list = QListWidget()
        self._client_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self._client_list.setEnabled(False)
        for client_id in self._client_ids:
            self._client_list.addItem(QListWidgetItem(client_id))
        layout.addWidget(self._client_list)
        return box

    def _build_remote_exec_section(self) -> QGroupBox:
        box = QGroupBox("🌐 Ejecución Remota")
        layout = QVBoxLayout(box)

        # Icons
        icon_open_url = qta.icon('fa6s.globe', color='#00BCD4')
        icon_browse = qta.icon('fa6s.folder-open', color='#FF9800')
        icon_run = qta.icon('fa6s.play', color='#4CAF50')

        url_row = QHBoxLayout()
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://...")
        url_row.addWidget(self._url_edit)
        self._open_url_btn = QPushButton("Abrir URL en todos")
        self._open_url_btn.setStyleSheet("border: 2px solid #00BCD4; color: #00BCD4")
        self._open_url_btn.setIcon(icon_open_url)
        self._open_url_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_url_btn.clicked.connect(self._on_open_url_clicked)
        url_row.addWidget(self._open_url_btn)
        layout.addLayout(url_row)

        quick_row = QHBoxLayout()
        for label, url in URL_SHORTCUTS.items():
            button = QPushButton(f"+ {label}")
            button.clicked.connect(lambda _, value=url: self._url_edit.setText(value))
            quick_row.addWidget(button)
        quick_row.addStretch()
        layout.addLayout(quick_row)

        app_row = QHBoxLayout()
        self._app_path_edit = QLineEdit()
        self._app_path_edit.setPlaceholderText(r"C:\Ruta\App.exe")
        app_row.addWidget(self._app_path_edit)
        browse_btn = QPushButton("")
        browse_btn.setProperty("class", "warning")
        browse_btn.setIcon(icon_browse)
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.clicked.connect(self._on_browse_app_clicked)
        app_row.addWidget(browse_btn)
        layout.addLayout(app_row)

        args_row = QHBoxLayout()
        args_row.addWidget(QLabel("Argumentos:"))
        self._app_args_edit = QLineEdit()
        self._app_args_edit.setPlaceholderText("--flag valor")
        args_row.addWidget(self._app_args_edit)
        self._run_app_btn = QPushButton("Ejecutar App")
        self._run_app_btn.setProperty("class", "success")
        self._run_app_btn.setIcon(icon_run)
        self._run_app_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._run_app_btn.clicked.connect(self._on_run_app_clicked)
        args_row.addWidget(self._run_app_btn)
        layout.addLayout(args_row)
        return box

    def _build_wol_section(self) -> QGroupBox:
        box = QGroupBox("📡 Wake-on-LAN")
        layout = QVBoxLayout(box)
        layout.addWidget(QLabel("Equipos desconectados con MAC conocida:"))

        # Icons
        icon_energy = qta.icon('fa6s.bolt', color='#FF9800')
        icon_energy_all = qta.icon('fa6s.plug', color='#4CAF50')

        self._offline_list = QListWidget()
        self._offline_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for client_id, mac in sorted(self._disconnected_clients.items()):
            item = QListWidgetItem(f"{client_id} — {mac}")
            item.setData(USER_ROLE, client_id)
            self._offline_list.addItem(item)
        layout.addWidget(self._offline_list)

        wol_row = QHBoxLayout()
        wol_selected = QPushButton("Encender seleccionados")
        wol_selected.setProperty("class", "warning")
        wol_selected.setIcon(icon_energy)
        wol_selected.setCursor(Qt.CursorShape.PointingHandCursor)
        wol_selected.clicked.connect(self._on_wol_selected_clicked)
        wol_row.addWidget(wol_selected)
        wol_all = QPushButton("Encender Todos")
        wol_all.setProperty("class", "success")
        wol_all.setIcon(icon_energy_all)
        wol_all.setCursor(Qt.CursorShape.PointingHandCursor)
        wol_all.clicked.connect(self._on_wol_all_clicked)
        wol_row.addWidget(wol_all)
        layout.addLayout(wol_row)

        manual_row = QHBoxLayout()
        self._manual_mac_edit = QLineEdit()
        self._manual_mac_edit.setPlaceholderText("AA:BB:CC:DD:EE:FF")
        manual_row.addWidget(self._manual_mac_edit)
        manual_btn = QPushButton("⚡ Enviar WoL")
        manual_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        manual_btn.clicked.connect(self._on_wol_manual_clicked)
        manual_row.addWidget(manual_btn)
        layout.addLayout(manual_row)
        return box

    def _selected_client_ids(self) -> list[str]:
        if self._all_radio.isChecked():
            return list(self._client_ids)
        return [item.text() for item in self._client_list.selectedItems()]

    def _selected_offline_client_ids(self) -> list[str]:
        client_ids: list[str] = []
        for item in self._offline_list.selectedItems():
            client_id = item.data(USER_ROLE)
            if client_id:
                client_ids.append(str(client_id))
        return client_ids

    @pyqtSlot(bool)
    def _toggle_client_list(self, all_selected: bool) -> None:
        self._client_list.setEnabled(not all_selected)

    @pyqtSlot()
    def _on_shutdown_clicked(self) -> None:
        targets = self._selected_client_ids()
        if not targets:
            QMessageBox.warning(self, "Sin selección", "Selecciona al menos un alumno.")
            return
        confirm = QMessageBox.question(
            self,
            "Confirmar apagado",
            "¿Seguro que deseas apagar los equipos seleccionados?",
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self._on_shutdown(targets, self._delay_spin.value())

    @pyqtSlot()
    def _on_restart_clicked(self) -> None:
        targets = self._selected_client_ids()
        if not targets:
            QMessageBox.warning(self, "Sin selección", "Selecciona al menos un alumno.")
            return
        confirm = QMessageBox.question(
            self,
            "Confirmar reinicio",
            "¿Seguro que deseas reiniciar los equipos seleccionados?",
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self._on_restart(targets, self._delay_spin.value())

    @pyqtSlot()
    def _on_lock_clicked(self) -> None:
        targets = self._selected_client_ids()
        if targets:
            self._on_lock(targets)

    @pyqtSlot()
    def _on_logout_clicked(self) -> None:
        targets = self._selected_client_ids()
        if targets:
            self._on_logout(targets)

    @pyqtSlot()
    def _on_open_url_clicked(self) -> None:
        targets = self._selected_client_ids()
        url = self._url_edit.text().strip()
        if not targets or not url:
            return
        self._on_open_url(targets, url)

    @pyqtSlot()
    def _on_run_app_clicked(self) -> None:
        targets = self._selected_client_ids()
        path = self._app_path_edit.text().strip()
        if not targets or not path:
            return
        args_text = self._app_args_edit.text().strip()
        args = shlex.split(args_text) if args_text else []
        self._on_run_app(targets, path, args)

    @pyqtSlot()
    def _on_browse_app_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar ejecutable",
            "",
            "Executables (*.exe);;Todos los archivos (*)",
        )
        if path:
            self._app_path_edit.setText(path)

    @pyqtSlot()
    def _on_wol_selected_clicked(self) -> None:
        selected = self._selected_offline_client_ids()
        if selected:
            self._on_wol_selected(selected)

    @pyqtSlot()
    def _on_wol_all_clicked(self) -> None:
        self._on_wol_all()

    @pyqtSlot()
    def _on_wol_manual_clicked(self) -> None:
        mac = self._manual_mac_edit.text().strip()
        if mac:
            self._on_wol_manual(mac)
