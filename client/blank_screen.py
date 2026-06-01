"""
client/blank_screen.py new
"""

from __future__ import annotations

import logging
import threading
import ctypes
import ctypes.wintypes
from typing import Optional

logger = logging.getLogger(__name__)

# Constantes Windows API
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_POPUP = 0x80000000
SW_SHOW = 5
COLOR_WINDOW = 5

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# --- Declarar tipos correctos para Windows x64 ---
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.c_size_t,    # WPARAM (64-bit)
    ctypes.c_ssize_t,   # LPARAM (64-bit)
]
user32.CreateWindowExW.restype = ctypes.wintypes.HWND
user32.CreateWindowExW.argtypes = [
    ctypes.wintypes.DWORD,
    ctypes.wintypes.LPCWSTR,
    ctypes.wintypes.LPCWSTR,
    ctypes.wintypes.DWORD,
    ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int,
    ctypes.wintypes.HWND,
    ctypes.wintypes.HMENU,
    ctypes.wintypes.HINSTANCE,
    ctypes.wintypes.LPVOID,
]
user32.PostMessageW.argtypes = [
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
]
# -------------------------------------------------


class BlankScreenOverlay:
    """Overlay negro nativo de Windows sin depender de Qt."""

    def __init__(self) -> None:
        self._active: bool = False
        self._thread: Optional[threading.Thread] = None
        self._hwnd: Optional[int] = None
        self._stop_event = threading.Event()

    def show(self, message: str = "Atención al frente") -> None:
        if self._active:
            return
        self._active = True
        self._stop_event.clear()
        logger.info("BlankScreenOverlay: showing overlay (message=%r)", message)
        self._thread = threading.Thread(
            target=self._run_overlay,
            args=(message,),
            daemon=True,
            name="dlslab-overlay",
        )
        self._thread.start()

    def hide(self) -> None:
        if not self._active:
            return
        self._active = False
        logger.info("BlankScreenOverlay: hiding overlay.")
        self._stop_event.set()
        if self._hwnd:
            try:
                user32.PostMessageW(self._hwnd, 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
        self._hwnd = None

    @property
    def active(self) -> bool:
        return self._active

    def _run_overlay(self, message: str) -> None:
        """Crea y muestra la ventana overlay en su propio hilo con message loop."""
        try:
            hinstance = kernel32.GetModuleHandleW(None)
            class_name = "DLSlabOverlay"

            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t,
                ctypes.wintypes.HWND,
                ctypes.c_uint,
                ctypes.c_size_t,
                ctypes.c_ssize_t,
            )

            def wnd_proc(hwnd, msg, wparam, lparam):
                WM_DESTROY     = 0x0002
                WM_PAINT       = 0x000F
                WM_KEYDOWN     = 0x0100
                WM_SYSKEYDOWN  = 0x0104
                WM_MOUSEMOVE   = 0x0200
                WM_LBUTTONDOWN = 0x0201
                WM_RBUTTONDOWN = 0x0204
                if msg == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
                elif msg == WM_PAINT:
                    ps = ctypes.create_string_buffer(64)
                    hdc = user32.BeginPaint(hwnd, ps)
                    rc = ctypes.wintypes.RECT()
                    user32.GetClientRect(hwnd, ctypes.byref(rc))
                    hbrush = gdi32.CreateSolidBrush(0x00000000)
                    user32.FillRect(hdc, ctypes.byref(rc), hbrush)
                    gdi32.DeleteObject(hbrush)
                    gdi32.SetTextColor(hdc, 0x00FFFFFF)
                    gdi32.SetBkMode(hdc, 1)
                    user32.DrawTextW(hdc, message, -1, ctypes.byref(rc), 0x25)
                    user32.EndPaint(hwnd, ps)
                    return 0
                elif msg in (WM_KEYDOWN, WM_SYSKEYDOWN, WM_MOUSEMOVE,
                             WM_LBUTTONDOWN, WM_RBUTTONDOWN):
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            wnd_proc_cb = WNDPROC(wnd_proc)

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style",         ctypes.c_uint),
                    ("lpfnWndProc",   WNDPROC),
                    ("cbClsExtra",    ctypes.c_int),
                    ("cbWndExtra",    ctypes.c_int),
                    ("hInstance",     ctypes.wintypes.HINSTANCE),
                    ("hIcon",         ctypes.wintypes.HICON),
                    ("hCursor",       ctypes.wintypes.HANDLE),
                    ("hbrBackground", ctypes.wintypes.HBRUSH),
                    ("lpszMenuName",  ctypes.wintypes.LPCWSTR),
                    ("lpszClassName", ctypes.wintypes.LPCWSTR),
                ]

            wc = WNDCLASSW()
            wc.lpfnWndProc   = wnd_proc_cb
            wc.hInstance     = hinstance
            wc.lpszClassName = class_name
            wc.hbrBackground = gdi32.CreateSolidBrush(0x00000000)
            user32.RegisterClassW(ctypes.byref(wc))

            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)

            hwnd = user32.CreateWindowExW(
                WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
                class_name, "DLSlab", WS_POPUP,
                0, 0, sw, sh,
                None, None, hinstance, None,
            )

            if not hwnd:
                logger.error("BlankScreenOverlay: no se pudo crear la ventana nativa.")
                return

            self._hwnd = hwnd
            user32.ShowWindow(hwnd, SW_SHOW)
            user32.UpdateWindow(hwnd)
            logger.info("BlankScreenOverlay: ventana nativa mostrada (%dx%d)", sw, sh)

            msg_struct = ctypes.wintypes.MSG()
            while not self._stop_event.is_set():
                result = user32.PeekMessageW(ctypes.byref(msg_struct), None, 0, 0, 1)
                if result:
                    if msg_struct.message == 0x0012:  # WM_QUIT
                        break
                    user32.TranslateMessage(ctypes.byref(msg_struct))
                    user32.DispatchMessageW(ctypes.byref(msg_struct))
                else:
                    self._stop_event.wait(timeout=0.016)

            user32.DestroyWindow(hwnd)
            user32.UnregisterClassW(class_name, hinstance)
            logger.info("BlankScreenOverlay: ventana nativa destruida.")

        except Exception as exc:
            logger.exception("BlankScreenOverlay: error en overlay nativo: %s", exc)