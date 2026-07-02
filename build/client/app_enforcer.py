"""
client/app_enforcer.py
======================
Windows process policy enforcer for DLSlab.

Supports whitelist and blacklist modes based on process names.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import psutil

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

PROCESS_SCAN_INTERVAL_SECONDS: float = 2.0
SYSTEM_PROCESS_EXCLUSIONS: set[str] = {
    # Kernel / Session Manager
    "System",
    "Registry",
    "Idle",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "LogonUI.exe",
    # Servicios
    "services.exe",
    "lsass.exe",
    "lsaiso.exe",
    "svchost.exe",
    "spoolsv.exe",
    "dllhost.exe",
    "WmiPrvSE.exe",
    "WUDFHost.exe",
    "wermgr.exe",
    # Shell / UI
    "explorer.exe",
    "dwm.exe",
    "taskhostw.exe",
    "taskmgr.exe",
    "sihost.exe",
    "ShellExperienceHost.exe",
    "StartMenuExperienceHost.exe",
    "SearchUI.exe",
    "SearchApp.exe",
    "SearchIndexer.exe",
    "ctfmon.exe",
    "fontdrvhost.exe",
    "UserOOBEBroker.exe",
    # Runtime / seguridad
    "RuntimeBroker.exe",
    "SecurityHealthService.exe",
    "SecurityHealthSystray.exe",
    "MsMpEng.exe",
    "NisSrv.exe",
    "SgrmBroker.exe",
    # Audio / dispositivos
    "audiodg.exe",
    "conhost.exe",
    # Python (agente DLSlab)
    "python.exe",
    "pythonw.exe",
    # Agente propio compilado con PyInstaller
    "agent.exe",
}

# Versión en minúsculas para comparaciones case-insensitive
SYSTEM_PROCESS_EXCLUSIONS_LOWER: set[str] = {p.lower() for p in SYSTEM_PROCESS_EXCLUSIONS}


class AppEnforcer:
    """Monitor and enforce process execution policies on Windows.

    Args:
        on_violation: Optional callback called when a process is terminated.
                      Signature: ``(process_name, mode) -> None``.
    """

    def __init__(
        self,
        on_violation: Callable[[str, str], None] | None = None,
    ) -> None:
        self._on_violation = on_violation
        self._mode: str | None = None
        self._apps: set[str] = set()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def is_active(self) -> bool:
        """Return ``True`` if monitoring thread is currently running."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def mode(self) -> str | None:
        """Return the active policy mode."""
        with self._lock:
            return self._mode

    @property
    def apps(self) -> list[str]:
        """Return current policy process names."""
        with self._lock:
            return sorted(self._apps)

    def set_whitelist(self, apps: list[str]) -> None:
        """Allow only the given process names."""
        with self._lock:
            self._mode = "whitelist"
            self._apps = {app.strip().lower() for app in apps if app.strip()}
        logger.info("App whitelist configured with %d process(es).", len(self._apps))

    def set_blacklist(self, apps: list[str]) -> None:
        """Block the given process names."""
        with self._lock:
            self._mode = "blacklist"
            self._apps = {app.strip().lower() for app in apps if app.strip()}
        logger.info("App blacklist configured with %d process(es).", len(self._apps))

    def clear_policy(self) -> None:
        """Remove active app policy."""
        with self._lock:
            self._mode = None
            self._apps.clear()
        logger.info("App policy cleared.")

    def start(self) -> None:
        """Start process monitoring."""
        if self.is_active:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="dlslab-app-enforcer",
        )
        self._thread.start()
        logger.info("App enforcer started.")

    def stop(self) -> None:
        """Stop process monitoring."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        logger.info("App enforcer stopped.")

    def _monitor_loop(self) -> None:
        """Continuously inspect and terminate policy-violating processes."""
        while not self._stop_event.is_set():
            with self._lock:
                mode = self._mode
                apps = set(self._apps)

            if mode and apps:
                self._enforce(mode, apps)

            self._stop_event.wait(PROCESS_SCAN_INTERVAL_SECONDS)

    def _enforce(self, mode: str, apps: set[str]) -> None:
        """Apply one scan cycle using the current policy."""
        for process in psutil.process_iter(attrs=["pid", "name"]):
            process_name = process.info.get("name") or ""
            if not process_name:
                continue

            process_name_l = process_name.lower()
            if process_name in SYSTEM_PROCESS_EXCLUSIONS:
                continue
            if process_name_l in SYSTEM_PROCESS_EXCLUSIONS_LOWER:
                continue

            should_kill = (
                mode == "whitelist" and process_name_l not in apps
            ) or (
                mode == "blacklist" and process_name_l in apps
            )
            if not should_kill:
                continue

            try:
                process.terminate()
                process.wait(timeout=1.5)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                continue

            logger.warning(
                "App policy violation (%s): terminated process %s (pid=%s).",
                mode,
                process_name,
                process.info.get("pid"),
            )
            if self._on_violation:
                self._on_violation(process_name, mode)

