"""Standalone Textual opening picker for the starter wizard.

The starter (``chess_tui.starter``) is an interactive wizard that
configures a cmux chess workspace.  Its opening step used to be a
raw-``input()`` search-then-pick loop; this module replaces it with
a single Textual screen: a live-filtering search box over the bundled
``chess_tui.openings`` catalog with a selectable list underneath.

The picker is deliberately self-contained — it does not import the
main ``ChessApp`` — so the starter can launch it on its own without
pulling in the board widgets, sounds, or network players.

Usage::

    from chess_tui.opening_picker import OpeningPickerApp

    chosen = OpeningPickerApp().run()  # Opening | None
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, ListItem, ListView, Static

from .openings import Opening, find, load_all, move_suffixes

# The catalog is ~3,800 rows; rendering all of them in a ListView is
# sluggish and pointless (the user is searching).  Cap the visible
# window so the list stays responsive, and tell the user how many were
# hidden by their (over-broad) query.
MAX_ROWS = 50


class OpeningPickerApp(App[Opening | None]):
    """Live-filter opening selector for the starter wizard.

    A search :class:`Input` on top filters the bundled catalog in real
    time; a :class:`ListView` underneath shows the matches.  The user
    picks with ``Enter`` (returns the highlighted :class:`Opening`) or
    cancels with ``Esc`` (returns ``None`` — the starter treats that as
    "standard game / start position").

    Transposition duplicates (rows sharing an ``(eco, name)`` but
    differing in move order) are disambiguated with the same divergent
    move-suffix labels the TUI's own selector uses
    (:func:`chess_tui.openings.move_suffixes`).

    The app returns its result via :meth:`App.exit`, so
    :meth:`run` yields the chosen :class:`Opening` or ``None``.
    """

    TITLE = "Opening setup"

    # ``priority=True`` so arrow keys/Enter reach the list even while
    # the search Input is focused — the Input keeps Left/Right/Home/
    # End for caret movement, but Up/Down/Enter drive the list, which
    # matches the on-screen hint.
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Standard game", show=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("enter", "select", "Select", show=False, priority=True),
    ]

    CSS: ClassVar[str] = """
    Screen {
        layout: vertical;
        padding: 1 2;
    }
    #opening-picker-search {
        height: 3;
        margin: 0 0 1 0;
        border: round $accent;
    }
    #opening-picker-search:focus {
        border: round $accent;
    }
    #opening-picker-list {
        height: 1fr;
        min-height: 10;
        max-height: 24;
        border: round $primary;
    }
    #opening-picker-hint {
        height: 1;
        margin: 1 0 0 0;
        color: $text-muted;
        text-align: center;
    }
    .opening-row-eco {
        color: $secondary;
        text-style: bold;
    }
    .opening-row-suffix {
        color: $accent;
    }
    """

    def __init__(self, initial_query: str = "") -> None:
        super().__init__()
        self._initial_query = initial_query
        # Catalog load is cached for the process lifetime, so this is
        # cheap on repeat invocations within one starter session.
        self._all: tuple[Opening, ...] = load_all()
        self._filtered: list[Opening] = []
        self._suffixes: list[str] = []

    # -- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Input(
                value=self._initial_query,
                placeholder=(
                    "Type an opening name or ECO code "
                    "(e.g. 'najdorf', 'B90') …"
                ),
                id="opening-picker-search",
            )
            yield ListView(id="opening-picker-list")
            yield Static(
                "↑↓ move · Enter select · Esc = standard game",
                id="opening-picker-hint",
            )

    def on_mount(self) -> None:
        # Seed the list with the first page of the catalog so the user
        # can browse immediately, then focus the search box so typing
        # filters as they go.
        self._refresh(self._initial_query)
        self.query_one("#opening-picker-search", Input).focus()

    # -- filtering ------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        """Re-filter the list on every keystroke."""
        if event.input.id != "opening-picker-search":
            return
        self._refresh(event.value)

    def _refresh(self, query: str) -> None:
        """Rebuild the list for *query* (empty = browse the catalog)."""
        if query.strip():
            matches = find(query)
        else:
            # Empty query: show the first page as a browse view rather
            # than a blank list — gives the user something to scroll.
            matches = list(self._all)
        self._filtered = matches[:MAX_ROWS]
        self._suffixes = move_suffixes(self._filtered) if self._filtered else []

        list_view = self.query_one("#opening-picker-list", ListView)
        list_view.clear()
        for i, opening in enumerate(self._filtered):
            list_view.append(
                ListItem(Static(self._format_row(opening, self._suffixes[i])))
            )

        hidden = len(matches) - len(self._filtered)
        hint = self.query_one("#opening-picker-hint", Static)
        if hidden > 0:
            hint.update(
                f"{len(self._filtered)} of {len(matches)} shown — "
                f"narrow your search to see the other {hidden}"
            )
        elif not self._filtered:
            hint.update("No openings match — Esc for a standard game")
        else:
            hint.update("↑↓ move · Enter select · Esc = standard game")

        # Keep a sensible selection: first row if any, else none.
        list_view.index = 0 if self._filtered else None

    @staticmethod
    def _format_row(o: Opening, suffix: str) -> str:
        """One-line label for a list row (matches the TUI's selector)."""
        eco = f"{o.eco:<3}"
        name = o.name
        if len(name) > 60:
            name = name[:57] + "..."
        return f"{eco}  {name:<60}  {suffix}"

    # -- actions ---------------------------------------------------------------

    def action_cursor_up(self) -> None:
        self.query_one("#opening-picker-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#opening-picker-list", ListView).action_cursor_down()

    def action_select(self) -> None:
        """Confirm the highlighted row (if any) and exit."""
        list_view = self.query_one("#opening-picker-list", ListView)
        idx = list_view.index
        if idx is None or not (0 <= idx < len(self._filtered)):
            return
        self.exit(self._filtered[idx])

    def action_cancel(self) -> None:
        """Esc: return None so the caller starts a standard game."""
        self.exit(None)

    # The ListView also emits a `Selected` event on Enter; route it
    # through the same path so mouse double-clicks work too.
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None or not (0 <= idx < len(self._filtered)):
            return
        self.exit(self._filtered[idx])