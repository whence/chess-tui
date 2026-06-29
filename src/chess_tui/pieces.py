"""Unicode glyph mapping for chess pieces."""

from __future__ import annotations

import chess

WHITE_GLYPHS: dict[int, str] = {
    chess.PAWN: "♙",
    chess.KNIGHT: "♘",
    chess.BISHOP: "♗",
    chess.ROOK: "♖",
    chess.QUEEN: "♕",
    chess.KING: "♔",
}

BLACK_GLYPHS: dict[int, str] = {
    chess.PAWN: "♟",
    chess.KNIGHT: "♞",
    chess.BISHOP: "♝",
    chess.ROOK: "♜",
    chess.QUEEN: "♛",
    chess.KING: "♚",
}


def glyph(piece: chess.Piece | None) -> str:
    """Return the unicode glyph for a piece, or a blank for empty squares."""
    if piece is None:
        return " "
    table = WHITE_GLYPHS if piece.color == chess.WHITE else BLACK_GLYPHS
    return table[piece.piece_type]