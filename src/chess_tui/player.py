"""Player abstraction: local (human) and network (HTTP) players.

A :class:`Player` is something that, given the current board position, can
return the next move. :class:`LocalPlayer` is a marker for "the human drives
this side via the TUI" (no logic — the TUI's own input handling does the work).
:class:`NetworkPlayer` POSTs the current FEN to a remote server and parses
the returned SAN into a move.
"""

from __future__ import annotations

import asyncio
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Protocol

import chess

from . import net


class Player(Protocol):
    """Something that can choose a move for one side."""

    color: chess.Color

    async def choose_move(self, board: chess.Board) -> chess.Move: ...


@dataclass
class LocalPlayer:
    """Marker for "the human plays this side via the TUI"."""

    color: chess.Color

    async def choose_move(self, board: chess.Board) -> chess.Move:
        # The TUI's input handling drives local moves; this method should
        # never be called. Raise loudly if it is.
        raise RuntimeError("LocalPlayer.choose_move should not be called")


@dataclass
class NetworkPlayer:
    """A player that defers move selection to a remote HTTP server.

    The server must implement the API described in
    ``openapi/chess-tui-net.yaml`` — POST /move with ``{"fen": ...}``, get
    back ``{"san": "..."}`` (or a 400 with ``{"error": "..."}`` for an
    illegal move / bad FEN).

    Retry behavior:
        - Retries infinitely on ALL errors (transport, server, illegal move).
        - If a request fails quickly (before the full timeout elapses), the
          client sleeps for the remaining time before retrying, so we don't
          hammer the server.
        - Calls ``on_status(message)`` with countdown while waiting to retry.
    """

    color: chess.Color
    url: str
    timeout: float = 10.0
    on_status: Callable[[str], None] | None = None

    def _report(self, msg: str) -> None:
        if self.on_status is not None:
            self.on_status(msg)

    async def choose_move(self, board: chess.Board) -> chess.Move:
        attempt = 0
        while True:
            attempt += 1
            start = time.monotonic()
            try:
                # Run the blocking urllib call on a worker thread so the TUI
                # event loop stays responsive.
                loop = asyncio.get_running_loop()
                san = await loop.run_in_executor(None, self._fetch_san, board.fen())
                try:
                    move = board.parse_san(san)
                except ValueError as exc:
                    raise IllegalMoveError(
                        f"server returned an unparseable move {san!r}: {exc}"
                    ) from exc
                if move not in board.legal_moves:
                    raise IllegalMoveError(
                        f"server returned an illegal move for this position: {san!r}"
                    )
                self._report("")
                return move
            except Exception as exc:
                # ANY error: compensate and retry with countdown
                elapsed = time.monotonic() - start
                remaining = max(0.0, self.timeout - elapsed)
                # Countdown while waiting
                for secs in range(int(remaining), 0, -1):
                    self._report(f"retrying in {secs}s (attempt {attempt}: {exc})")
                    await asyncio.sleep(1)
                if remaining % 1 > 0:
                    await asyncio.sleep(remaining % 1)
                self._report(f"retrying (attempt {attempt + 1})...")

    def _fetch_san(self, fen: str) -> str:
        return net.request_move(self.url, fen, timeout=self.timeout)


class IllegalMoveError(ValueError):
    """A move the server returned is illegal in the current position."""
