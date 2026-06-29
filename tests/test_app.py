"""Headless integration tests for the Textual TUI app.

These run via Textual's Pilot — no human or terminal required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import chess
import pytest

from chess_tui.app import Cell, ChessApp, TextLine
from chess_tui.state import BoardState
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


# ---- move input -------------------------------------------------------------


async def test_typing_san_move_advances_position() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        await pilot.press("e", "2", "e", "4", "enter")
        await pilot.pause()
        assert piece_at_display(app, 4, 4) == "♙"
        assert piece_at_display(app, 6, 4) == " "  # e2 empty
        assert "Black to move" in title_text(app)


async def test_typing_uci_move_works() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
        await pilot.press("e", "2", "e", "4", "enter")
        await pilot.pause()
        await pilot.press("e", "7", "e", "5", "enter")
        await pilot.pause()
        assert piece_at_display(app, 3, 4) == "♟"


async def test_illegal_move_shows_error_and_does_not_advance() -> None:
    async with run_app() as (app, pilot):
        await pilot.pause()
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