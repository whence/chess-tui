"""Pilot tests for the standalone opening picker (chess_tui.opening_picker).

These drive the Textual app headlessly: type into the search box,
verify the list filters live, pick a row with Enter, and cancel with
Escape.
"""

from __future__ import annotations

import pytest

from chess_tui.opening_picker import OpeningPickerApp
from chess_tui.openings import Opening
from textual.widgets import Input, ListView


def _row_texts(app: OpeningPickerApp) -> list[str]:
    lv = app.query_one("#opening-picker-list", ListView)
    return [
        str(item.children[0].render())
        for item in lv.children
        if hasattr(item, "children")
    ]


@pytest.mark.asyncio
async def test_empty_query_seeds_browse_view() -> None:
    """With no query the list shows the first page of the catalog."""
    app = OpeningPickerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        lv = app.query_one("#opening-picker-list", ListView)
        # Seeded with MAX_ROWS rows from the bundled catalog.
        assert len([c for c in lv.children if hasattr(c, "children")]) == 50


@pytest.mark.asyncio
async def test_typing_filters_the_list_live() -> None:
    """Each keystroke narrows the list to matches for the query."""
    app = OpeningPickerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Type a narrow query that matches exactly one opening.
        await pilot.press("b", "o", "n", "g", "c", "l", "o", "u", "d")
        await pilot.pause()
        lv = app.query_one("#opening-picker-list", ListView)
        rows = _row_texts(app)
        assert len(rows) == 1
        assert "Bongcloud" in rows[0]
        # The single match is a Bongcloud Attack opening.
        assert app._filtered[0].eco == "C20"


@pytest.mark.asyncio
async def test_enter_returns_the_highlighted_opening() -> None:
    """Enter confirms the highlighted row and exits with that Opening."""
    app = OpeningPickerApp()
    result: list[Opening | None] = []
    async with app.run_test() as pilot:
        await pilot.press("b", "o", "n", "g", "c", "l", "o", "u", "d")
        await pilot.pause()
        highlighted = app._filtered[0]
        await pilot.press("enter")
        # run_test() captures the exit value on app.return_value.
        result.append(app.return_value)
    chosen = result[0]
    assert chosen is not None
    assert chosen == highlighted
    assert chosen.name == "Bongcloud Attack"


@pytest.mark.asyncio
async def test_arrow_keys_move_highlight_and_enter_picks() -> None:
    """Down moves the highlight; Enter picks the highlighted row."""
    app = OpeningPickerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Search something with many matches.
        await pilot.press("s", "i", "c", "i", "l", "i", "a", "n")
        await pilot.pause()
        lv = app.query_one("#opening-picker-list", ListView)
        assert lv.index == 0
        await pilot.press("down")
        await pilot.pause()
        assert lv.index == 1
        expected = app._filtered[1]
        await pilot.press("enter")
        await pilot.pause()
        assert app.return_value == expected


@pytest.mark.asyncio
async def test_escape_returns_none_for_standard_game() -> None:
    """Esc cancels -> run() yields None (standard game)."""
    app = OpeningPickerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.return_value is None


@pytest.mark.asyncio
async def test_no_matches_shows_hint_and_esc_still_cancels() -> None:
    """A query with zero matches updates the hint and Esc still cancels."""
    from textual.widgets import Static

    app = OpeningPickerApp()
    async with app.run_test() as pilot:
        await pilot.press("z", "z", "z", "q", "x")
        await pilot.pause()
        assert _row_texts(app) == []
        hint = app.query_one("#opening-picker-hint", Static)
        assert "No openings match" in str(hint.render())
        await pilot.press("escape")
        await pilot.pause()
    assert app.return_value is None


@pytest.mark.asyncio
async def test_over_broad_query_reports_hidden_count() -> None:
    """A query matching more than MAX_ROWS shows the 'narrow your search'
    hint with the number of hidden rows."""
    from textual.widgets import Static

    app = OpeningPickerApp()
    async with app.run_test() as pilot:
        # 'a' matches hundreds of openings (every name containing 'a').
        await pilot.press("a")
        await pilot.pause()
        lv = app.query_one("#opening-picker-list", ListView)
        shown = [c for c in lv.children if hasattr(c, "children")]
        assert len(shown) == 50
        hint = app.query_one("#opening-picker-hint", Static)
        assert "narrow your search" in str(hint.render())


@pytest.mark.asyncio
async def test_transposition_duplicates_get_disambiguating_suffix() -> None:
    """Rows sharing (eco, name) but different move orders show the
    divergent-move suffix so the user can tell them apart."""
    app = OpeningPickerApp()
    async with app.run_test() as pilot:
        await pilot.press("n", "a", "j", "d", "o", "r", "f")
        await pilot.pause()
        rows = _row_texts(app)
        # At least one Najdorf row must carry the arrow suffix that
        # move_suffixes produces for transposition clusters.
        assert any("\u2192" in r for r in rows)