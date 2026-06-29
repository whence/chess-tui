"""Player abstraction: local (human) and network (HTTP) players.

A :class:`Player` is something that, given the current board position, can
return the next move. :class:`LocalPlayer` is a marker for "the human drives
this side via the TUI" (no logic — the TUI's own input handling does the work).
:class:`NetworkPlayer` POSTs the current FEN to a remote server and parses
the returned SAN into a move.
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

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
    """

    color: chess.Color
    url: str
    timeout: float = 30.0

    async def choose_move(self, board: chess.Board) -> chess.Move:
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
        return move

    def _fetch_san(self, fen: str) -> str:
        return net.request_move(self.url, fen, timeout=self.timeout)


class IllegalMoveError(ValueError):
    """A move the server returned is illegal in the current position."""