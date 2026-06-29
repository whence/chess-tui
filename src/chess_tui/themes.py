"""Theme for the board and pieces.

Adopted from marcusbuffett/command-line-chess's --checkered theme:
https://github.com/marcusbuffett/command-line-chess (see ``src/Board.py``,
``Board.tileColors``).

  tileColors[0] → (x + y) % 2 == 0  (top-left, a8)  → #769656 (dark green)
  tileColors[1] → (x + y) % 2 == 1                   → #BACA44 (light olive)

Piece colors default to white/black; the upstream ``--white``/``--black``
CLI flags are not modelled here yet.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    light_square: str  # hex, used when (row+col) % 2 == 0
    dark_square: str   # hex, used when (row+col) % 2 == 1
    light_piece: str   # fg for white pieces
    dark_piece: str    # fg for black pieces


THEME = Theme(
    name="checkered",
    light_square="#769656",
    dark_square="#BACA44",
    light_piece="white",
    dark_piece="black",
)