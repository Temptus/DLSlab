"""
server/protocol.py Yan
==================
Low-level helpers for framing and parsing DLSlab messages over a TCP stream.

Each message is sent as a **newline-terminated UTF-8 JSON string**, which
makes it trivial to split messages in an asyncio StreamReader.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from shared.messages import Message

logger = logging.getLogger(__name__)

# Maximum size of a single incoming message (50 MB).
# Increased from 10 MB to support SEND_FILE transfers of large documents.
MAX_MESSAGE_BYTES: int = 50 * 1024 * 1024


async def read_message(reader: asyncio.StreamReader) -> Message | None:
    """Read and parse a single newline-terminated message from *reader*.

    Unknown message types (e.g. new features not yet deployed to this node)
    are silently discarded and reading continues, so the connection is never
    dropped due to a version mismatch.  ``None`` is returned only when the
    remote side has actually closed the connection.

    Args:
        reader: The :class:`asyncio.StreamReader` for an open connection.

    Returns:
        A parsed :class:`~shared.messages.Message`, or ``None`` when the
        remote side has closed the connection.
    """
    while True:
        try:
            raw: bytes = await reader.readuntil(b"\n")
        except asyncio.IncompleteReadError:
            return None
        except ConnectionResetError:
            return None

        if len(raw) > MAX_MESSAGE_BYTES:
            logger.warning(
                "Oversized message received (%d bytes) — discarding.", len(raw)
            )
            return None

        try:
            return Message.from_json(raw.decode("utf-8").strip())
        except ValueError as exc:
            # Unknown or unsupported message type — skip and keep reading.
            logger.warning("Unrecognised message discarded: %s", exc)
            continue


async def write_message(writer: asyncio.StreamWriter, message: Message) -> None:
    """Serialise and send *message* over *writer*.

    Args:
        writer:  The :class:`asyncio.StreamWriter` for an open connection.
        message: The :class:`~shared.messages.Message` to send.
    """
    writer.write(message.to_bytes())
    await writer.drain()


async def iter_messages(reader: asyncio.StreamReader) -> AsyncIterator[Message]:
    """Yield parsed messages from *reader* until the connection closes.

    Args:
        reader: The :class:`asyncio.StreamReader` for an open connection.

    Yields:
        Parsed :class:`~shared.messages.Message` objects.
    """
    while True:
        message = await read_message(reader)
        if message is None:
            break
        yield message
