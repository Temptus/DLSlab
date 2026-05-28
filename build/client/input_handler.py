"""
client/input_handler.py
=======================
Receives REMOTE_INPUT messages from the DLSlab server and executes the
corresponding mouse / keyboard actions on the local Windows desktop using
``pynput``.

Supported event types
---------------------
- ``mouse_move``  — move cursor to an absolute screen position.
- ``mouse_click`` — press or release a mouse button.
- ``key_press``   — press a keyboard key.
- ``key_release`` — release a keyboard key.

Usage example::

    from client.input_handler import InputHandler

    handler = InputHandler()
    handler.handle_event("mouse_move", {"x": 100, "y": 200})
    handler.handle_event("key_press", {"key": "a"})
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from pynput import keyboard as kb
    from pynput import mouse as ms
    _PYNPUT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYNPUT_AVAILABLE = False

logger = logging.getLogger(__name__)

# Mapping from button name strings (sent over the wire) to pynput Button values.
_BUTTON_MAP: dict[str, Any] = {}
if _PYNPUT_AVAILABLE:
    _BUTTON_MAP = {
        "left": ms.Button.left,
        "right": ms.Button.right,
        "middle": ms.Button.middle,
    }


class InputHandler:
    """Translates REMOTE_INPUT payloads into local mouse / keyboard events.

    This class is intentionally stateless — each call to :meth:`handle_event`
    is independent.

    Raises:
        RuntimeError: On construction if ``pynput`` is not installed.
    """

    def __init__(self) -> None:
        if not _PYNPUT_AVAILABLE:
            raise RuntimeError(
                "pynput is required for remote input. "
                "Install it with: pip install pynput"
            )
        self._mouse = ms.Controller()
        self._keyboard = kb.Controller()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle_event(self, event_type: str, event_data: dict[str, Any]) -> None:
        """Dispatch a single remote input event to the local desktop.

        Args:
            event_type:  One of ``mouse_move``, ``mouse_click``,
                         ``key_press``, ``key_release``.
            event_data:  Dictionary of parameters specific to *event_type*.
        """
        dispatch = {
            "mouse_move": self._mouse_move,
            "mouse_click": self._mouse_click,
            "key_press": self._key_press,
            "key_release": self._key_release,
        }
        handler = dispatch.get(event_type)
        if handler is None:
            logger.warning("Unknown input event type: %s", event_type)
            return
        try:
            handler(event_data)
        except Exception as exc:
            logger.exception("Error handling input event '%s': %s", event_type, exc)

    # ------------------------------------------------------------------
    # Mouse helpers
    # ------------------------------------------------------------------

    def _mouse_move(self, data: dict[str, Any]) -> None:
        """Move the cursor to an absolute position.

        Args:
            data: Must contain integer keys ``x`` and ``y``.
        """
        x: int = int(data["x"])
        y: int = int(data["y"])
        self._mouse.position = (x, y)
        logger.debug("mouse_move -> (%d, %d)", x, y)

    def _mouse_click(self, data: dict[str, Any]) -> None:
        """Press or release a mouse button at an absolute position.

        Args:
            data: Must contain:
                - ``x`` (int): horizontal position.
                - ``y`` (int): vertical position.
                - ``button`` (str): ``"left"``, ``"right"``, or ``"middle"``.
                - ``pressed`` (bool): ``True`` to press, ``False`` to release.
        """
        x: int = int(data["x"])
        y: int = int(data["y"])
        button_str: str = data.get("button", "left")
        pressed: bool = bool(data.get("pressed", True))

        button = _BUTTON_MAP.get(button_str, ms.Button.left)
        self._mouse.position = (x, y)
        if pressed:
            self._mouse.press(button)
        else:
            self._mouse.release(button)
        logger.debug(
            "mouse_click -> (%d, %d) button=%s pressed=%s", x, y, button_str, pressed
        )

    # ------------------------------------------------------------------
    # Keyboard helpers
    # ------------------------------------------------------------------

    def _key_press(self, data: dict[str, Any]) -> None:
        """Press a keyboard key.

        Args:
            data: Must contain ``key`` (str) — either a single character
                  (e.g. ``"a"``) or a special key name prefixed with ``Key.``
                  (e.g. ``"Key.enter"``).
        """
        key = self._resolve_key(data.get("key", ""))
        if key is None:
            return
        self._keyboard.press(key)
        logger.debug("key_press -> %s", key)

    def _key_release(self, data: dict[str, Any]) -> None:
        """Release a keyboard key.

        Args:
            data: Must contain ``key`` (str) — same format as in
                  :meth:`_key_press`.
        """
        key = self._resolve_key(data.get("key", ""))
        if key is None:
            return
        self._keyboard.release(key)
        logger.debug("key_release -> %s", key)

    # ------------------------------------------------------------------
    # Key resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_key(key_str: str) -> "kb.Key | str | None":
        """Convert a key string to a pynput key value.

        Args:
            key_str: A single character or a ``Key.<name>`` string.

        Returns:
            A :class:`pynput.keyboard.Key` enum member for special keys,
            a plain string for regular characters, or ``None`` on failure.
        """
        if not key_str:
            logger.warning("_resolve_key: empty key string.")
            return None

        # Special key: "Key.enter", "Key.space", etc.
        if key_str.startswith("Key."):
            key_name = key_str[4:]
            key_value = getattr(kb.Key, key_name, None)
            if key_value is None:
                logger.warning("Unknown special key: %s", key_str)
            return key_value

        # Regular character key.
        return key_str
