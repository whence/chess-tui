"""Color themes for the board and pieces.

Adopted from cli-chess (https://github.com/trevorbayless/cli-chess) which uses
prompt_toolkit-style spec strings (e.g. ``bg:cadetblue``, ``fg:white``). We
translate them to equivalent Rich style fragments for our Table-based renderer.

Override by editing :data:`DEFAULT` or by writing your own dataclass — see
``src/cli_chess/utils/styles.py`` in the upstream project for the full set of
keys cli-chess exposes (``light-square``, ``dark-square``, ``last-move``,
``pre-move``, ``in-check``, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass


# Translation table for CSS-named colors that Rich doesn't recognize. cli-chess
# uses HTML/CSS color names (see https://www.w3.org/TR/css-color-4/#named-colors);
# Rich only knows the basic 16 ANSI names. Anything not in this table will be
# silently dropped on render — add entries as needed.
_CSS_COLORS: dict[str, str] = {
    "cadetblue": "#5F9EA0",
    "darkslateblue": "#483D8B",
    "yellowgreen": "#9ACD32",
    "darkorange": "#FF8C00",
    "darkgreen": "#006400",
    "darkred": "#8B0000",
    "darkcyan": "#008B8B",
    "darkmagenta": "#8B008B",
    "dimgray": "#696969",
    "slategray": "#708090",
    "mediumturquoise": "#48D1CC",
    "limegreen": "#32CD32",
    "orangered": "#FF4500",
    "lightgray": "#D3D3D3",
}


def _resolve_color(name: str) -> str:
    """Return a Rich-friendly color spec, translating CSS names to hex."""
    lower = name.lower()
    return _CSS_COLORS.get(lower, lower)


def _pt_to_rich(spec: str) -> str:
    """Convert a prompt_toolkit style spec (e.g. ``bg:cadetblue``) to a
    Rich style fragment that we can hand to :class:`rich.text.Text`.

    Handles ``bg:<color>``, ``fg:<color>``, and ``bold``. Unknown parts are
    dropped silently so an upstream change in cli-chess won't crash us.
    """
    parts: list[str] = []
    for token in spec.split():
        if ":" not in token:
            parts.append(token)  # bare modifiers like "bold" or "italic"
            continue
        key, value = token.split(":", 1)
        if key == "bg":
            parts.append(f"on {_resolve_color(value)}")
        elif key == "fg":
            parts.append(_resolve_color(value))
        # anything else (e.g. "noinherit") is dropped
    return " ".join(parts)


@dataclass(frozen=True)
class Theme:
    """A single color theme for the board and pieces.

    Fields mirror cli-chess's default style keys. Only square backgrounds and
    piece foregrounds are wired up in the renderer today; the other keys
    (``last_move``, ``in_check``, ``label``) are kept so we can extend without
    breaking the dataclass shape.
    """

    name: str
    light_square: str  # bg spec
    dark_square: str  # bg spec
    light_piece: str  # fg spec
    dark_piece: str  # fg spec
    label: str = "fg:gray"  # rank/file labels
    last_move: str = "bg:yellowgreen"
    in_check: str = "bg:red"

    def light_square_bg(self) -> str:
        return _pt_to_rich(self.light_square)

    def dark_square_bg(self) -> str:
        return _pt_to_rich(self.dark_square)

    def light_piece_fg(self) -> str:
        return _pt_to_rich(self.light_piece)

    def dark_piece_fg(self) -> str:
        return _pt_to_rich(self.dark_piece)

    def label_style(self) -> str:
        return _pt_to_rich(self.label)


# Adopted verbatim from cli-chess's default style.
# See https://github.com/trevorbayless/cli-chess/blob/master/src/cli_chess/utils/styles.py
DEFAULT = Theme(
    name="cli-chess default",
    light_square="bg:cadetblue",
    dark_square="bg:darkslateblue",
    light_piece="fg:white",
    dark_piece="fg:black",
)


# Adopted from marcusbuffett/command-line-chess --checkered theme.
# See https://github.com/marcusbuffett/command-line-chess — `src/Board.py`,
# `Board.tileColors`. In that project:
#   tileColors[0] applies to squares where (x + y) % 2 == 0  (top-left, a8)
#   tileColors[1] applies to squares where (x + y) % 2 == 1
# Piece colors default to white/black; --white/--black CLI flags override.
CHECKERED = Theme(
    name="clc checkered",
    light_square="bg:#769656",  # tileColors[0]: dark green, (x+y) % 2 == 0
    dark_square="bg:#BACA44",  # tileColors[1]: light olive, (x+y) % 2 == 1
    light_piece="fg:white",
    dark_piece="fg:black",
)