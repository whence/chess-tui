"""Headless integration tests for the Textual TUI app.

These run via Textual's Pilot — no human or terminal required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import chess
import pytest

from chess_tui.app import Cell, ChessApp, FileBar, RankBar, TextLine
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


def piece_at_display(app: ChessApp, row: int, col: int) -> chess.Piece | None:
    """Return the :class:`chess.Piece` currently shown at the given cell,
    or ``None`` if the cell is empty."""
    cell = app.query_one(f"#cell-{row}-{col}", Cell)
    return cell.piece


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
        assert piece_at_display(app, 0, 0) == chess.Piece(chess.ROOK, chess.BLACK)  # a8
        assert piece_at_display(app, 0, 4) == chess.Piece(chess.KING, chess.BLACK)  # e8
        # Row 7 (rank 1) — white back rank.
        assert piece_at_display(app, 7, 0) == chess.Piece(chess.ROOK, chess.WHITE)  # a1
        assert piece_at_display(app, 7, 4) == chess.Piece(chess.KING, chess.WHITE)  # e1
        # Row 6 (rank 2) — white pawns.
        for col in range(8):
            assert piece_at_display(app, 6, col) == chess.Piece(chess.PAWN, chess.WHITE)


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
    via the move list, not the move input. The list is sorted alphabetically
    by SAN (case-insensitive), so the first item is 'a3' at the start."""
    async with run_app() as (app, pilot):
        await pilot.pause()
        # Move list has focus by default; Enter picks the highlighted item.
        await pilot.press("enter")
        await pilot.pause()
        assert app._state.san_history() == ["a3"]


# ---- move input -------------------------------------------------------------


async def test_typing_san_move_advances_position() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        focus_input(app)
        await pilot.press("e", "2", "e", "4", "enter")
        await pilot.pause()
        assert piece_at_display(app, 4, 4) == chess.Piece(chess.PAWN, chess.WHITE)
        assert piece_at_display(app, 6, 4) is None  # e2 empty
        assert "Black to move" in title_text(app)


async def test_typing_uci_move_works() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        focus_input(app)
        await pilot.press("e", "2", "e", "4", "enter")
        await pilot.pause()
        await pilot.press("e", "7", "e", "5", "enter")
        await pilot.pause()
        assert piece_at_display(app, 3, 4) == chess.Piece(chess.PAWN, chess.BLACK)


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
        assert piece_at_display(app, 6, 4) == chess.Piece(chess.PAWN, chess.WHITE)
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
        assert piece_at_display(app, 0, 0) == chess.Piece(chess.ROOK, chess.BLACK)  # a8
        app.action_flip()
        await pilot.pause()
        assert piece_at_display(app, 0, 7) == chess.Piece(chess.ROOK, chess.WHITE)  # h1
        assert piece_at_display(app, 7, 0) == chess.Piece(chess.ROOK, chess.BLACK)  # h8


async def test_f_key_again_flips_back() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        app.action_flip()
        await pilot.pause()
        app.action_flip()
        await pilot.pause()
        assert piece_at_display(app, 0, 0) == chess.Piece(chess.ROOK, chess.BLACK)


# ---- reset ------------------------------------------------------------------


async def test_r_key_resets_board() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        await pilot.press("e", "4", "enter")
        await pilot.press("e", "5", "enter")
        await pilot.pause()
        app.action_reset()
        await pilot.pause()
        assert piece_at_display(app, 6, 4) == chess.Piece(chess.PAWN, chess.WHITE)
        assert piece_at_display(app, 1, 4) == chess.Piece(chess.PAWN, chess.BLACK)
        assert app._state.flipped is False


# ---- move list --------------------------------------------------------------


async def test_move_list_is_populated_with_legal_moves() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        labels = move_labels(app)
        assert "e4" in labels
        assert "Nf3" in labels
        assert "Nc3" in labels


