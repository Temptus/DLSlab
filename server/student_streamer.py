"""
server/student_streamer.py
==========================
Relays high-resolution frames from a presenting student to all audience clients.

When the teacher starts a "Show Student" session, the :class:`StudentStreamer`:

1. Tracks the *presenter* client (the student whose screen is being shared).
2. Receives each high-resolution frame forwarded by :mod:`server.main_server`
   via :meth:`relay_frame`.
3. Broadcasts a ``STUDENT_FRAME`` message to every *audience* client.

The streamer is intentionally **frame-driven** — it has no capture loop of its
own.  The presenter's agent is responsible for sending frames at the requested
rate; this class simply relays them.

Usage example::

    streamer = StudentStreamer(server)
    streamer.start(presenter_id="PC-03-abc", audience_ids=None)  # None → everyone else
    await streamer.relay_frame(frame_b64)
    streamer.stop()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shared.messages import make_student_frame

if TYPE_CHECKING:
    from server.main_server import DLSlabServer

logger = logging.getLogger(__name__)


class StudentStreamer:
    """Relays presenter screen frames to the audience.

    Args:
        server: The running :class:`~server.main_server.DLSlabServer` instance
                used to send messages to connected clients.
    """

    def __init__(self, server: "DLSlabServer") -> None:
        self._server = server
        self._presenter_id: str | None = None
        self._audience_ids: list[str] | None = None
        self._running: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_streaming(self) -> bool:
        """``True`` while a show-student session is active."""
        return self._running

    @property
    def presenter_id(self) -> str | None:
        """Client ID of the current presenter, or ``None`` if inactive."""
        return self._presenter_id

    def start(
        self,
        presenter_id: str,
        audience_ids: list[str] | None,
    ) -> None:
        """Begin relaying frames from *presenter_id* to the audience.

        If a session is already active it is stopped first.

        Args:
            presenter_id: Client ID of the student who is presenting.
            audience_ids: Explicit list of audience client IDs.  Pass ``None``
                          to broadcast to **all** currently connected clients
                          except the presenter.
        """
        if self._running:
            self.stop()

        self._presenter_id = presenter_id
        self._audience_ids = audience_ids
        self._running = True
        logger.info(
            "StudentStreamer started — presenter=%s audience=%s",
            presenter_id,
            "all except presenter" if audience_ids is None else len(audience_ids),
        )

    def stop(self) -> None:
        """Stop the current show-student session."""
        self._running = False
        old_presenter = self._presenter_id
        self._presenter_id = None
        self._audience_ids = None
        logger.info("StudentStreamer stopped (was presenter=%s).", old_presenter)

    async def relay_frame(self, frame_b64: str) -> None:
        """Relay *frame_b64* to all audience clients.

        This method is a no-op if :attr:`is_streaming` is ``False`` or
        *frame_b64* is empty.

        Args:
            frame_b64: Base64-encoded JPEG string received from the presenter.
        """
        if not self._running or not frame_b64:
            return

        msg = make_student_frame("server", frame_b64)

        if self._audience_ids is None:
            # Broadcast to everyone except the presenter.
            targets: list[str] = [
                cid
                for cid in self._server.clients.all_client_ids()
                if cid != self._presenter_id
            ]
        else:
            targets = list(self._audience_ids)

        for cid in targets:
            await self._server.send_to_client(cid, msg)
