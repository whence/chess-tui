"""Textual TUI for chess-tui."""

from __future__ import annotations

from typing import ClassVar

import chess
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Input, Label, ListItem, ListView, Static

from .pieces import glyph
from .state import BoardState, IllegalMoveError
from .themes import THEME, Theme


# Marker attribute attached to ListItems to map them back to their move UCI.
_MOVE_ATTR = "_move_uci"


def _parse_color(spec: str) -> str:
    """Resolve a color spec to a string Textual's CSS understands.

    Accepts hex like ``#769656`` and named CSS colors.
    """
    return spec


class Cell(Static):
    """A single square on the rendered board."""

    def __init__(self, row: int, col: int) -> None:
        super().__init__(" ", id=f"cell-{row}-{col}")
        self.row = row
        self.col = col
        self.glyph: str = " "

    def set_piece(
        self,
        piece: chess.Piece | None,
        *,
        light: bool,
        light_bg: str,
        dark_bg: str,
        light_piece_fg: str,
        dark_piece_fg: str,
    ) -> None:
        bg = light_bg if light else dark_bg
        self.styles.background = _parse_color(bg)
        if piece is None:
            self.glyph = " "
            self.styles.color = ""
        else:
            self.glyph = glyph(piece) or " "
            self.styles.color = dark_piece_fg if piece.color == chess.BLACK else light_piece_fg
        self.update(self.glyph)


class TextLine(Static):
    """Static widget that exposes its current text via a simple attribute."""

    def __init__(self, text: str = "", **kwargs) -> None:
        super().__init__(text, **kwargs)
        self.text_value: str = text

    def set_text(self, text: str) -> None:
        self.text_value = text
        self.update(text)


