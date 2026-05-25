"""
shared/messages.py
==================
Dataclasses and helpers for the DLSlab client-server protocol.

All messages are serialised to / deserialised from JSON.
Every message carries three top-level fields:

    {
        "type":      "<MessageType>",
        "client_id": "<uuid-or-hostname>",
        "payload":   { ... }
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------

class MessageType(str, Enum):
    """Enumeration of all protocol message types."""

    REGISTER = "REGISTER"
    SCREENSHOT = "SCREENSHOT"
    REMOTE_INPUT = "REMOTE_INPUT"
    PING = "PING"
    PONG = "PONG"
    COMMAND = "COMMAND"
    BLANK_SCREEN = "BLANK_SCREEN"
    UNBLANK_SCREEN = "UNBLANK_SCREEN"


# ---------------------------------------------------------------------------
# Base message
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """Base DLSlab protocol message."""

    type: MessageType
    client_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialise the message to a JSON string."""
        data = {
            "type": self.type.value,
            "client_id": self.client_id,
            "payload": self.payload,
        }
        return json.dumps(data)

    def to_bytes(self) -> bytes:
        """Serialise the message to UTF-8 bytes (newline-terminated)."""
        return (self.to_json() + "\n").encode("utf-8")

    @classmethod
    def from_json(cls, raw: str) -> "Message":
        """Deserialise a JSON string into a :class:`Message` instance.

        Args:
            raw: JSON string received from the network.

        Returns:
            A :class:`Message` instance.

        Raises:
            ValueError: If the JSON is malformed or missing required fields.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc

        try:
            msg_type = MessageType(data["type"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Unknown or missing message type: {exc}") from exc

        return cls(
            type=msg_type,
            client_id=data.get("client_id", ""),
            payload=data.get("payload", {}),
        )


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------

def make_register(client_id: str, hostname: str, ip: str) -> Message:
    """Build a REGISTER message.

    Args:
        client_id: Unique identifier for the client (e.g. UUID or hostname).
        hostname:  Human-readable machine name.
        ip:        Client IP address as seen from the client itself.

    Returns:
        A :class:`Message` of type REGISTER.
    """
    return Message(
        type=MessageType.REGISTER,
        client_id=client_id,
        payload={"hostname": hostname, "ip": ip},
    )


def make_screenshot(client_id: str, image_b64: str) -> Message:
    """Build a SCREENSHOT message containing a base64-encoded JPEG.

    Args:
        client_id:  Unique identifier for the sending client.
        image_b64:  Base64-encoded JPEG thumbnail.

    Returns:
        A :class:`Message` of type SCREENSHOT.
    """
    return Message(
        type=MessageType.SCREENSHOT,
        client_id=client_id,
        payload={"image": image_b64},
    )


def make_remote_input(
    server_id: str,
    target_client_id: str,
    event_type: str,
    event_data: dict[str, Any],
) -> Message:
    """Build a REMOTE_INPUT message forwarded from the server to a client.

    Args:
        server_id:        Identifier of the server (usually ``"server"``).
        target_client_id: The client that will execute the input event.
        event_type:       One of ``mouse_move``, ``mouse_click``,
                          ``key_press``, ``key_release``.
        event_data:       Event-specific parameters (coordinates, key, etc.).

    Returns:
        A :class:`Message` of type REMOTE_INPUT.
    """
    return Message(
        type=MessageType.REMOTE_INPUT,
        client_id=server_id,
        payload={
            "target": target_client_id,
            "event_type": event_type,
            "event_data": event_data,
        },
    )


def make_ping(sender_id: str) -> Message:
    """Build a PING heartbeat message.

    Args:
        sender_id: Identifier of the sender (client or server).

    Returns:
        A :class:`Message` of type PING.
    """
    return Message(type=MessageType.PING, client_id=sender_id)


def make_pong(sender_id: str) -> Message:
    """Build a PONG heartbeat response message.

    Args:
        sender_id: Identifier of the sender (client or server).

    Returns:
        A :class:`Message` of type PONG.
    """
    return Message(type=MessageType.PONG, client_id=sender_id)


def make_blank_screen(server_id: str, message: str = "Atención al frente") -> Message:
    """Build a BLANK_SCREEN message sent from the server to a client.

    Args:
        server_id: Identifier of the server.
        message:   Text to display on the student's screen overlay.

    Returns:
        A :class:`Message` of type BLANK_SCREEN.
    """
    return Message(
        type=MessageType.BLANK_SCREEN,
        client_id=server_id,
        payload={"message": message},
    )


def make_unblank_screen(server_id: str) -> Message:
    """Build an UNBLANK_SCREEN message sent from the server to a client.

    Args:
        server_id: Identifier of the server.

    Returns:
        A :class:`Message` of type UNBLANK_SCREEN.
    """
    return Message(
        type=MessageType.UNBLANK_SCREEN,
        client_id=server_id,
        payload={},
    )


def make_command(server_id: str, target_client_id: str, command: str, args: dict[str, Any] | None = None) -> Message:
    """Build a COMMAND message sent from the server to a client.

    Args:
        server_id:        Identifier of the server.
        target_client_id: The client that will execute the command.
        command:          Command name, e.g. ``"shutdown"``, ``"open_url"``.
        args:             Optional dictionary of command arguments.

    Returns:
        A :class:`Message` of type COMMAND.
    """
    return Message(
        type=MessageType.COMMAND,
        client_id=server_id,
        payload={
            "target": target_client_id,
            "command": command,
            "args": args or {},
        },
    )
