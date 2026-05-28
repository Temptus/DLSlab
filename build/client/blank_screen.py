"""
client/blank_screen.py
======================
Fullscreen black overlay that blacks out all monitors on the student PC,
blocks all keyboard and mouse input at the system level (using pynput with
``suppress=True``), and displays a customisable message.

The overlay is implemented with PyQt6 — one borderless, always-on-top
:class:`QWidget` per connected monitor — and runs in the Qt main thread
(via :func:`QMetaObject.invokeMethod`) to keep it safe for asyncio agents.

Input blocking uses :mod:`pynput` listeners in background threads.

Typical usage inside the agent
-------------------------------
::

    overlay = BlankScreenOverlay()
    overlay.show("Atención al frente")   # blocks input, shows message
    ...
    overlay.hide()                        # restores input, removes windows
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, QVariant
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

OVERLAY_FONT_SIZE: int = 36          # pt — message text size
OVERLAY_BACKGROUND: str = "#000000"  # black background
OVERLAY_TEXT_COLOR: str = "#ffffff"  # white text
OVERLAY_OPACITY: float = 1.0         # fully opaque

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BlankScreenOverlay
# ---------------------------------------------------------------------------

class BlankScreenOverlay:
    """Manages fullscreen black overlays across all monitors.

    The class creates one :class:`QWidget` per screen returned by
    :func:`QApplication.screens()`.  Each widget is frameless and stays
    on top of all other windows, including the Windows taskbar.

    Input blocking is achieved through :mod:`pynput` listeners started in
    daemon threads with ``suppress=True``, which intercepts events at the
    OS hook level before they reach any other application.

    Note:
        A :class:`QApplication` instance must exist before instantiating
        this class (the agent's Qt loop or the teacher UI provides one).
        On headless systems where Qt is unavailable, the class degrades
        gracefully by logging a warning and disabling overlay windows.
    """

    def __init__(self) -> None:
        self._overlays: list[QWidget] = []
        self._kb_listener: Optional[threading.Thread] = None
        self._ms_listener: Optional[threading.Thread] = None
        self._active: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(self, message: str = "Atención al frente") -> None:
        """Display the blank-screen overlay on all monitors and block input.

        Safe to call from any thread; the Qt widgets are created/shown in
        the Qt main thread via :func:`QMetaObject.invokeMethod`.

        Args:
            message: Text displayed centred on the black overlay.
        """
        if self._active:
            logger.debug("BlankScreenOverlay already active — ignoring show().")
            return

        self._active = True
        logger.info("BlankScreenOverlay: showing overlay (message=%r)", message)

        # Build and show Qt windows from the main Qt thread.
        app = QApplication.instance()
        if app is None:
            logger.warning(
                "BlankScreenOverlay: no QApplication instance — overlay skipped."
            )
        else:
            self._create_overlays(message)

        # Start input blocking listeners in background threads.
        self._start_input_block()

    def hide(self) -> None:
        """Remove the blank-screen overlays and restore normal input.

        Safe to call from any thread.
        """
        if not self._active:
            logger.debug("BlankScreenOverlay already inactive — ignoring hide().")
            return

        self._active = False
        logger.info("BlankScreenOverlay: hiding overlay.")

        # Stop input block first so the teacher can interact normally.
        self._stop_input_block()

        # Destroy Qt windows from the main Qt thread.
        self._destroy_overlays()

    @property
    def active(self) -> bool:
        """``True`` if the overlay is currently displayed."""
        return self._active

    # ------------------------------------------------------------------
    # Overlay creation / destruction
    # ------------------------------------------------------------------

    def _create_overlays(self, message: str) -> None:
        """Create one fullscreen overlay widget per connected monitor.

        Called in the Qt main thread (or proxied to it when invoked from
        a non-Qt thread via :func:`_run_in_qt_thread`).

        Args:
            message: Text to centre on each overlay.
        """
        screens = QApplication.screens()
        if not screens:
            logger.warning("BlankScreenOverlay: no screens found.")
            return

        for screen in screens:
            overlay = _build_overlay_widget(message, screen.geometry())
            self._overlays.append(overlay)
            overlay.show()
            overlay.raise_()
            overlay.activateWindow()

        logger.debug(
            "BlankScreenOverlay: %d overlay(s) created.", len(self._overlays)
        )

    def _destroy_overlays(self) -> None:
        """Close and delete all overlay widgets."""
        for overlay in self._overlays:
            try:
                overlay.hide()
                overlay.close()
                overlay.deleteLater()
            except Exception as exc:  # pragma: no cover
                logger.debug("Error closing overlay: %s", exc)
        self._overlays.clear()
        logger.debug("BlankScreenOverlay: all overlays destroyed.")

    # ------------------------------------------------------------------
    # Input blocking
    # ------------------------------------------------------------------

    def _start_input_block(self) -> None:
        """Start pynput keyboard and mouse listeners with suppress=True.

        Each listener runs in its own daemon thread so it does not block
        the asyncio event loop.
        """
        try:
            from pynput import keyboard as kb_mod, mouse as ms_mod
        except ImportError:
            logger.warning(
                "pynput not installed — input blocking disabled. "
                "Install it with: pip install pynput"
            )
            return

        # Keyboard listener — suppress all key events.
        self._kb_listener = threading.Thread(
            target=self._run_keyboard_listener,
            args=(kb_mod,),
            daemon=True,
            name="dlslab-kb-block",
        )
        self._kb_listener.start()

        # Mouse listener — suppress all mouse events.
        self._ms_listener = threading.Thread(
            target=self._run_mouse_listener,
            args=(ms_mod,),
            daemon=True,
            name="dlslab-ms-block",
        )
        self._ms_listener.start()

        logger.info("BlankScreenOverlay: input blocking started.")

    def _stop_input_block(self) -> None:
        """Stop the pynput listeners if they are running."""
        # Listeners are stored as attributes on the thread objects; stop them.
        for attr in ("_kb_listener_obj", "_ms_listener_obj"):
            listener = getattr(self, attr, None)
            if listener is not None:
                try:
                    listener.stop()
                except Exception as exc:
                    logger.debug("Error stopping listener: %s", exc)
                setattr(self, attr, None)

        self._kb_listener = None
        self._ms_listener = None
        logger.info("BlankScreenOverlay: input blocking stopped.")

    def _run_keyboard_listener(self, kb_mod: object) -> None:
        """Target for the keyboard-blocking daemon thread.

        Args:
            kb_mod: The ``pynput.keyboard`` module (passed in to avoid a
                    circular import at module level).
        """
        listener = kb_mod.Listener(  # type: ignore[attr-defined]
            on_press=lambda key: False,
            on_release=lambda key: False,
            suppress=True,
        )
        self._kb_listener_obj = listener
        listener.start()
        listener.join()

    def _run_mouse_listener(self, ms_mod: object) -> None:
        """Target for the mouse-blocking daemon thread.

        Args:
            ms_mod: The ``pynput.mouse`` module (passed in to avoid a
                    circular import at module level).
        """
        listener = ms_mod.Listener(  # type: ignore[attr-defined]
            on_move=lambda x, y: False,
            on_click=lambda x, y, button, pressed: False,
            on_scroll=lambda x, y, dx, dy: False,
            suppress=True,
        )
        self._ms_listener_obj = listener
        listener.start()
        listener.join()


# ---------------------------------------------------------------------------
# Helper: build one overlay window
# ---------------------------------------------------------------------------

def _build_overlay_widget(message: str, geometry: "QRect") -> QWidget:  # type: ignore[name-defined]  # noqa: F821
    """Create a single fullscreen black overlay widget for the given geometry.

    Args:
        message:  Text to display centred in the widget.
        geometry: Screen geometry (position + size) obtained from
                  :meth:`QScreen.geometry`.

    Returns:
        A :class:`QWidget` configured as a frameless, always-on-top,
        fullscreen black window — **not yet shown**.
    """
    overlay = QWidget()
    overlay.setWindowFlags(
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.Tool
    )
    overlay.setGeometry(geometry)
    overlay.setStyleSheet(f"background-color: {OVERLAY_BACKGROUND};")
    overlay.setWindowOpacity(OVERLAY_OPACITY)

    # Centred message label.
    label = QLabel(message, overlay)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setGeometry(0, 0, geometry.width(), geometry.height())

    font = QFont()
    font.setPointSize(OVERLAY_FONT_SIZE)
    font.setBold(True)
    label.setFont(font)
    label.setStyleSheet(f"color: {OVERLAY_TEXT_COLOR}; background: transparent;")
    label.setWordWrap(True)

    return overlay
