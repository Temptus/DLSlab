"""
client/web_enforcer.py
======================
Windows web access enforcer for DLSlab.

Implements:
1) Browser process blocking.
2) URL whitelist policy via local HTTP proxy + hosts file controls.
"""

from __future__ import annotations

import atexit
import http.client
import logging
import os
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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
HOSTS_FILE_PATH: str = r"C:\Windows\System32\drivers\etc\hosts"
HOSTS_BACKUP_PATH: str = r"C:\Windows\System32\drivers\etc\hosts.dlslab.bak"
HOSTS_MARKER_START: str = "# DLSlab Web Policy Start"
HOSTS_MARKER_END: str = "# DLSlab Web Policy End"
COMMON_BLOCKED_DOMAINS: list[str] = [
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
    "youtube.com",
    "www.youtube.com",
    "facebook.com",
    "www.facebook.com",
    "instagram.com",
    "www.instagram.com",
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "tiktok.com",
    "www.tiktok.com",
    "reddit.com",
    "www.reddit.com",
    "wikipedia.org",
    "www.wikipedia.org",
]


class _WhitelistProxyHandler(BaseHTTPRequestHandler):
    """HTTP proxy handler that only allows configured domains."""

    server: "_WhitelistProxyServer"

    def do_GET(self) -> None:  # noqa: N802
        self._forward_request("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._forward_request("HEAD")

    def do_CONNECT(self) -> None:  # noqa: N802
        domain = self.path.split(":", 1)[0].lower()
        if self.server.get_allowed_domain(domain) is None:
            self.server.report_violation(domain)
        self.send_error(403, "HTTPS proxy tunneling is blocked by policy.")

    def _forward_request(self, method: str) -> None:
        parsed = urlparse(self.path)
        if parsed.scheme and parsed.scheme.lower() != "http":
            self.send_error(403, "Only HTTP URLs are supported by policy proxy.")
            return

        if parsed.scheme:
            domain = (parsed.hostname or "").lower()
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
        else:
            host_header = self.headers.get("Host", "")
            domain = host_header.split(":", 1)[0].lower()
            path = self.path or "/"

        if not path.startswith("/"):
            path = "/" + path

        allowed_domain = self.server.get_allowed_domain(domain)
        if not domain or allowed_domain is None:
            if domain:
                self.server.report_violation(domain)
            self.send_error(403, "Blocked by DLSlab web policy.")
            return

        try:
            connection = http.client.HTTPConnection(allowed_domain, timeout=8)
            connection.request(method, path)
            response = connection.getresponse()
            body = response.read()
            self.send_response(response.status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(body)
            connection.close()
        except Exception as exc:
            logger.debug("Proxy forward failed for domain=%s path=%s: %s", allowed_domain, path, exc)
            self.send_error(502, "Proxy forwarding error.")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        """Silence default HTTP server logging."""
        logger.debug("Proxy: " + format, *args)


class _WhitelistProxyServer(ThreadingHTTPServer):
    """Proxy server with domain whitelist and violation callback."""

    def __init__(
        self,
        host: str,
        port: int,
        allowed_domains: set[str],
        on_violation: Callable[[str, str], None] | None,
    ) -> None:
        self._allowed_domains = {d.lower() for d in allowed_domains}
        self._on_violation = on_violation
        super().__init__((host, port), _WhitelistProxyHandler)

    def get_allowed_domain(self, domain: str) -> str | None:
        """Return canonical allowlisted domain if *domain* is permitted."""
        if domain in self._allowed_domains:
            return domain
        for allowed in self._allowed_domains:
            if domain.endswith(f".{allowed}"):
                return allowed
        return None

    def report_violation(self, domain: str) -> None:
        """Emit a policy-violation callback for blocked domain access."""
        if self._on_violation:
            self._on_violation(domain, "web_whitelist")


class WebEnforcer:
    """Apply web access policies on a Windows client."""

    def __init__(
        self,
        app_enforcer: AppEnforcer | None = None,
        on_violation: Callable[[str, str], None] | None = None,
    ) -> None:
        self._app_enforcer = app_enforcer
        self._on_violation = on_violation
        self._proxy_server: _WhitelistProxyServer | None = None
        self._proxy_thread: threading.Thread | None = None
        self._allowed_domains: set[str] = set()
        self._saved_app_policy: tuple[str | None, list[str], bool] | None = None
        atexit.register(self._restore_hosts_backup)

    @property
    def is_active(self) -> bool:
        """Return ``True`` when a web policy is active."""
        proxy_active = self._proxy_thread is not None and self._proxy_thread.is_alive()
        return proxy_active or self._saved_app_policy is not None

    def start(self) -> None:
        """Start the web enforcer (no-op; policies activate methods directly)."""
        logger.debug("Web enforcer start called.")

    def stop(self) -> None:
        """Stop all web restrictions."""
        self.clear_web_policy()

    def block_browsers(self) -> None:
        """Block all known browsers by killing their processes and blacklisting them."""
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

        for process in psutil.process_iter(attrs=["pid", "name"]):
            name = (process.info.get("name") or "").lower()
            if name not in browsers:
                continue
            try:
                process.terminate()
                process.wait(timeout=1.5)
                logger.info("Terminated browser process %s.", name)
                if self._on_violation:
                    self._on_violation(name, "block_all")
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                continue

    def set_url_whitelist(self, urls: list[str]) -> None:
        """Allow web access only to domains extracted from *urls*."""
        self._allowed_domains = self._extract_domains(urls)
        if not self._allowed_domains:
            logger.warning("URL whitelist received empty domain set.")
            return

        self._start_proxy()
        self._apply_hosts_policy(self._allowed_domains)
        logger.info(
            "Web URL whitelist active on %s:%d for %d domain(s).",
            PROXY_HOST,
            PROXY_PORT,
            len(self._allowed_domains),
        )

    def clear_web_policy(self) -> None:
        """Clear all web restrictions and restore original settings."""
        self._stop_proxy()
        self._restore_hosts_backup()
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

    def _start_proxy(self) -> None:
        """Start the local whitelist proxy."""
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
        """Stop the local whitelist proxy if it is running."""
        if self._proxy_server:
            self._proxy_server.shutdown()
            self._proxy_server.server_close()
        if self._proxy_thread and self._proxy_thread.is_alive():
            self._proxy_thread.join(timeout=2.0)
        self._proxy_server = None
        self._proxy_thread = None

    def _apply_hosts_policy(self, allowed_domains: set[str]) -> None:
        """Write DLSlab policy block entries to hosts file."""
        if os.name != "nt":
            logger.warning("Hosts policy changes are only supported on Windows.")
            return

        hosts_path = Path(HOSTS_FILE_PATH)
        backup_path = Path(HOSTS_BACKUP_PATH)

        if not backup_path.exists():
            backup_path.write_text(hosts_path.read_text(encoding="utf-8"), encoding="utf-8")

        original = backup_path.read_text(encoding="utf-8")
        blocked_domains = [
            d for d in COMMON_BLOCKED_DOMAINS if d.lower() not in allowed_domains
        ]
        entries = "\n".join(f"127.0.0.1 {domain}" for domain in blocked_domains)
        content = (
            f"{original.rstrip()}\n\n"
            f"{HOSTS_MARKER_START}\n"
            f"{entries}\n"
            f"{HOSTS_MARKER_END}\n"
        )
        hosts_path.write_text(content, encoding="utf-8")

    def _restore_hosts_backup(self) -> None:
        """Restore the original hosts backup if present."""
        if os.name != "nt":
            return
        hosts_path = Path(HOSTS_FILE_PATH)
        backup_path = Path(HOSTS_BACKUP_PATH)
        if backup_path.exists():
            try:
                hosts_path.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError as exc:
                logger.warning("Failed restoring hosts file: %s", exc)

    @staticmethod
    def _extract_domains(urls: list[str]) -> set[str]:
        """Extract normalized domains from URL list."""
        domains: set[str] = set()
        for raw_url in urls:
            url = raw_url.strip()
            if not url:
                continue
            parsed = urlparse(url if "://" in url else f"https://{url}")
            if parsed.hostname:
                domains.add(parsed.hostname.lower())
        return domains
