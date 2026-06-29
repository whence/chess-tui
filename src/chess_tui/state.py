"""Core game state and move logic.

This module is intentionally UI-free so it can be unit-tested without any
Textual/TUI machinery.
"""

from __future__ import annotations

import chess
from chess import Board, Color, Move, Piece, Square


FILES = "abcdefgh"


class IllegalMoveError(ValueError):
    """Raised when a move cannot be applied."""


class BoardState:
    """Wraps a python-chess Board with display-aware helpers."""

    def __init__(self, board: Board | None = None, *, flipped: bool = False) -> None:
        self._board: Board = board if board is not None else Board()
        self.flipped: bool = flipped
        # SAN must be captured at push-time; python-chess refuses to compute
        # SAN for moves that aren't currently legal.
        self._san_stack: list[str] = []

    # ---- construction ----------------------------------------------------

    @classmethod
    def from_fen(cls, fen: str, *, flipped: bool = False) -> "BoardState":
        return cls(Board(fen), flipped=flipped)

    @classmethod
    def from_pgn(cls, pgn_text: str, *, flipped: bool = False) -> "BoardState":
        import io

        import chess.pgn

        game = chess.pgn.read_game(io.StringIO(pgn_text))
        if game is None:
            raise ValueError("no game found in PGN")
        state = cls(game.board(), flipped=flipped)
        for move in game.mainline_moves():
            state.apply_move(move)
        return state

    # ---- queries ---------------------------------------------------------

    @property
    def board(self) -> Board:
        return self._board

    def piece_at(self, square: Square) -> Piece | None:
        return self._board.piece_at(square)

    def turn(self) -> Color:
        return self._board.turn

    def turn_name(self) -> str:
        return "White" if self._board.turn == chess.WHITE else "Black"

    def is_game_over(self) -> bool:
        return self._board.is_game_over()

    def result(self) -> str:
        return self._board.result(claim_draw=True)

    def is_check(self) -> bool:
        return self._board.is_check()

    def is_checkmate(self) -> bool:
        return self._board.is_checkmate()

    def is_stalemate(self) -> bool:
        return self._board.is_stalemate()

    def legal_moves(self) -> list[Move]:
        return list(self._board.legal_moves)

    def legal_moves_from(self, square: Square) -> list[Move]:
        return [m for m in self._board.legal_moves if m.from_square == square]

    def san_for(self, move: Move) -> str:
        return self._board.san(move)

    def san_history(self) -> list[str]:
        return list(self._san_stack)

    def fen(self) -> str:
        return self._board.fen()

    def fullmove_number(self) -> int:
        return self._board.fullmove_number

    # ---- mutation --------------------------------------------------------

    def reset(self) -> None:
        self._board.reset()
        self._san_stack.clear()
        self.flipped = False

    def flip(self) -> None:
        self.flipped = not self.flipped

    def apply_san(self, text: str) -> Move:
        text = text.strip()
        if not text:
            raise IllegalMoveError("empty move")
        try:
            move = self._board.parse_san(text)
        except ValueError as exc:
            # Fall back to UCI notation (e.g. "e2e4", "e7e8q").
            try:
                move = self._board.parse_uci(text)
            except ValueError:
                raise IllegalMoveError(str(exc)) from None
        return self.apply_move(move)

    def apply_move(self, move: Move) -> Move:
        if move not in self._board.legal_moves:
            raise IllegalMoveError(f"illegal move: {move.uci()}")
        self._san_stack.append(self._board.san(move))
        self._board.push(move)
        return move

    # ---- display mapping -------------------------------------------------

    def square_at(self, display_row: int, display_col: int) -> Square:
        """Map a (row, col) in the rendered grid to a python-chess square.

        Row 0 is the top of the displayed board; col 0 is the left edge.
        Respects the flipped orientation.
        """
        if not 0 <= display_row < 8 and 0 <= display_col < 8:
            raise ValueError(f"display position out of range: ({display_row}, {display_col})")
        if self.flipped:
            return display_row * 8 + (7 - display_col)
        return (7 - display_row) * 8 + display_col

    def display_position(self, square: Square) -> tuple[int, int]:
        """Inverse of square_at: square index -> (display_row, display_col).

        Labels stay attached to squares — only the visual orientation rotates
        when the board is flipped.
        """
        rank_idx, file_idx = divmod(square, 8)
        if self.flipped:
            return rank_idx, 7 - file_idx
        return 7 - rank_idx, file_idx

    def file_label(self, display_col: int) -> str:
        return FILES[(7 - display_col) if self.flipped else display_col]

    def rank_label(self, display_row: int) -> str:
        return str((display_row + 1) if self.flipped else (8 - display_row))

    def parse_display_square(self, text: str) -> Square | None:
        """Parse a square label like 'e2' into its chess.Board square index.

        Algebraic notation labels are fixed to squares, so this is independent
        of display orientation. Returns None if the input isn't a square label.
        """
        text = text.strip().lower()
        if len(text) != 2:
            return None
        file_char, rank_char = text[0], text[1]
        if file_char not in FILES or rank_char not in "12345678":
            return None
        file_idx = FILES.index(file_char)
        rank_idx = int(rank_char) - 1
        return rank_idx * 8 + file_idx