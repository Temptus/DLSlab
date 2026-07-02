"""
client/web_enforcer.py
======================
Windows web access enforcer for DLSlab.

Implements:
1) Browser process blocking (block_all mode).
2) URL whitelist policy via a local HTTP/HTTPS proxy that is registered as
   the Windows system proxy through the registry — so Chrome, Edge and Firefox
   use it automatically without any per-browser configuration.

Architecture
------------
* ``set_url_whitelist(urls)``
    - Starts a local ``ThreadingHTTPServer`` on 127.0.0.1:8877.
    - Saves the current Windows proxy settings.
    - Sets the Windows registry proxy to 127.0.0.1:8877 and notifies WinINet.
    - HTTP requests are forwarded only for allowlisted domains (403 otherwise).
    - HTTPS CONNECT tunnels are established only for allowlisted domains.

* ``clear_web_policy()``
    - Shuts down the proxy server.
    - Restores the previously saved Windows proxy settings.

* ``block_browsers()`` (block_all mode)
    - Blacklists all browser processes via AppEnforcer.
    - Terminates any currently running browsers.
"""

from __future__ import annotations

import atexit
import ctypes
import logging
import os
import select
import socket
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import psutil

from client.app_enforcer import AppEnforcer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

BROWSER_PROCESSES: list[str] = [
    "chrome.exe",
    "firefox.exe",
    "msedge.exe",
    "opera.exe",
    "brave.exe",
    "iexplore.exe",
    "safari.exe",
]

PROXY_HOST: str = "127.0.0.1"
PROXY_PORT: int = 8877

_INET_SETTINGS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
_INET_OPTION_SETTINGS_CHANGED = 39
_INET_OPTION_REFRESH = 37

# Seconds between repeated violation reports for the same domain (anti-spam)
_VIOLATION_DEBOUNCE_S: float = 5.0

# ---------------------------------------------------------------------------
# Windows registry / WinINet helpers
# ---------------------------------------------------------------------------

def _read_proxy_settings() -> dict:
    """Return the current Windows proxy settings from HKCU registry."""
    if os.name != "nt":
        return {}
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INET_SETTINGS_KEY, 0, winreg.KEY_READ)

        def _get(name: str, default):
            try:
                val, _ = winreg.QueryValueEx(key, name)
                return val
            except FileNotFoundError:
                return default

        result = {
            "ProxyEnable":   _get("ProxyEnable",   0),
            "ProxyServer":   _get("ProxyServer",   ""),
            "ProxyOverride": _get("ProxyOverride", ""),
        }
        winreg.CloseKey(key)
        return result
    except Exception as exc:
        logger.warning("_read_proxy_settings failed: %s", exc)
        return {}


def _write_proxy_settings(settings: dict) -> None:
    """Write a proxy settings dict back to HKCU registry and refresh WinINet."""
    if os.name != "nt" or not settings:
        return
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INET_SETTINGS_KEY, 0, winreg.KEY_WRITE)
        winreg.SetValueEx(key, "ProxyEnable",   0, winreg.REG_DWORD, settings.get("ProxyEnable",   0))
        winreg.SetValueEx(key, "ProxyServer",   0, winreg.REG_SZ,    settings.get("ProxyServer",   ""))
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ,    settings.get("ProxyOverride", ""))
        winreg.CloseKey(key)
        _refresh_wininet()
    except Exception as exc:
        logger.warning("_write_proxy_settings failed: %s", exc)


def _enable_system_proxy(host: str, port: int) -> None:
    """Point the Windows system proxy to host:port and enable it."""
    if os.name != "nt":
        return
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INET_SETTINGS_KEY, 0, winreg.KEY_WRITE)
        winreg.SetValueEx(key, "ProxyEnable",   0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "ProxyServer",   0, winreg.REG_SZ,    f"{host}:{port}")
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ,    "<local>")
        winreg.CloseKey(key)
        _refresh_wininet()
        logger.info("Windows system proxy → %s:%d", host, port)
    except Exception as exc:
        logger.warning("_enable_system_proxy failed: %s", exc)


