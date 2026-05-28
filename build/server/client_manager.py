"""
server/client_manager.py
========================
Manages the set of student agents currently connected to the DLSlab server.

Each connected client is represented by a :class:`ClientInfo` dataclass that
stores metadata and the asyncio transport used to push messages back to that
client.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

logger = logging.getLogger(__name__)


@dataclass
class ClientInfo:
    """Metadata and I/O handles for a single connected student agent.

    Attributes:
        client_id:  Unique identifier sent by the client during REGISTER.
        hostname:   Human-readable machine name of the student PC.
        ip:         IP address of the client as reported by the OS.
        mac:        Client MAC address used for Wake-on-LAN.
        last_seen:  UTC timestamp of the last successfully received message.
        writer:     asyncio StreamWriter used to push messages to this client.
    """

    client_id: str
    hostname: str
    ip: str
    mac: str
    last_seen: datetime
    writer: asyncio.StreamWriter

    def touch(self) -> None:
        """Update :attr:`last_seen` to the current UTC time."""
        self.last_seen = datetime.now(tz=timezone.utc)

    @property
    def is_connected(self) -> bool:
        """Return ``True`` if the underlying transport is still open."""
        return not self.writer.is_closing()


class ClientManager:
    """Thread-safe registry of all connected DLSlab student agents.

    All mutating operations are **not** coroutine-safe by themselves —
    callers must ensure they run inside the same asyncio event loop.
    """

    def __init__(self) -> None:
        self._clients: dict[str, ClientInfo] = {}

    # ------------------------------------------------------------------
    # Registration / removal
    # ------------------------------------------------------------------

    def register(
        self,
        client_id: str,
        hostname: str,
        ip: str,
        mac: str,
        writer: asyncio.StreamWriter,
    ) -> ClientInfo:
        """Register a new client or update an existing registration.

        If a client with *client_id* is already registered (e.g. after a
        reconnect), its entry is updated in place.

        Args:
            client_id: Unique string identifier for the client.
            hostname:  Human-readable machine name.
            ip:        Client IP address.
            mac:       Client MAC address (empty if unknown).
            writer:    asyncio StreamWriter for the open TCP connection.

        Returns:
            The newly created or updated :class:`ClientInfo`.
        """
        info = ClientInfo(
            client_id=client_id,
            hostname=hostname,
            ip=ip,
            mac=mac,
            last_seen=datetime.now(tz=timezone.utc),
            writer=writer,
        )
        self._clients[client_id] = info
        logger.info(
            "Client registered: %s (%s @ %s, mac=%s)",
            client_id,
            hostname,
            ip,
            mac or "unknown",
        )
        return info

    def remove(self, client_id: str) -> None:
        """Remove a client from the registry.

        Args:
            client_id: The identifier of the client to remove.
        """
        if client_id in self._clients:
            del self._clients[client_id]
            logger.info("Client removed: %s", client_id)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, client_id: str) -> ClientInfo | None:
        """Return the :class:`ClientInfo` for *client_id*, or ``None``.

        Args:
            client_id: The identifier to look up.

        Returns:
            The matching :class:`ClientInfo`, or ``None`` if not found.
        """
        return self._clients.get(client_id)

    def all_clients(self) -> Iterator[ClientInfo]:
        """Iterate over all currently registered clients.

        Yields:
            :class:`ClientInfo` objects for every registered client.
        """
        yield from self._clients.values()

    def all_client_ids(self) -> Iterator[str]:
        """Iterate over all currently registered client identifiers.

        Yields:
            Client ID strings for every registered client.
        """
        yield from self._clients.keys()

    def __len__(self) -> int:
        return len(self._clients)

    def __contains__(self, client_id: str) -> bool:
        return client_id in self._clients

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def touch(self, client_id: str) -> None:
        """Update *last_seen* for *client_id* to the current UTC time.

        Args:
            client_id: The identifier of the client to refresh.
        """
        client = self._clients.get(client_id)
        if client:
            client.touch()

    async def broadcast(self, message_bytes: bytes) -> None:
        """Send raw bytes to every connected client.

        Clients whose transport has already closed are silently skipped.

        Args:
            message_bytes: Pre-serialised message (UTF-8, newline-terminated).
        """
        for client in list(self._clients.values()):
            if client.is_connected:
                try:
                    client.writer.write(message_bytes)
                    await client.writer.drain()
                except (ConnectionResetError, BrokenPipeError, OSError) as exc:
                    logger.warning(
                        "Failed to broadcast to %s: %s", client.client_id, exc
                    )
