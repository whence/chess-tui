"""Chess piece rendering.

Two public entry points:

* :func:`render_piece` — returns a fully-opaque 64x64 RGB ``PIL.Image.Image``
  of a piece composited onto a chosen background colour.  This is what
  :class:`~chess_tui.app.Cell` hands to ``textual_image.widget.AutoImage``
  so the protocol-appropriate renderer (Sixel / TGP / half-cell / Unicode)
  gets a flat image and the cell highlight colour shows through
  consistently on every terminal.

* :func:`glyph` — the Unicode glyph for a piece.  Used in compact text
  contexts that don't justify a raster image (the move-list side panel,
  the promotion piece selector).

The 12 piece images are bundled under ``src/chess_tui/pieces/`` as
``<color><piece>.png`` (e.g. ``wK.png``, ``bP.png``).  See
``src/chess_tui/pieces/README.md`` and ``THIRD_PARTY_NOTICES.txt`` in the
project root for the Cburnett attribution.
"""

from __future__ import annotations

import chess
from PIL import Image
from importlib.resources import files


# ---- compact text --------------------------------------------------------


# Solid glyphs (used for both white and black in compact text contexts).
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


# ---- raster image rendering ----------------------------------------------


# Map (piece_type, color) -> filename under src/chess_tui/pieces/.
_PIECE_FILES: dict[tuple[int, int], str] = {
    (chess.PAWN,   chess.WHITE): "wP.png",
    (chess.KNIGHT, chess.WHITE): "wN.png",
    (chess.BISHOP, chess.WHITE): "wB.png",
    (chess.ROOK,   chess.WHITE): "wR.png",
    (chess.QUEEN,  chess.WHITE): "wQ.png",
    (chess.KING,   chess.WHITE): "wK.png",
    (chess.PAWN,   chess.BLACK): "bP.png",
    (chess.KNIGHT, chess.BLACK): "bN.png",
    (chess.BISHOP, chess.BLACK): "bB.png",
    (chess.ROOK,   chess.BLACK): "bR.png",
    (chess.QUEEN,  chess.BLACK): "bQ.png",
    (chess.KING,   chess.BLACK): "bK.png",
}


def _load_piece_images() -> dict[tuple[int, int], Image.Image]:
    """Load all 12 piece images as RGBA, keeping their data resident in memory."""
    pieces_dir = files("chess_tui").joinpath("pieces")
    images: dict[tuple[int, int], Image.Image] = {}
    for key, filename in _PIECE_FILES.items():
        with pieces_dir.joinpath(filename).open("rb") as f:
            img = Image.open(f).convert("RGBA")
            img.load()  # force data into memory before the BytesIO goes out of scope
            images[key] = img
    return images


# Loaded once at import; the resulting dict is read-only for the life of the
# process.  ``alpha_composite`` is non-mutating, so the cached RGBA images are
# safe to reuse across calls.
_PIECE_IMAGES: dict[tuple[int, int], Image.Image] = _load_piece_images()


def piece_size() -> tuple[int, int]:
    """Source image size (width, height) in pixels — the same for every piece."""
    sample = next(iter(_PIECE_IMAGES.values()))
    return sample.width, sample.height


def render_piece(piece: chess.Piece, *, bg: str) -> Image.Image:
    """Return an opaque RGB image of ``piece`` on a ``bg``-coloured square.

    The returned image is a fresh ``PIL.Image.Image`` sized like the source
    PNG (currently 64x64) and is safe for ``textual_image`` to use directly:
    alpha has been flattened against ``bg`` so the renderer doesn't have to
    care about transparency.

    Args:
        piece: The piece to draw.  Determines the source image.
        bg:    Background colour as ``#RRGGBB``.  This is the highlight
               colour for the cell — the cell's "empty" area will show
               this colour.
    """
    src = _PIECE_IMAGES[(piece.piece_type, piece.color)]
    r, g, b = _hex_to_rgb(bg)
    bg_layer = Image.new("RGBA", src.size, (r, g, b, 255))
    return Image.alpha_composite(bg_layer, src).convert("RGB")


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected #RRGGBB, got {hex_color!r}")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
