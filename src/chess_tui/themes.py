"""Theme for the board and pieces.

Adopted from marcusbuffett/command-line-chess's --checkered theme:
https://github.com/marcusbuffett/command-line-chess (see ``src/Board.py``,
``Board.tileColors``).

  tileColors[0] → (x + y) % 2 == 0  (top-left, a8)  → #769656 (dark green)
  tileColors[1] → (x + y) % 2 == 1                   → #BACA44 (light olive)

Cell highlights are bg colours applied by :func:`render_piece` from
:mod:`chess_tui.pieces` — the piece image is alpha-composited onto the
chosen highlight so the protocol-appropriate renderer (Sixel / TGP /
half-cell / Unicode) just gets a flat opaque image to draw.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    light_square: str  # bg for (row+col) % 2 == 0
    dark_square: str   # bg for (row+col) % 2 == 1
    # Cell-state highlights — alpha-composited under each piece image.
    selected: str      # the piece the user has selected to move
    move_from: str     # the from-square of the last move
    move_to: str       # the to-square   of the last move
    cursor: str        # the cell under the cursor
    cursor_sel: str    # cursor on the selected piece's square


THEME = Theme(
    name="classic",
    light_square="#dfc492",
    dark_square="#b58863",
    selected="#FFFF00",   # yellow
    move_from="#90EE90",  # light green
    move_to="#90EE90",    # light green (same as move_from)
    cursor="#00CED1",     # dark turquoise
    cursor_sel="#FFD700", # gold
)