def _disable_system_proxy() -> None:
    """Disable the Windows system proxy."""
    if os.name != "nt":
        return
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INET_SETTINGS_KEY, 0, winreg.KEY_WRITE)
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(key)
        _refresh_wininet()
        logger.info("Windows system proxy disabled.")
    except Exception as exc:
        logger.warning("_disable_system_proxy failed: %s", exc)


def _refresh_wininet() -> None:
    """Notify WinINet (and therefore all browsers) that proxy settings changed."""
    try:
        ctypes.windll.wininet.InternetSetOptionW(None, _INET_OPTION_SETTINGS_CHANGED, None, 0)
        ctypes.windll.wininet.InternetSetOptionW(None, _INET_OPTION_REFRESH,          None, 0)
    except Exception as exc:
        logger.debug("_refresh_wininet failed: %s", exc)


# ---------------------------------------------------------------------------
# Proxy request handler
# ---------------------------------------------------------------------------

class _WhitelistProxyHandler(BaseHTTPRequestHandler):
    """HTTP/HTTPS proxy that enforces a domain whitelist.

    * HTTPS (CONNECT): establishes a raw TCP tunnel to the remote server if
      the domain is allowed; returns 403 otherwise.
    * HTTP (GET/HEAD/POST): forwards the request if the domain is allowed;
      returns 403 otherwise.
    """

    server: "_WhitelistProxyServer"

    # ------------------------------------------------------------------ HTTPS

    def do_CONNECT(self) -> None:  # noqa: N802
        """HTTPS tunnel — allow only whitelisted domains."""
        host_port = self.path                            # e.g. "example.com:443"
        domain    = host_port.split(":", 1)[0].lower()
        try:
            port = int(host_port.split(":", 1)[1]) if ":" in host_port else 443
        except ValueError:
            port = 443

        if self.server.get_allowed_domain(domain) is None:
            self.server.report_violation(domain)
            self.send_error(403, "Blocked by DLSlab web policy.")
            return

        # Open a raw TCP connection to the real server
        try:
            remote = socket.create_connection((domain, port), timeout=10)
        except OSError as exc:
            logger.debug("CONNECT to %s:%d failed: %s", domain, port, exc)
            self.send_error(502, "Cannot connect to remote host.")
            return

        # Tell the browser the tunnel is open, then relay TLS bytes
        self.send_response(200, "Connection Established")
        self.end_headers()
        self._relay(self.connection, remote)

    # ------------------------------------------------------------------- HTTP

    def do_GET(self) -> None:   # noqa: N802
        self._forward_http("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._forward_http("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._forward_http("POST")

    # ---------------------------------------------------------------- helpers

    def _forward_http(self, method: str) -> None:
        import http.client

        parsed = urlparse(self.path)
        if parsed.scheme:
            domain = (parsed.hostname or "").lower()
            path   = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
        else:
            host_header = self.headers.get("Host", "")
            domain = host_header.split(":", 1)[0].lower()
            path   = self.path or "/"

        if not path.startswith("/"):
            path = "/" + path

        allowed = self.server.get_allowed_domain(domain)
        if not domain or allowed is None:
            if domain:
                self.server.report_violation(domain)
            self.send_error(403, "Blocked by DLSlab web policy.")
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(content_length) if content_length > 0 else None

            conn = http.client.HTTPConnection(allowed, timeout=8)
            conn.request(method, path, body=body)
            resp      = conn.getresponse()
            resp_body = resp.read()
            conn.close()

            self.send_response(resp.status)
            self.send_header("Content-Type",   resp.getheader("Content-Type", "application/octet-stream"))
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(resp_body)
        except Exception as exc:
            logger.debug("HTTP forward failed %s%s: %s", allowed, path, exc)
            self.send_error(502, "Proxy forwarding error.")

    @staticmethod
    def _relay(client: socket.socket, remote: socket.socket) -> None:
        """Bidirectional raw relay for the HTTPS tunnel."""
        socks = [client, remote]
        try:
            while True:
                readable, _, error = select.select(socks, [], socks, 60)
                if error or not readable:
                    break
                for sock in readable:
                    try:
                        data = sock.recv(65536)
                    except OSError:
                        return
                    if not data:
                        return
                    other = remote if sock is client else client
                    try:
                        other.sendall(data)
                    except OSError:
                        return
        finally:
            try:
                remote.close()
            except OSError:
                pass

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        logger.debug("Proxy: " + format, *args)


# ---------------------------------------------------------------------------
# Proxy server
# ---------------------------------------------------------------------------

class _WhitelistProxyServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that carries the allowlist and violation callback."""

    def __init__(
        self,
        host: str,
        port: int,
        allowed_domains: set[str],
        on_violation: Callable[[str, str], None] | None,
    ) -> None:
        self._allowed_domains = {d.lower() for d in allowed_domains}
        self._on_violation    = on_violation
        self._recent: dict[str, float] = {}
        self._recent_lock = threading.Lock()
        super().__init__((host, port), _WhitelistProxyHandler)

    def get_allowed_domain(self, domain: str) -> str | None:
        """Return the canonical allowlisted entry if *domain* is permitted."""
        domain = domain.lower()
        if domain in self._allowed_domains:
            return domain
        for allowed in self._allowed_domains:
            if domain.endswith(f".{allowed}"):
                return allowed
        return None

    def report_violation(self, domain: str) -> None:
        """Call the violation callback, deduplicating within ``_VIOLATION_DEBOUNCE_S``."""
        now = time.monotonic()
        with self._recent_lock:
            if now - self._recent.get(domain, 0.0) < _VIOLATION_DEBOUNCE_S:
                return
            self._recent[domain] = now
        if self._on_violation:
            self._on_violation(domain, "web_whitelist")


# ---------------------------------------------------------------------------
# Public WebEnforcer
# ---------------------------------------------------------------------------

class WebEnforcer:
    """Apply web-access policies on a Windows student machine."""

    def __init__(
        self,
        app_enforcer: AppEnforcer | None = None,
        on_violation: Callable[[str, str], None] | None = None,
    ) -> None:
        self._app_enforcer    = app_enforcer
        self._on_violation    = on_violation
        self._proxy_server:   _WhitelistProxyServer | None = None
        self._proxy_thread:   threading.Thread | None = None
        self._allowed_domains: set[str] = set()
        self._saved_app_policy: tuple[str | None, list[str], bool] | None = None
        self._saved_proxy: dict = {}
        atexit.register(self.clear_web_policy)

    # ---------------------------------------------------------------- properties

    @property
    def is_active(self) -> bool:
        proxy_active = self._proxy_thread is not None and self._proxy_thread.is_alive()
        return proxy_active or self._saved_app_policy is not None

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        logger.debug("WebEnforcer.start() — no-op.")

    def stop(self) -> None:
        self.clear_web_policy()

    # ---------------------------------------------------------------- block_all

    def block_browsers(self) -> None:
        """Kill all browser processes and blacklist them via AppEnforcer."""
        browsers = [name.lower() for name in BROWSER_PROCESSES]

        if self._app_enforcer and self._saved_app_policy is None:
            self._saved_app_policy = (
                self._app_enforcer.mode,
                self._app_enforcer.apps,
                self._app_enforcer.is_active,
            )

        if self._app_enforcer:
            existing_mode = self._app_enforcer.mode
            existing_apps = self._app_enforcer.apps
            if existing_mode == "blacklist":
                merged = sorted(set(existing_apps) | set(browsers))
                self._app_enforcer.set_blacklist(merged)
            elif existing_mode is None:
                self._app_enforcer.set_blacklist(browsers)
            self._app_enforcer.start()

        for proc in psutil.process_iter(attrs=["pid", "name"]):
            name = (proc.info.get("name") or "").lower()
            if name not in browsers:
                continue
            try:
                proc.terminate()
                proc.wait(timeout=1.5)
                logger.info("Terminated browser: %s", name)
                if self._on_violation:
                    self._on_violation(name, "block_all")
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                pass

    # ---------------------------------------------------------------- whitelist

    def set_url_whitelist(self, urls: list[str]) -> None:
        """Allow only the domains extracted from *urls*; block everything else.

        1. Parse and normalise domains from the URL list (both www / non-www).
        2. Start the local proxy server on PROXY_HOST:PROXY_PORT.
        3. Save the current Windows proxy settings.
        4. Register the proxy in the Windows registry and notify WinINet so
           Chrome, Edge and Firefox start routing through it immediately.
        """
        self._allowed_domains = self._extract_domains(urls)
        if not self._allowed_domains:
            logger.warning("set_url_whitelist: empty domain list — not applied.")
            return

        self._start_proxy()
        self._saved_proxy = _read_proxy_settings()
        _enable_system_proxy(PROXY_HOST, PROXY_PORT)

        logger.info(
            "Web whitelist active — proxy %s:%d — %d domain(s): %s",
            PROXY_HOST, PROXY_PORT,
            len(self._allowed_domains),
            ", ".join(sorted(self._allowed_domains)),
        )

    # ---------------------------------------------------------------- clear

    def clear_web_policy(self) -> None:
        """Remove all web restrictions and restore the previous proxy settings."""
        self._stop_proxy()

        if self._saved_proxy:
            _write_proxy_settings(self._saved_proxy)
            self._saved_proxy = {}
        else:
            _disable_system_proxy()

        self._allowed_domains.clear()

        if self._app_enforcer and self._saved_app_policy is not None:
            mode, apps, was_active = self._saved_app_policy
            if mode == "whitelist":
                self._app_enforcer.set_whitelist(apps)
            elif mode == "blacklist":
                self._app_enforcer.set_blacklist(apps)
            else:
                self._app_enforcer.clear_policy()
            if was_active:
                self._app_enforcer.start()
            elif mode is None:
                self._app_enforcer.stop()
            self._saved_app_policy = None

    # ---------------------------------------------------------------- internals

    def _start_proxy(self) -> None:
        self._stop_proxy()
        self._proxy_server = _WhitelistProxyServer(
            host=PROXY_HOST,
            port=PROXY_PORT,
            allowed_domains=self._allowed_domains,
            on_violation=self._on_violation,
        )
        self._proxy_thread = threading.Thread(
            target=self._proxy_server.serve_forever,
            daemon=True,
            name="dlslab-web-proxy",
        )
        self._proxy_thread.start()

    def _stop_proxy(self) -> None:
        if self._proxy_server:
            self._proxy_server.shutdown()
            self._proxy_server.server_close()
        if self._proxy_thread and self._proxy_thread.is_alive():
            self._proxy_thread.join(timeout=2.0)
        self._proxy_server = None
        self._proxy_thread = None

    @staticmethod
    def _extract_domains(urls: list[str]) -> set[str]:
        """Parse URLs and return a set of normalised hostnames.

        Both ``example.com`` and ``www.example.com`` are added automatically
        so teachers don't need to type both variants.
        """
        domains: set[str] = set()
        for raw in urls:
            url = raw.strip()
            if not url:
                continue
            parsed = urlparse(url if "://" in url else f"https://{url}")
            host = (parsed.hostname or "").lower()
            if not host:
                continue
            domains.add(host)
            if host.startswith("www."):
                domains.add(host[4:])
            else:
                domains.add(f"www.{host}")
        return domains
