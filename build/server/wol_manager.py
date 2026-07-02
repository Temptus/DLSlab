"""
server/wol_manager.py
=====================
Wake-on-LAN helper and MAC persistence for DLSlab.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import wakeonlan

from server.client_manager import ClientManager

logger = logging.getLogger(__name__)

MAC_STORE_PATH: Path = Path("macs.json")


class WolManager:
    """Handles known MAC addresses and Wake-on-LAN packet sending."""

    def __init__(self, client_manager: ClientManager) -> None:
        self._client_manager = client_manager
        self._known_macs: dict[str, str] = {}
        self._load_known_macs()

    def register_mac(self, client_id: str, mac: str) -> None:
        """Register (or update) a MAC address for a given client.

        If the same MAC is already stored under a *different* client_id
        (stale entry from a previous run), that old entry is removed so the
        teacher console never shows duplicate thumbnails for the same machine.
        """
        normalized = self._normalize_mac(mac)
        if not normalized:
            return
        # Remove stale entries that share the same MAC under a different ID.
        stale = [
            cid for cid, m in self._known_macs.items()
            if m == normalized and cid != client_id
        ]
        for cid in stale:
            del self._known_macs[cid]
            logger.info(
                "Removed stale MAC entry %s (MAC %s re-assigned to %s)",
                cid, normalized, client_id,
            )
        self._known_macs[client_id] = normalized
        self._save_known_macs()

    def find_client_id_by_mac(self, normalized_mac: str) -> str | None:
        """Return the client_id that owns *normalized_mac*, or ``None``."""
        for cid, mac in self._known_macs.items():
            if mac == normalized_mac:
                return cid
        return None

    def wake(self, client_id: str) -> bool:
        """Send a WoL packet to a client by its ID."""
        mac = self._known_macs.get(client_id)
        if not mac:
            info = self._client_manager.get(client_id)
            if info and info.mac:
                mac = self._normalize_mac(info.mac)
        if not mac:
            return False
        self.wake_by_mac(mac)
        return True

    def wake_by_mac(self, mac: str) -> None:
        """Send a WoL packet directly using the provided MAC address."""
        normalized = self._normalize_mac(mac)
        if not normalized:
            raise ValueError(f"Invalid MAC address: {mac!r}")
        wakeonlan.send_magic_packet(normalized)

    def wake_all(self) -> dict[str, bool]:
        """Send WoL to all known MACs and return per-client success status."""
        results: dict[str, bool] = {}
        for client_id in self._known_macs:
            try:
                results[client_id] = self.wake(client_id)
            except (ValueError, OSError) as exc:
                logger.warning("Wake-on-LAN failed for %s: %s", client_id, exc)
                results[client_id] = False
        return results

    def get_known_macs(self) -> dict[str, str]:
        """Return a copy of the known client MAC mapping."""
        return dict(self._known_macs)

    @staticmethod
    def _normalize_mac(mac: str) -> str:
        cleaned = "".join(ch for ch in mac.strip() if ch.isalnum())
        if len(cleaned) != 12:
            return ""
        return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2)).upper()

    def _load_known_macs(self) -> None:
        if not MAC_STORE_PATH.exists():
            return
        try:
            raw = json.loads(MAC_STORE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read MAC store %s: %s", MAC_STORE_PATH, exc)
            return
        if not isinstance(raw, dict):
            return
        for client_id, mac in raw.items():
            normalized = self._normalize_mac(str(mac))
            if normalized:
                self._known_macs[str(client_id)] = normalized

    def _save_known_macs(self) -> None:
        try:
            MAC_STORE_PATH.write_text(
                json.dumps(self._known_macs, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Failed to persist MAC store %s: %s", MAC_STORE_PATH, exc)

