"""
client/power_manager.py
=======================
Windows power-management helpers for the DLSlab student agent.
"""

from __future__ import annotations

import ctypes
import subprocess
import uuid
import webbrowser

SHUTDOWN_EXE: str = "shutdown"
MAC_GROUP_BYTES: int = 8
MAC_BITS: int = 48


class PowerManager:
    """Utility methods for power/session control and remote app execution."""

    @staticmethod
    def shutdown(delay_seconds: int = 0) -> None:
        """Power off this machine after an optional delay."""
        subprocess.run(
            [SHUTDOWN_EXE, "/s", "/t", str(max(0, int(delay_seconds)))],
            check=False,
        )

    @staticmethod
    def restart(delay_seconds: int = 0) -> None:
        """Restart this machine after an optional delay."""
        subprocess.run(
            [SHUTDOWN_EXE, "/r", "/t", str(max(0, int(delay_seconds)))],
            check=False,
        )

    @staticmethod
    def logout() -> None:
        """Log out the currently active user session."""
        subprocess.run([SHUTDOWN_EXE, "/l"], check=False)

    @staticmethod
    def lock_workstation() -> None:
        """Lock the local workstation session (equivalent to Win+L)."""
        ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]

    @staticmethod
    def open_url(url: str) -> None:
        """Open a URL in the default browser."""
        webbrowser.open(url)

    @staticmethod
    def run_app(path: str, args: list[str] | None = None) -> None:
        """Start an application with optional arguments."""
        subprocess.Popen([path] + (args or []))  # noqa: S603,S607

    @staticmethod
    def get_mac_address() -> str:
        """Return the primary MAC address as ``AA:BB:CC:DD:EE:FF``."""
        mac_value = uuid.getnode()
        mac_parts = [
            f"{(mac_value >> bit_offset) & 0xFF:02X}"
            for bit_offset in range(0, MAC_BITS, MAC_GROUP_BYTES)
        ][::-1]
        return ":".join(mac_parts)

