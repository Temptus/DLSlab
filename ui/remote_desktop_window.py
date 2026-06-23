"""
ui/remote_desktop_window.py
===========================
Ventana de escritorio remoto para el profesor.

Muestra el stream de alta resolución de un estudiante y permite al profesor
controlar la PC del estudiante enviando eventos de mouse y teclado a través
del servidor DLSlab.

Arquitectura
------------
1. El servidor solicita al cliente ``REQUEST_HIRES_SCREENSHOT``.
2. Los frames llegan al servidor y se redirigen mediante el callback
   ``on_remote_input`` a esta ventana.
3. La ventana escala el frame al tamaño del widget y lo muestra.
4. Los eventos de mouse/teclado del profesor se mapean a coordenadas
   absolutas de la pantalla del estudiante y se envían como
   ``REMOTE_INPUT`` al servidor, que los reenvía al cliente.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QCloseEvent,
    QKeyEvent,
    QMouseEvent,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Dimensiones del frame hires tal como las envía el cliente (client/agent.py)
HIRES_WIDTH: int = 1280
HIRES_HEIGHT: int = 720


class RemoteDesktopWindow(QWidget):
    """Ventana de escritorio remoto del profesor.

    Muestra el stream en vivo de un estudiante y permite controlar su PC
    mediante mouse y teclado.

    Args:
        hostname:        Nombre del equipo del estudiante (para el título).
        screen_width:    Resolución horizontal real del monitor del estudiante.
                         Se usa para mapear coordenadas del widget a la pantalla
                         real.  Si es 0 se usa ``HIRES_WIDTH`` como fallback.
        screen_height:   Resolución vertical real del monitor del estudiante.
        on_remote_input: Callable ``(event_type, event_data)`` invocado cada
                         vez que el profesor genera un evento de mouse o teclado.
        on_close:        Callable sin argumentos invocado al cerrar la ventana,
                         para que el caller detenga el stream hires.
        parent:          Widget padre opcional.
    """

    #: Emitida justo antes de que la ventana se cierre.
    closed = pyqtSignal()

    def __init__(
        self,
        hostname: str,
        screen_width: int,
        screen_height: int,
        on_remote_input: Callable[[str, dict[str, Any]], None],
        on_close: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self._hostname = hostname
        self._screen_width: int = screen_width if screen_width > 0 else HIRES_WIDTH
        self._screen_height: int = screen_height if screen_height > 0 else HIRES_HEIGHT
        self._on_remote_input = on_remote_input
        self._on_close_cb = on_close
        self._control_enabled: bool = True

        self.setWindowTitle(f"🖥️  Control Remoto — {hostname}")
        self.setMinimumSize(800, 500)
        self.resize(HIRES_WIDTH, HIRES_HEIGHT + 56)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._setup_ui()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Construye el layout: barra de info + área del frame."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Barra superior ---
        bar = QWidget()
        bar.setStyleSheet("background-color: #1a237e;")
        bar.setFixedHeight(48)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(10, 6, 10, 6)
        bar_layout.setSpacing(8)

        info = QLabel(
            f"🖥️  <b>{self._hostname}</b>"
            f"  —  resolución: {self._screen_width}×{self._screen_height}"
            "  —  Haz clic en la imagen para enviar inputs"
        )
        info.setStyleSheet("color: white; font-size: 13px;")
        bar_layout.addWidget(info)
        bar_layout.addStretch()

        self._toggle_btn = QPushButton("⏸  Pausar control")
        self._toggle_btn.setFixedHeight(30)
        self._toggle_btn.setStyleSheet(self._btn_style("#e53935", "#ff5252"))
        self._toggle_btn.clicked.connect(self._toggle_control)
        bar_layout.addWidget(self._toggle_btn)

        close_btn = QPushButton("✖  Cerrar")
        close_btn.setFixedHeight(30)
        close_btn.setStyleSheet(self._btn_style("#555555", "#888888"))
        close_btn.clicked.connect(self.close)
        bar_layout.addWidget(close_btn)

        layout.addWidget(bar)

        # --- Área del frame ---
        self._frame_label = QLabel()
        self._frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._frame_label.setStyleSheet("background-color: #000000;")
        self._frame_label.setMouseTracking(True)
        layout.addWidget(self._frame_label, stretch=1)

    @staticmethod
    def _btn_style(normal: str, hover: str) -> str:
        return (
            f"QPushButton {{ background: {normal}; color: white; border-radius: 4px;"
            f" padding: 4px 12px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {hover}; }}"
        )

    # ------------------------------------------------------------------
    # Public API — actualizar frame
    # ------------------------------------------------------------------

    def update_frame(self, frame_b64: str) -> None:
        """Mostrar un nuevo frame (llamado desde el hilo Qt principal).

        Args:
            frame_b64: Frame JPEG codificado en base64.
        """
        try:
            data = base64.b64decode(frame_b64)
            pixmap = QPixmap()
            pixmap.loadFromData(data, "JPEG")
            scaled = pixmap.scaled(
                self._frame_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._frame_label.setPixmap(scaled)
        except Exception as exc:
            logger.exception("Error al actualizar frame de control remoto: %s", exc)

    # ------------------------------------------------------------------
    # Coordinate mapping
    # ------------------------------------------------------------------

    def _widget_to_student_coords(self, wx: int, wy: int) -> tuple[int, int]:
        """Mapear coordenadas del widget a coordenadas absolutas del estudiante.

        El frame JPEG tiene tamaño ``HIRES_WIDTH × HIRES_HEIGHT`` pero se
        muestra escalado con ``KeepAspectRatio``.  Este método calcula el
        área efectiva del pixmap dentro del QLabel y mapea a la resolución
        real del estudiante.

        Args:
            wx: Coordenada X dentro del ``_frame_label``.
            wy: Coordenada Y dentro del ``_frame_label``.

        Returns:
            Tupla ``(x, y)`` en coordenadas absolutas de la pantalla del
            estudiante.
        """
        lw = self._frame_label.width()
        lh = self._frame_label.height()
        if lw <= 0 or lh <= 0:
            return (0, 0)

        frame_aspect = HIRES_WIDTH / HIRES_HEIGHT
        label_aspect = lw / lh

        if frame_aspect > label_aspect:
            # Barras arriba y abajo (letterbox)
            disp_w = lw
            disp_h = int(lw / frame_aspect)
            ox, oy = 0, (lh - disp_h) // 2
        else:
            # Barras izquierda y derecha (pillarbox)
            disp_h = lh
            disp_w = int(lh * frame_aspect)
            ox, oy = (lw - disp_w) // 2, 0

        if disp_w <= 0 or disp_h <= 0:
            return (0, 0)

        fx = wx - ox
        fy = wy - oy
        sx = int(fx * self._screen_width / disp_w)
        sy = int(fy * self._screen_height / disp_h)

        # Clamp a los límites de la pantalla del estudiante
        sx = max(0, min(sx, self._screen_width - 1))
        sy = max(0, min(sy, self._screen_height - 1))
        return (sx, sy)

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._control_enabled:
            return
        self.setFocus()
        pos = self._frame_label.mapFromParent(event.pos())
        sx, sy = self._widget_to_student_coords(pos.x(), pos.y())
        btn = self._qt_button_to_str(event.button())
        self._on_remote_input(
            "mouse_click", {"x": sx, "y": sy, "button": btn, "pressed": True}
        )

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._control_enabled:
            return
        pos = self._frame_label.mapFromParent(event.pos())
        sx, sy = self._widget_to_student_coords(pos.x(), pos.y())
        btn = self._qt_button_to_str(event.button())
        self._on_remote_input(
            "mouse_click", {"x": sx, "y": sy, "button": btn, "pressed": False}
        )

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._control_enabled:
            return
        pos = self._frame_label.mapFromParent(event.pos())
        sx, sy = self._widget_to_student_coords(pos.x(), pos.y())
        self._on_remote_input("mouse_move", {"x": sx, "y": sy})

    @staticmethod
    def _qt_button_to_str(button: Qt.MouseButton) -> str:
        mapping = {
            Qt.MouseButton.LeftButton:   "left",
            Qt.MouseButton.RightButton:  "right",
            Qt.MouseButton.MiddleButton: "middle",
        }
        return mapping.get(button, "left")

    # ------------------------------------------------------------------
    # Keyboard events
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._control_enabled:
            return
        key_str = self._qt_key_to_pynput(event)
        if key_str:
            self._on_remote_input("key_press", {"key": key_str})

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._control_enabled:
            return
        key_str = self._qt_key_to_pynput(event)
        if key_str:
            self._on_remote_input("key_release", {"key": key_str})

    @staticmethod
    def _qt_key_to_pynput(event: QKeyEvent) -> str | None:
        """Convertir un QKeyEvent a una cadena compatible con pynput.

        Para teclas especiales devuelve ``"Key.<nombre>"``; para caracteres
        imprimibles devuelve el carácter tal cual.

        Args:
            event: Evento de teclado de Qt.

        Returns:
            Cadena de tecla para pynput, o ``None`` si no hay mapping.
        """
        special: dict[Qt.Key, str] = {
            Qt.Key.Key_Return:    "Key.enter",
            Qt.Key.Key_Enter:     "Key.enter",
            Qt.Key.Key_Backspace: "Key.backspace",
            Qt.Key.Key_Delete:    "Key.delete",
            Qt.Key.Key_Escape:    "Key.esc",
            Qt.Key.Key_Tab:       "Key.tab",
            Qt.Key.Key_Space:     "Key.space",
            Qt.Key.Key_Left:      "Key.left",
            Qt.Key.Key_Right:     "Key.right",
            Qt.Key.Key_Up:        "Key.up",
            Qt.Key.Key_Down:      "Key.down",
            Qt.Key.Key_Home:      "Key.home",
            Qt.Key.Key_End:       "Key.end",
            Qt.Key.Key_PageUp:    "Key.page_up",
            Qt.Key.Key_PageDown:  "Key.page_down",
            Qt.Key.Key_Insert:    "Key.insert",
            Qt.Key.Key_F1:        "Key.f1",
            Qt.Key.Key_F2:        "Key.f2",
            Qt.Key.Key_F3:        "Key.f3",
            Qt.Key.Key_F4:        "Key.f4",
            Qt.Key.Key_F5:        "Key.f5",
            Qt.Key.Key_F6:        "Key.f6",
            Qt.Key.Key_F7:        "Key.f7",
            Qt.Key.Key_F8:        "Key.f8",
            Qt.Key.Key_F9:        "Key.f9",
            Qt.Key.Key_F10:       "Key.f10",
            Qt.Key.Key_F11:       "Key.f11",
            Qt.Key.Key_F12:       "Key.f12",
            Qt.Key.Key_Control:   "Key.ctrl_l",
            Qt.Key.Key_Shift:     "Key.shift",
            Qt.Key.Key_Alt:       "Key.alt_l",
            Qt.Key.Key_Meta:      "Key.cmd",
            Qt.Key.Key_CapsLock:  "Key.caps_lock",
            Qt.Key.Key_Print:     "Key.print_screen",
        }
        key_enum = Qt.Key(event.key())
        if key_enum in special:
            return special[key_enum]
        text = event.text()
        if text and text.isprintable():
            return text
        return None

    # ------------------------------------------------------------------
    # Toggle control mode
    # ------------------------------------------------------------------

    def _toggle_control(self) -> None:
        """Alternar entre modo activo (inputs habilitados) y modo solo vista."""
        self._control_enabled = not self._control_enabled
        if self._control_enabled:
            self._toggle_btn.setText("⏸  Pausar control")
            self._toggle_btn.setStyleSheet(self._btn_style("#e53935", "#ff5252"))
        else:
            self._toggle_btn.setText("▶  Reanudar control")
            self._toggle_btn.setStyleSheet(self._btn_style("#2e7d32", "#43a047"))

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Notificar al caller para detener el stream y emitir la señal."""
        try:
            self._on_close_cb()
        except Exception as exc:
            logger.warning("Error en on_close callback de control remoto: %s", exc)
        self.closed.emit()
        super().closeEvent(event)