class Legend(Static):
    """Shows which piece glyphs are white vs black."""

    WHITE_PIECES = "♙ ♘ ♗ ♖ ♕ ♔"
    BLACK_PIECES = "♟ ♞ ♝ ♜ ♛ ♚"

    DEFAULT_CSS: ClassVar[str] = """
    Legend {
        height: 2;
        padding: 0 1;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        from rich.table import Table
        from rich.text import Text

        table = Table(
            show_header=False,
            show_edge=False,
            box=None,
            padding=(0, 1),
            expand=False,
        )
        table.add_column(justify="right", no_wrap=True, width=8)
        table.add_column(justify="left", no_wrap=True)
        table.add_row(Text("White", style="bold"), Text(self.WHITE_PIECES))
        table.add_row(Text("Black", style="bold"), Text(self.BLACK_PIECES))
        super().__init__(table)


class BoardWidget(Static):
    """Renders the 8x8 board as a grid of Cells, themed by a :class:`Theme`."""

    DEFAULT_CSS: ClassVar[str] = """
    BoardWidget {
        height: 8;
        width: 24;
    }
    #board-grid {
        grid-size: 8 8;
        grid-gutter: 0;
        width: 24;
        height: 8;
    }
    Cell {
        width: 3;
        height: 1;
        content-align: center middle;
        text-style: bold;
    }
    CoordinateBar {
        width: 24;
        height: 1;
        content-align: center middle;
    }
    """

    def __init__(self, theme: Theme | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.theme: Theme = theme or THEME

    def compose(self) -> ComposeResult:
        with Grid(id="board-grid"):
            for row in range(8):
                for col in range(8):
                    yield Cell(row, col)

    def refresh_board(self, state: BoardState) -> None:
        light_bg = self.theme.light_square
        dark_bg = self.theme.dark_square
        light_fg = self.theme.light_piece
        dark_fg = self.theme.dark_piece
        for row in range(8):
            for col in range(8):
                square = state.square_at(row, col)
                piece = state.piece_at(square)
                light = (row + col) % 2 == 0
                cell = self.query_one(f"#cell-{row}-{col}", Cell)
                cell.set_piece(
                    piece,
                    light=light,
                    light_bg=light_bg,
                    dark_bg=dark_bg,
                    light_piece_fg=light_fg,
                    dark_piece_fg=dark_fg,
                )


class CoordinateBar(Static):
    """Renders file/rank labels around the board."""

    DEFAULT_CSS: ClassVar[str] = """
    CoordinateBar {
        height: 1;
        content-align: center middle;
    }
    """

    def update_for(self, state: BoardState, *, axis: str) -> None:
        if axis == "files":
            self.update(" ".join(state.file_label(c) for c in range(8)))
        else:
            self.update(" ".join(state.rank_label(r) for r in range(8)))


class ChessApp(App):
    """Main TUI application."""

    CSS: ClassVar[str] = """
    Screen {
        layout: vertical;
    }
    #title {
        dock: top;
        height: 1;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }
    #main {
        layout: horizontal;
        height: 1fr;
    }
    #board-area {
        layout: horizontal;
        width: 1fr;
        height: 100%;
        align: center middle;
    }
    #board-inner {
        layout: vertical;
        width: auto;
        height: auto;
    }
    #side {
        layout: vertical;
        width: 32;
        padding: 1 1;
    }
    #status {
        height: auto;
        padding: 1 0;
    }
    #move-list {
        height: 1fr;
        border: round $primary;
    }
    #move-input {
        height: 3;
    }
    #help {
        height: auto;
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f", "flip", "Flip board"),
        Binding("r", "reset", "Reset"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    state: reactive[BoardState] = reactive(BoardState, init=False)

    def __init__(self, state: BoardState | None = None) -> None:
        super().__init__()
        self._state: BoardState = state or BoardState()

    # ---- composition -----------------------------------------------------

    def compose(self) -> ComposeResult:
        yield TextLine("Chess TUI — White to move", id="title")
        with Horizontal(id="main"):
            with Horizontal(id="board-area"):
                with Vertical(id="board-inner"):
                    yield CoordinateBar(id="files-top")
                    yield BoardWidget(id="board")
                    yield CoordinateBar(id="files-bot")
            with Vertical(id="side"):
                yield TextLine("", id="status")
                yield ListView(id="move-list")
                yield Input(placeholder="Enter move (SAN or UCI), or from-square…", id="move-input")
                yield Legend()
                yield TextLine(
                    "↑↓ + Enter: pick a move • Tab to input to type a move • "
                    "f: flip • r: reset • q: quit",
                    id="help",
                )

    def on_mount(self) -> None:
        self.state = self._state  # triggers watch_state -> refresh_all
        # Default focus is the move list so Enter picks the highlighted move
        # (Tab to the input below if you want to type a custom one).
        move_list = self.query_one("#move-list", ListView)
        move_list.index = 0
        move_list.focus()

    # ---- rendering -------------------------------------------------------

    def watch_state(self, _old: BoardState, _new: BoardState) -> None:
        self.refresh_all()

    def refresh_all(self) -> None:
        board = self.query_one("#board", BoardWidget)
        board.refresh_board(self._state)
        self.query_one("#files-top", CoordinateBar).update_for(self._state, axis="files")
        self.query_one("#files-bot", CoordinateBar).update_for(self._state, axis="files")
        self._refresh_title()
        self._refresh_status()
        self._refresh_move_list()

    def _refresh_title(self) -> None:
        title = self.query_one("#title", TextLine)
        if self._state.is_game_over():
            title.set_text(f"Chess TUI — Game over: {self._state.result()}")
            # Use a neutral background when the game is over.
            title.styles.background = "#5a5a5a"
            title.styles.color = "white"
        else:
            suffix = " (check)" if self._state.is_check() else ""
            title.set_text(f"Chess TUI — {self._state.turn_name()} to move{suffix}")
            if self._state.turn() == chess.WHITE:
                # White's turn: white background, dark text for contrast.
                title.styles.background = "white"
                title.styles.color = "black"
            else:
                # Black's turn: dark grey background, white text.
                title.styles.background = "#3a3a3a"
                title.styles.color = "white"

    def _refresh_status(self) -> None:
        status = self.query_one("#status", TextLine)
        history = self._state.san_history()
        last = f"Last: {history[-1]}" if history else "Last: —"
        status.set_text(f"Move {self._state.fullmove_number()} • {last} • FEN: {self._state.fen()}")

    def _refresh_move_list(self) -> None:
        move_list = self.query_one("#move-list", ListView)
        for child in list(move_list.children):
            child.remove()
        if self._state.is_game_over():
            move_list.append(ListItem(Label(f"Game over — {self._state.result()}")))
            return
        for move in self._state.legal_moves():
            item = ListItem(Label(self._state.san_for(move)))
            setattr(item, _MOVE_ATTR, move.uci())
            move_list.append(item)

    # ---- events ----------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value
        event.input.value = ""
        self._handle_input(text)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        uci = getattr(event.item, _MOVE_ATTR, None)
        if uci is None:
            return
        self._try_apply(uci, is_uci=True)

    # ---- actions ---------------------------------------------------------

    def action_flip(self) -> None:
        self._state.flip()
        self.refresh_all()

    def action_reset(self) -> None:
        self._state.reset()
        self.refresh_all()

    # ---- input handling --------------------------------------------------

    def _handle_input(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        # Try to apply as a move first (SAN like "e4"/"Nf3" or UCI like
        # "e2e4"). Only fall back to the from-square query if the text isn't
        # a parseable move — otherwise typing a 2-char pawn move like "e4"
        # would get intercepted as a "where can this square go?" query.
        try:
            self._state.apply_san(text)
        except (IllegalMoveError, ValueError, chess.AmbiguousMoveError):
            square = self._state.parse_display_square(text)
            if square is not None:
                self._show_destinations(square)
                return
            self._set_status_error(f"unrecognized input: {text!r}")
            return
        self.refresh_all()

    def _try_apply(self, text: str, *, is_uci: bool = False) -> None:
        try:
            if is_uci:
                move = self._state.board.parse_uci(text)
                if move not in self._state.legal_moves():
                    raise IllegalMoveError(f"illegal move: {text}")
                self._state.apply_move(move)
            else:
                self._state.apply_san(text)
        except (IllegalMoveError, ValueError, chess.InvalidMoveError, chess.AmbiguousMoveError) as exc:
            self._set_status_error(str(exc))
            return
        self.refresh_all()

    def _show_destinations(self, square: chess.Square) -> None:
        move_list = self.query_one("#move-list", ListView)
        for child in list(move_list.children):
            child.remove()
        moves = self._state.legal_moves_from(square)
        if not moves:
            move_list.append(ListItem(Label("No legal moves from that square")))
            return
        for move in moves:
            item = ListItem(Label(self._state.san_for(move)))
            setattr(item, _MOVE_ATTR, move.uci())
            move_list.append(item)

    def _set_status_error(self, msg: str) -> None:
        status = self.query_one("#status", TextLine)
        status.set_text(msg)


def main() -> None:
    ChessApp().run()


if __name__ == "__main__":
    main()