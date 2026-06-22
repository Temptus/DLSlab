"""
shared/messages.py Yan
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
    START_SHOW_TEACHER = "START_SHOW_TEACHER"
    STOP_SHOW_TEACHER = "STOP_SHOW_TEACHER"
    TEACHER_FRAME = "TEACHER_FRAME"
    START_SHOW_STUDENT = "START_SHOW_STUDENT"
    STOP_SHOW_STUDENT = "STOP_SHOW_STUDENT"
    STUDENT_FRAME = "STUDENT_FRAME"
    REQUEST_HIRES_SCREENSHOT = "REQUEST_HIRES_SCREENSHOT"
    STOP_HIRES_SCREENSHOT = "STOP_HIRES_SCREENSHOT"
    SET_APP_POLICY = "SET_APP_POLICY"
    CLEAR_APP_POLICY = "CLEAR_APP_POLICY"
    SET_WEB_POLICY = "SET_WEB_POLICY"
    CLEAR_WEB_POLICY = "CLEAR_WEB_POLICY"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    SHUTDOWN = "SHUTDOWN"
    RESTART = "RESTART"
    LOGOUT = "LOGOUT"
    LOCK_WORKSTATION = "LOCK_WORKSTATION"
    OPEN_URL = "OPEN_URL"
    RUN_APP = "RUN_APP"
    CLIENT_MAC = "CLIENT_MAC"
    SEND_FILE = "SEND_FILE"


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

def make_register(
    client_id: str,
    hostname: str,
    ip: str,
    mac: str = "",
) -> Message:
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
        payload={"hostname": hostname, "ip": ip, "mac": mac},
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


def make_start_show_teacher(server_id: str) -> Message:
    """Build a START_SHOW_TEACHER message sent from the server to a client.

    Args:
        server_id: Identifier of the server.

    Returns:
        A :class:`Message` of type START_SHOW_TEACHER.
    """
    return Message(
        type=MessageType.START_SHOW_TEACHER,
        client_id=server_id,
        payload={},
    )


def make_stop_show_teacher(server_id: str) -> Message:
    """Build a STOP_SHOW_TEACHER message sent from the server to a client.

    Args:
        server_id: Identifier of the server.

    Returns:
        A :class:`Message` of type STOP_SHOW_TEACHER.
    """
    return Message(
        type=MessageType.STOP_SHOW_TEACHER,
        client_id=server_id,
        payload={},
    )


def make_teacher_frame(server_id: str, frame_b64: str) -> Message:
    """Build a TEACHER_FRAME message carrying a base64-encoded JPEG frame.

    Args:
        server_id:  Identifier of the server.
        frame_b64:  Base64-encoded JPEG of the teacher's screen.

    Returns:
        A :class:`Message` of type TEACHER_FRAME.
    """
    return Message(
        type=MessageType.TEACHER_FRAME,
        client_id=server_id,
        payload={"frame": frame_b64},
    )


def make_request_hires_screenshot(server_id: str) -> Message:
    """Build a REQUEST_HIRES_SCREENSHOT message sent from the server to the presenter client.

    Args:
        server_id: Identifier of the server.

    Returns:
        A :class:`Message` of type REQUEST_HIRES_SCREENSHOT.
    """
    return Message(
        type=MessageType.REQUEST_HIRES_SCREENSHOT,
        client_id=server_id,
        payload={},
    )


def make_stop_hires_screenshot(server_id: str) -> Message:
    """Build a STOP_HIRES_SCREENSHOT message sent from the server to the presenter client.

    Args:
        server_id: Identifier of the server.

    Returns:
        A :class:`Message` of type STOP_HIRES_SCREENSHOT.
    """
    return Message(
        type=MessageType.STOP_HIRES_SCREENSHOT,
        client_id=server_id,
        payload={},
    )


def make_start_show_student(
    server_id: str,
    presenter_name: str,
    presenter_id: str,
) -> Message:
    """Build a START_SHOW_STUDENT message sent from the server to audience clients.

    Args:
        server_id:      Identifier of the server.
        presenter_name: Human-readable name of the presenting student.
        presenter_id:   Unique client ID of the presenting student.

    Returns:
        A :class:`Message` of type START_SHOW_STUDENT.
    """
    return Message(
        type=MessageType.START_SHOW_STUDENT,
        client_id=server_id,
        payload={"presenter_name": presenter_name, "presenter_id": presenter_id},
    )


def make_stop_show_student(server_id: str) -> Message:
    """Build a STOP_SHOW_STUDENT message sent from the server to audience clients.

    Args:
        server_id: Identifier of the server.

    Returns:
        A :class:`Message` of type STOP_SHOW_STUDENT.
    """
    return Message(
        type=MessageType.STOP_SHOW_STUDENT,
        client_id=server_id,
        payload={},
    )


def make_student_frame(server_id: str, frame_b64: str) -> Message:
    """Build a STUDENT_FRAME message carrying a base64-encoded JPEG frame.

    Args:
        server_id:  Identifier of the server.
        frame_b64:  Base64-encoded JPEG of the presenting student's screen.

    Returns:
        A :class:`Message` of type STUDENT_FRAME.
    """
    return Message(
        type=MessageType.STUDENT_FRAME,
        client_id=server_id,
        payload={"frame": frame_b64},
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


def make_set_app_policy(server_id: str, mode: str, apps: list[str]) -> Message:
    """Build a SET_APP_POLICY message.

    Args:
        server_id: Identifier of the server.
        mode:      Policy mode (``"whitelist"`` or ``"blacklist"``).
        apps:      Process names used by the policy.

    Returns:
        A :class:`Message` of type SET_APP_POLICY.
    """
    return Message(
        type=MessageType.SET_APP_POLICY,
        client_id=server_id,
        payload={"mode": mode, "apps": apps},
    )


def make_clear_app_policy(server_id: str) -> Message:
    """Build a CLEAR_APP_POLICY message."""
    return Message(
        type=MessageType.CLEAR_APP_POLICY,
        client_id=server_id,
        payload={},
    )


def make_set_web_policy(server_id: str, mode: str, urls: list[str]) -> Message:
    """Build a SET_WEB_POLICY message."""
    return Message(
        type=MessageType.SET_WEB_POLICY,
        client_id=server_id,
        payload={"mode": mode, "urls": urls},
    )


def make_clear_web_policy(server_id: str) -> Message:
    """Build a CLEAR_WEB_POLICY message."""
    return Message(
        type=MessageType.CLEAR_WEB_POLICY,
        client_id=server_id,
        payload={},
    )


def make_policy_violation(client_id: str, process_name: str, mode: str) -> Message:
    """Build a POLICY_VIOLATION message emitted by a client."""
    return Message(
        type=MessageType.POLICY_VIOLATION,
        client_id=client_id,
        payload={"process_name": process_name, "mode": mode},
    )


def make_shutdown(server_id: str, delay: int) -> Message:
    """Build a SHUTDOWN message."""
    return Message(
        type=MessageType.SHUTDOWN,
        client_id=server_id,
        payload={"delay": delay},
    )


def make_restart(server_id: str, delay: int) -> Message:
    """Build a RESTART message."""
    return Message(
        type=MessageType.RESTART,
        client_id=server_id,
        payload={"delay": delay},
    )


def make_logout(server_id: str) -> Message:
    """Build a LOGOUT message."""
    return Message(
        type=MessageType.LOGOUT,
        client_id=server_id,
        payload={},
    )


def make_lock_workstation(server_id: str) -> Message:
    """Build a LOCK_WORKSTATION message."""
    return Message(
        type=MessageType.LOCK_WORKSTATION,
        client_id=server_id,
        payload={},
    )


def make_open_url(server_id: str, url: str) -> Message:
    """Build an OPEN_URL message."""
    return Message(
        type=MessageType.OPEN_URL,
        client_id=server_id,
        payload={"url": url},
    )


def make_run_app(server_id: str, path: str, args: list[str] | None = None) -> Message:
    """Build a RUN_APP message."""
    return Message(
        type=MessageType.RUN_APP,
        client_id=server_id,
        payload={"path": path, "args": args or []},
    )


def make_send_file(server_id: str, filename: str, data_b64: str) -> Message:
    """Build a SEND_FILE message carrying a base64-encoded document.

    Args:
        server_id: Identifier of the server.
        filename:  Original filename (e.g. ``"practica1.pdf"``).
        data_b64:  Base64-encoded binary contents of the file.

    Returns:
        A :class:`Message` of type SEND_FILE.
    """
    return Message(
        type=MessageType.SEND_FILE,
        client_id=server_id,
        payload={"filename": filename, "data": data_b64},
    )
