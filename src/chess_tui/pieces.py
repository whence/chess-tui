"""Unicode glyph mapping for chess pieces.

All pieces use solid (filled) glyphs. Color is applied via CSS.
"""

from __future__ import annotations

import chess

# Solid glyphs for all pieces (used for both white and black)
SOLID_GLYPHS: dict[int, str] = {
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
    return SOLID_GLYPHS[piece.piece_type]