async def test_move_list_is_sorted_alphabetically_by_san() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        labels = move_labels(app)
        # Sort is case-insensitive: 'a3' < 'Na3' < 'Nc3' < 'Nf3' < …
        assert labels == sorted(labels, key=str.lower), (
            f"move list not sorted: {labels}"
        )
        # Sanity across the case boundary.
        assert labels.index("a3") < labels.index("Na3")
        assert labels.index("Na3") < labels.index("Nc3")
        assert labels.index("Nc3") < labels.index("Nf3")
        assert labels.index("Nf3") < labels.index("Nh3")


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


# ---- board centering -------------------------------------------------------


async def test_board_block_is_horizontally_centered_in_board_area() -> None:
    """The whole board block (rank column + cells + file bar = 51 wide)
    should sit in the middle of board-area, or left-aligned at x=0 if
    it's wider than board-area (which happens on narrow terminals)."""
    async with run_app() as (app, pilot):
        await pilot.pause()
        ba = app.query_one("#board-area")
        bi = app.query_one("#board-inner")
        # board-inner is 51 wide (3 for rank + 48 cells); center it.
        # When the board is wider than board-area (e.g. 80-col terminal
        # with the 32-col side panel), the math gives a negative offset
        # and the layout just pins it to x=0.
        expected_x = max(0, ba.region.x + (ba.region.width - bi.region.width) // 2)
        assert bi.region.x == expected_x, (
            f"board-inner at x={bi.region.x}, expected x={expected_x} "
            f"(ba width={ba.region.width}, bi width={bi.region.width})"
        )


# ---- coordinates -----------------------------------------------------------


def _bar_text(widget) -> str:
    from io import StringIO
    from rich.console import Console

    buf = StringIO()
    # Wide enough to hold the rank/file bars (file bar is 43 chars wide).
    Console(file=buf, width=80, force_terminal=False, color_system=None).print(widget.render())
    return buf.getvalue()


async def test_rank_bar_shows_8_to_1_unflipped() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        rb = app.query_one("#ranks-left", RankBar)
        text = _bar_text(rb)
        lines = text.splitlines()
        # 8 ranks × 3 lines each (top padding, label, bottom padding).
        # The label sits on the middle line of its 3-line cell block.
        expected: list[str] = []
        for n in range(8, 0, -1):
            expected.extend(["   ", f"  {n}", "   "])
        assert lines == expected, lines


async def test_rank_bar_flips_to_1_to_8() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        app.action_flip()
        await pilot.pause()
        rb = app.query_one("#ranks-left", RankBar)
        text = _bar_text(rb)
        lines = text.splitlines()
        expected: list[str] = []
        for n in range(1, 9):
            expected.extend(["   ", f"  {n}", "   "])
        assert lines == expected, lines


async def test_file_bar_shows_a_to_h_unflipped() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        fb = app.query_one("#files-bot", FileBar)
        text = _bar_text(fb).rstrip("\n")
        # 51 chars total: 3-char blank + 8 file labels, each in a 6-char cell
        assert len(text) == 51
        assert text == "   " + "".join(f"  {c}   " for c in "abcdefgh"), text


async def test_file_bar_flips_to_h_to_a() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        app.action_flip()
        await pilot.pause()
        fb = app.query_one("#files-bot", FileBar)
        text = _bar_text(fb).rstrip("\n")
        assert text == "   " + "".join(f"  {c}   " for c in "hgfedcba"), text


async def test_file_labels_align_with_board_cells() -> None:
    """Each file label is centered in a 6-char cell whose center matches
    the center of the corresponding board cell."""
    async with run_app() as (app, pilot):
        await pilot.pause()
        fb = app.query_one("#files-bot", FileBar)
        bw = app.query_one("#board")
        text = _bar_text(fb).rstrip("\n")
        # File label at index 3 + c*6 + 2 should match board cell c center
        # (board cell c is at x=bw.region.x + c*6 + 2 in a 6-char cell).
        for c in range(8):
            label_char = text[3 + c * 6 + 2]
            assert label_char == "abcdefgh"[c], (
                f"file {c} mismatch: got {label_char!r}"
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
        assert piece_at_display(app, 5, 5) == chess.Piece(chess.KNIGHT, chess.WHITE)


# ---- themes -----------------------------------------------------------------


async def test_default_theme_is_checkered() -> None:
    """The default theme is the two-tone palette defined in :mod:`chess_tui.themes`:
    - (row+col) % 2 == 0 → THEME.light_square
    - (row+col) % 2 == 1 → THEME.dark_square
    """
    async with run_app() as (app, pilot):
        await pilot.pause()
        board = app.query_one("#board")
        assert board.theme is THEME
        # Cell (0, 0) — light square.
        cell_00 = app.query_one("#cell-0-0", Cell)
        r, g, b = (
            cell_00.styles.background.r,
            cell_00.styles.background.g,
            cell_00.styles.background.b,
        )
        light = THEME.light_square.lstrip("#")
        assert (r, g, b) == (int(light[0:2], 16), int(light[2:4], 16), int(light[4:6], 16)), (
            f"expected #{light}, got rgb({r}, {g}, {b})"
        )
        # Cell (0, 1) — dark square.
        cell_01 = app.query_one("#cell-0-1", Cell)
        r, g, b = (
            cell_01.styles.background.r,
            cell_01.styles.background.g,
            cell_01.styles.background.b,
        )
        dark = THEME.dark_square.lstrip("#")
        assert (r, g, b) == (int(dark[0:2], 16), int(dark[2:4], 16), int(dark[4:6], 16)), (
            f"expected #{dark}, got rgb({r}, {g}, {b})"
        )


# ---- --opening integration --------------------------------------------------


async def test_opening_kwarg_sets_title_and_state() -> None:
    """Passing an Opening to ChessApp should set the board position
    and show the opening name + ECO in the title bar."""
    from chess_tui.openings import resolve as resolve_opening
    opening = resolve_opening("B90")
    state = BoardState(board=opening.to_board())
    app = ChessApp(state=state, opening=opening)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Title includes the opening name and ECO.
        title = title_text(app)
        assert opening.name in title
        assert opening.eco in title
        # And the board is actually at the opening's position, not the
        # initial one.  Verify via the FEN stored in state.
        assert app._state.fen() == opening.to_fen()


async def test_no_opening_means_default_title() -> None:
    """No opening kwarg → title should be the plain ``Chess TUI — ...``."""
    async with run_app() as (app, pilot):
        await pilot.pause()
        assert app._opening is None
        assert "Najdorf" not in title_text(app)
        assert "(B" not in title_text(app)


async def test_opening_used_as_starting_position_for_moves() -> None:
    """After starting from Najdorf, the legal move list should contain
    the moves natural to that position (e.g. white's knight on d4 has
    many options), and the SAN history should be empty until we play."""
    from chess_tui.openings import resolve as resolve_opening
    opening = resolve_opening("B90")
    state = BoardState(board=opening.to_board())
    app = ChessApp(state=state, opening=opening)
    async with app.run_test() as pilot:
        await pilot.pause()
        # No moves played yet.
        assert app._state.san_history() == []
        # White is to move (the Najdorf PGN ends with black's a6).
        assert app._state.turn() == chess.WHITE


async def test_opening_populates_san_history_for_network_players() -> None:
    """Maia-3's --use-history feature (and any other history-aware
    network player) needs the SAN history to be populated when the
    game starts from an opening, otherwise it receives
    ``{"fen": <opening FEN>, "moves": []}`` and silently falls back to
    no-context mode.

    We verify that ``BoardState.from_pgn(opening.pgn)`` (the path used
    by ``--opening``) populates the SAN stack the same way
    ``apply_move`` would.
    """
    from chess_tui.openings import resolve as resolve_opening
    opening = resolve_opening("B90")
    # Replay the opening through BoardState — same call as main().
    state = BoardState.from_pgn(opening.pgn)
    history = state.san_history()
    # The B90 canonical line is the 5 Najdorf moves (10 plies).
    assert history == [
        "e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6",
    ]
    # And the position is the opening's FEN, not the standard start.
    assert state.fen() == opening.to_fen()
    # Now construct the app the same way main() does, and verify the
    # state that gets passed to network players still has the history.
    app = ChessApp(state=state, opening=opening)
    assert app._state.san_history() == history