"""Headless integration tests for the Textual TUI app.

These run via Textual's Pilot — no human or terminal required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import chess
import pytest

from chess_tui.app import Cell, ChessApp, Legend, TextLine
from chess_tui.state import BoardState
from chess_tui.themes import THEME
from textual.widgets import Input, ListView


# ---- helpers ----------------------------------------------------------------


@asynccontextmanager
async def run_app(
    state: BoardState | None = None,
) -> AsyncIterator[tuple[ChessApp, object]]:
    """Yield (app, pilot) inside a live Textual test context."""
    app = ChessApp(state)
    async with app.run_test() as pilot:
        yield app, pilot


def piece_at_display(app: ChessApp, row: int, col: int) -> str:
    cell = app.query_one(f"#cell-{row}-{col}", Cell)
    return cell.glyph


def title_text(app: ChessApp) -> str:
    return app.query_one("#title", TextLine).text_value


def status_text(app: ChessApp) -> str:
    return app.query_one("#status", TextLine).text_value


def move_labels(app: ChessApp) -> list[str]:
    move_list = app.query_one("#move-list", ListView)
    return [str(item.children[0].render()) for item in move_list.children]


def focus_input(app: ChessApp) -> None:
    """Move focus to the move-input so typed keys go there."""
    app.query_one("#move-input", Input).focus()


# ---- startup ----------------------------------------------------------------


async def test_app_starts_and_renders_starting_position() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        # Row 0 (rank 8) — black back rank.
        assert piece_at_display(app, 0, 0) == "♜"  # a8 black rook
        assert piece_at_display(app, 0, 4) == "♚"  # e8 black king
        # Row 7 (rank 1) — white back rank.
        assert piece_at_display(app, 7, 0) == "♖"  # a1 white rook
        assert piece_at_display(app, 7, 4) == "♔"  # e1 white king
        # Row 6 (rank 2) — white pawns.
        for col in range(8):
            assert piece_at_display(app, 6, col) == "♙"


async def test_app_initial_title_is_white_to_move() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        assert "White to move" in title_text(app)


async def test_title_background_reflects_turn() -> None:
    """White's turn → white bg / dark text; black's turn → dark grey bg / white text."""
    async with run_app() as (app, pilot):
        await pilot.pause()
        title = app.query_one("#title", TextLine)
        # White to move at start: white background, dark text.
        bg = title.styles.background
        assert (bg.r, bg.g, bg.b) == (255, 255, 255)
        color = title.styles.color
        assert (color.r, color.g, color.b) == (0, 0, 0)
        # Black to move after 1.e4: dark grey background, light text.
        app._state.apply_san("e4")
        app.refresh_all()
        await pilot.pause()
        bg = title.styles.background
        # Dark grey (we use #3a3a3a) — every channel < 80.
        assert bg.r < 80 and bg.g < 80 and bg.b < 80
        color = title.styles.color
        # Light text on dark background — every channel > 200.
        assert color.r > 200 and color.g > 200 and color.b > 200


# ---- focus & move-list-as-default ------------------------------------------


async def test_default_focus_is_move_list_not_input() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        move_list = app.query_one("#move-list", ListView)
        move_input = app.query_one("#move-input", Input)
        assert move_list.has_focus
        assert not move_input.has_focus


async def test_enter_on_move_list_applies_highlighted_move() -> None:
    """The default action for Enter should be 'pick the highlighted move'
    via the move list, not the move input. The first item in the list is
    the first legal move python-chess yields (Nh3 at the start)."""
    async with run_app() as (app, pilot):
        await pilot.pause()
        # Move list has focus by default; Enter picks the highlighted item.
        await pilot.press("enter")
        await pilot.pause()
        assert app._state.san_history() == ["Nh3"]


# ---- move input -------------------------------------------------------------


async def test_typing_san_move_advances_position() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        focus_input(app)
        await pilot.press("e", "2", "e", "4", "enter")
        await pilot.pause()
        assert piece_at_display(app, 4, 4) == "♙"
        assert piece_at_display(app, 6, 4) == " "  # e2 empty
        assert "Black to move" in title_text(app)


async def test_typing_uci_move_works() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        focus_input(app)
        await pilot.press("e", "2", "e", "4", "enter")
        await pilot.pause()
        await pilot.press("e", "7", "e", "5", "enter")
        await pilot.pause()
        assert piece_at_display(app, 3, 4) == "♟"


async def test_typing_two_char_pawn_move_works() -> None:
    """Regression: 2-char SAN pawn moves like 'e4' were getting intercepted
    by the from-square query because both look like 'e4'."""
    async with run_app() as (app, pilot):
        await pilot.pause()
        focus_input(app)
        await pilot.press("e", "4", "enter")
        await pilot.pause()
        assert app._state.san_history() == ["e4"], (
            f"expected ['e4'], got {app._state.san_history()}"
        )


async def test_illegal_move_shows_error_and_does_not_advance() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        focus_input(app)
        await pilot.press("e", "2", "e", "5", "enter")  # illegal pawn jump
        await pilot.pause()
        assert piece_at_display(app, 6, 4) == "♙"
        # Status should reflect an error, not the normal "Move N • ..." line.
        status = status_text(app)
        assert not status.startswith("Move "), f"expected error, got {status!r}"
        assert "e2e5" in status


async def test_input_clears_after_submission() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        focus_input(app)
        inp = app.query_one("#move-input", Input)
        await pilot.press("e", "4", "enter")
        await pilot.pause()
        assert inp.value == ""


# ---- flip -------------------------------------------------------------------


async def test_f_key_flips_board() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        assert piece_at_display(app, 0, 0) == "♜"  # a8 black rook
        app.action_flip()
        await pilot.pause()
        assert piece_at_display(app, 0, 7) == "♖"  # h1 white rook
        assert piece_at_display(app, 7, 0) == "♜"  # h8 black rook


async def test_f_key_again_flips_back() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        app.action_flip()
        await pilot.pause()
        app.action_flip()
        await pilot.pause()
        assert piece_at_display(app, 0, 0) == "♜"


# ---- reset ------------------------------------------------------------------


async def test_r_key_resets_board() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        await pilot.press("e", "4", "enter")
        await pilot.press("e", "5", "enter")
        await pilot.pause()
        app.action_reset()
        await pilot.pause()
        assert piece_at_display(app, 6, 4) == "♙"
        assert piece_at_display(app, 1, 4) == "♟"
        assert app._state.flipped is False


# ---- move list --------------------------------------------------------------


async def test_move_list_is_populated_with_legal_moves() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        labels = move_labels(app)
        assert "e4" in labels
        assert "Nf3" in labels
        assert "Nc3" in labels


async def test_typing_from_square_shows_destinations() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        focus_input(app)
        await pilot.press("e", "2", "enter")
        await pilot.pause()
        labels = move_labels(app)
        assert "e3" in labels
        assert "e4" in labels


# ---- checkmate / game over --------------------------------------------------


async def test_game_over_displays_result() -> None:
    state = BoardState.from_pgn("1. f3 e5 2. g4 Qh4#")
    async with run_app(state) as (app, pilot):
        await pilot.pause()
        assert "Game over" in title_text(app)
        assert "0-1" in title_text(app)


# ---- legend -----------------------------------------------------------------


async def test_legend_shows_white_and_black_pieces() -> None:
    from io import StringIO
    from rich.console import Console
    from rich.table import Table as RichTable

    async with run_app() as (app, pilot):
        await pilot.pause()
        legend = app.query_one(Legend)
        # Find the Rich Table on the widget.
        table = None
        for attr in dir(legend):
            if not attr.startswith("_"):
                continue
            val = getattr(legend, attr, None)
            if isinstance(val, RichTable):
                table = val
                break
        assert table is not None, "Legend widget has no Rich Table attached"
        buf = StringIO()
        Console(file=buf, width=80, force_terminal=False, color_system=None).print(table)
        text = buf.getvalue()
        assert "White" in text
        assert "Black" in text
        for g in "♙♘♗♖♕♔♟♞♝♜♛♚":
            assert g in text, f"legend missing glyph {g!r}"


# ---- board centering -------------------------------------------------------


async def test_board_is_horizontally_centered_in_board_area() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        ba = app.query_one("#board-area")
        board = app.query_one("#board")
        # The board (24 chars wide) should sit in the middle of board-area.
        expected_x = ba.region.x + (ba.region.width - board.region.width) // 2
        assert board.region.x == expected_x, (
            f"board at x={board.region.x}, expected x={expected_x} "
            f"(ba width={ba.region.width})"
        )


# ---- board widget refresh ---------------------------------------------------


async def test_board_widget_refresh_reflects_state_changes() -> None:
    state = BoardState()
    async with run_app(state) as (app, pilot):
        await pilot.pause()
        state.apply_san("e4")
        state.apply_san("e5")
        state.apply_san("Nf3")
        app.refresh_all()
        await pilot.pause()
        assert piece_at_display(app, 5, 5) == "♘"


# ---- themes -----------------------------------------------------------------


async def test_default_theme_is_checkered() -> None:
    """The only theme is the checkered palette from command-line-chess:
    - (row+col) % 2 == 0 → #769656 (dark green)
    - (row+col) % 2 == 1 → #BACA44 (light olive)
    """
    async with run_app() as (app, pilot):
        await pilot.pause()
        board = app.query_one("#board")
        assert board.theme is THEME
        # Cell (0, 0) → tileColors[0] = #769656.
        cell_00 = app.query_one("#cell-0-0", Cell)
        r, g, b = (
            cell_00.styles.background.r,
            cell_00.styles.background.g,
            cell_00.styles.background.b,
        )
        assert (r, g, b) == (0x76, 0x96, 0x56), (
            f"expected #769656, got rgb({r}, {g}, {b})"
        )
        # Cell (0, 1) → tileColors[1] = #BACA44.
        cell_01 = app.query_one("#cell-0-1", Cell)
        r, g, b = (
            cell_01.styles.background.r,
            cell_01.styles.background.g,
            cell_01.styles.background.b,
        )
        assert (r, g, b) == (0xBA, 0xCA, 0x44), (
            f"expected #BACA44, got rgb({r}, {g}, {b})"
        )
        # And the rendered SVG should carry those colors too.
        svg = app.export_screenshot()
        assert "769656" in svg.lower()
        assert "baca44" in svg.lower()