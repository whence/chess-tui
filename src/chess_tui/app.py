"""Textual TUI for chess-tui."""

from __future__ import annotations

from typing import ClassVar

import chess
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, ScrollableContainer, Vertical
from textual.widgets import Input, Label, ListItem, ListView, Static

from . import net
from .pieces import glyph
from .sound import play_click
from .player import LocalPlayer, NetworkPlayer, Player
from .state import BoardState, IllegalMoveError
from .themes import THEME, Theme


# Marker attribute attached to ListItems to map them back to their move UCI.
_MOVE_ATTR = "_move_uci"


def _parse_color(spec: str) -> str:
    """Resolve a color spec to a string Textual's CSS understands.

    Accepts hex like ``#769656`` or named CSS colors.
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
        cursor: bool = False,
        selected: bool = False,
        last_move: bool = False,
    ) -> None:
        # Determine background color
        if cursor and selected:
            # Cursor on selected piece: bright highlight
            bg = "#FFD700"  # gold
        elif cursor:
            # Cursor only: cyan highlight
            bg = "#00CED1"  # dark turquoise
        elif selected:
            # Selected piece's square: yellow
            bg = "#FFFF00"  # yellow
        elif last_move:
            # Last move highlight: semi-transparent green
            bg = "#90EE90"  # light green
        else:
            bg = light_bg if light else dark_bg

        self.styles.background = _parse_color(bg)

        if piece is None:
            self.glyph = " "
            self.styles.color = ""
        else:
            self.glyph = glyph(piece) or " "
            # Use contrasting color based on background
            if cursor or selected or last_move:
                # On highlighted backgrounds, use dark piece color
                self.styles.color = "#1a1a1a"
            else:
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
    """

    def __init__(self, theme: Theme | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.theme: Theme = theme or THEME

    def compose(self) -> ComposeResult:
        with Grid(id="board-grid"):
            for row in range(8):
                for col in range(8):
                    yield Cell(row, col)

    def refresh_board(
        self,
        state: BoardState,
        cursor: tuple[int, int] | None = None,
        selected: tuple[int, int] | None = None,
        last_move_squares: tuple[int, int] | None = None,
    ) -> None:
        light_bg = self.theme.light_square
        dark_bg = self.theme.dark_square
        light_fg = self.theme.light_piece
        dark_fg = self.theme.dark_piece
        # Convert last move squares to display coordinates
        last_move_display: tuple[tuple[int, int], tuple[int, int]] | None = None
        if last_move_squares is not None:
            from_sq, to_sq = last_move_squares
            last_move_display = (
                state.display_position(from_sq),
                state.display_position(to_sq),
            )
        for row in range(8):
            for col in range(8):
                square = state.square_at(row, col)
                piece = state.piece_at(square)
                light = (row + col) % 2 == 0
                cell = self.query_one(f"#cell-{row}-{col}", Cell)
                is_cursor = cursor == (row, col)
                is_selected = selected == (row, col)
                is_last_move = last_move_display is not None and (
                    (row, col) == last_move_display[0] or (row, col) == last_move_display[1]
                )
                cell.set_piece(
                    piece,
                    light=light,
                    light_bg=light_bg,
                    dark_bg=dark_bg,
                    light_piece_fg=light_fg,
                    dark_piece_fg=dark_fg,
                    cursor=is_cursor,
                    selected=is_selected,
                    last_move=is_last_move,
                )


class RankBar(Static):
    """Vertical column of rank labels on the left of the board."""

    DEFAULT_CSS: ClassVar[str] = """
    RankBar {
        width: 3;
        height: 8;
    }
    """

    def refresh_ranks(self, state: BoardState) -> None:
        lines = [f" {state.rank_label(r):>2}" for r in range(8)]
        self.update("\n".join(lines))


class FileBar(Static):
    """File labels (a, b, …, h) below the board."""

    DEFAULT_CSS: ClassVar[str] = """
    FileBar {
        width: 27;
        height: 1;
    }
    """

    def refresh_files(self, state: BoardState) -> None:
        parts = ["   "]  # blank for rank-column alignment
        for c in range(8):
            parts.append(f" {state.file_label(c)} ")
        self.update("".join(parts))


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
        width: 27;
        height: 9;
    }
    #board-row {
        layout: horizontal;
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
    #move-history {
        height: 8;
        border: round $secondary;
        padding: 0 1;
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
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("left", "cursor_left", "Left", show=False, priority=True),
        Binding("right", "cursor_right", "Right", show=False, priority=True),
        Binding("space", "select_piece", "Select/Place", show=False, priority=True),
        Binding("enter", "confirm_selection", "Confirm", show=False),
        Binding("escape", "cancel_selection", "Cancel", show=False, priority=True),
        Binding("f", "flip", "Flip board"),
        Binding("r", "reset", "Reset"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        state: BoardState | None = None,
        players: dict[chess.Color, Player] | None = None,
    ) -> None:
        super().__init__()
        self._state: BoardState = state or BoardState()
        self._players: dict[chess.Color, Player] = players or {
            chess.WHITE: LocalPlayer(color=chess.WHITE),
            chess.BLACK: LocalPlayer(color=chess.BLACK),
        }
        # Cursor position (display row, col)
        self._cursor: tuple[int, int] = (7, 4)  # Start at e1
        # Selected piece square (display row, col) or None
        self._selected: tuple[int, int] | None = None
        # Last move highlight as square indices (flip-independent)
        self._last_move_squares: tuple[int, int] | None = None  # (from_sq, to_sq)

    # ---- composition -----------------------------------------------------

    def compose(self) -> ComposeResult:
        yield TextLine("Chess TUI — White to move", id="title")
        with Horizontal(id="main"):
            with Horizontal(id="board-area"):
                with Vertical(id="board-inner"):
                    with Horizontal(id="board-row"):
                        yield RankBar(id="ranks-left")
                        yield BoardWidget(id="board")
                    yield FileBar(id="files-bot")
            with Vertical(id="side"):
                yield TextLine("", id="status")
                with ScrollableContainer(id="move-history"):
                    yield Static("Moves:", id="move-history-text")
                yield ListView(id="move-list")
                yield Input(placeholder="Enter move (SAN or UCI), or from-square…", id="move-input")
                yield TextLine(
                    "Arrow keys: navigate • Space: select/place • "
                    "Enter: confirm • Esc: cancel • f: flip • r: reset • q: quit",
                    id="help",
                )

    def on_mount(self) -> None:
        self.refresh_all()
        # Trigger network move on mount if it's network player's turn
        self.call_later(self._maybe_request_network_move)

    # ---- rendering -------------------------------------------------------

    def _maybe_request_network_move(self) -> None:
        if self._state.is_game_over():
            return
        player = self._players[self._state.turn()]
        if not isinstance(player, NetworkPlayer):
            return
        self.run_worker(
            self._do_network_move(player),
            exclusive=True,
            name="network-move",
        )

    async def _do_network_move(self, player: NetworkPlayer) -> None:
        if self._state.is_game_over():
            return
        if self._players[self._state.turn()] is not player:
            return
        player.on_status = self._set_status_error
        moves = self._state.san_history()
        try:
            move = await player.choose_move(self._state.board, moves=moves)
        finally:
            player.on_status = None
        try:
            self._state.apply_move(move)
            self._last_move_squares = (move.from_square, move.to_square)
        except IllegalMoveError as exc:
            self._set_status_error(f"network player returned bad move: {exc}")
            return
        self._commit()

    def refresh_all(self) -> None:
        board = self.query_one("#board", BoardWidget)
        board.refresh_board(
            self._state,
            cursor=self._cursor,
            selected=self._selected,
            last_move_squares=self._last_move_squares,
        )
        self.query_one("#ranks-left", RankBar).refresh_ranks(self._state)
        self.query_one("#files-bot", FileBar).refresh_files(self._state)
        self._refresh_title()
        self._refresh_status()
        self._refresh_move_history()
        self._refresh_move_list()

    def _refresh_title(self) -> None:
        title = self.query_one("#title", TextLine)
        if self._state.is_game_over():
            title.set_text(f"Chess TUI — Game over: {self._state.result()}")
            title.styles.background = "#5a5a5a"
            title.styles.color = "white"
        else:
            suffix = " (check)" if self._state.is_check() else ""
            title.set_text(f"Chess TUI — {self._state.turn_name()} to move{suffix}")
            if self._state.turn() == chess.WHITE:
                title.styles.background = "white"
                title.styles.color = "black"
            else:
                title.styles.background = "#3a3a3a"
                title.styles.color = "white"

    def _refresh_status(self) -> None:
        status = self.query_one("#status", TextLine)
        history = self._state.san_history()
        last = f"Last: {history[-1]}" if history else "Last: —"
        turn = self._state.turn()
        player = self._players[turn]
        side = self._state.turn_name()
        if isinstance(player, NetworkPlayer) and not self._state.is_game_over():
            side += f" (network @ {player.url})"
        # Show cursor position
        row, col = self._cursor
        square = self._state.square_at(row, col)
        square_name = chess.square_name(square)
        status.set_text(
            f"Move {self._state.fullmove_number()} • {side} to move • "
            f"Cursor: {square_name} • {last}"
        )

    def _refresh_move_list(self) -> None:
        move_list = self.query_one("#move-list", ListView)
        for child in list(move_list.children):
            child.remove()
        if self._state.is_game_over():
            move_list.append(ListItem(Label(f"Game over — {self._state.result()}")))
            return
        # If a piece is selected, show its legal destinations
        if self._selected is not None:
            from_row, from_col = self._selected
            from_square = self._state.square_at(from_row, from_col)
            moves = self._state.legal_moves_from(from_square)
            if not moves:
                move_list.append(ListItem(Label("No legal moves from this square")))
            else:
                for move in moves:
                    item = ListItem(Label(self._state.san_for(move)))
                    setattr(item, _MOVE_ATTR, move.uci())
                    move_list.append(item)
        else:
            # Show all legal moves sorted
            moves = sorted(
                self._state.legal_moves(),
                key=lambda m: self._state.san_for(m).lower(),
            )
            for move in moves:
                item = ListItem(Label(self._state.san_for(move)))
                setattr(item, _MOVE_ATTR, move.uci())
                move_list.append(item)

    def _refresh_move_history(self) -> None:
        """Refresh the move history panel."""
        history_text = self.query_one("#move-history-text", Static)
        history = self._state.san_history()
        if not history:
            history_text.update("")
            return
        # Format as pairs: "1. e4 e5 2. Nf3 Nf6 ..."
        move_pairs: list[str] = []
        for i in range(0, len(history), 2):
            num = i // 2 + 1
            if i + 1 < len(history):
                move_pairs.append(f"{num}. {history[i]} {history[i+1]}")
            else:
                move_pairs.append(f"{num}. {history[i]}")
        history_text.update(" ".join(move_pairs))

    # ---- cursor actions --------------------------------------------------

    def _input_has_focus(self) -> bool:
        """Check if the text input widget has focus."""
        try:
            inp = self.query_one("#move-input", Input)
            return inp.has_focus
        except Exception:
            return False

    def _is_local_turn(self) -> bool:
        """Check if it's a local player's turn to move."""
        if self._state.is_game_over():
            return False
        player = self._players[self._state.turn()]
        return isinstance(player, LocalPlayer)

    def action_cursor_up(self) -> None:
        if self._input_has_focus() or not self._is_local_turn():
            return
        row, col = self._cursor
        if row > 0:
            self._cursor = (row - 1, col)
            self.refresh_all()

    def action_cursor_down(self) -> None:
        if self._input_has_focus() or not self._is_local_turn():
            return
        row, col = self._cursor
        if row < 7:
            self._cursor = (row + 1, col)
            self.refresh_all()

    def action_cursor_left(self) -> None:
        if self._input_has_focus() or not self._is_local_turn():
            return
        row, col = self._cursor
        if col > 0:
            self._cursor = (row, col - 1)
            self.refresh_all()

    def action_cursor_right(self) -> None:
        if self._input_has_focus() or not self._is_local_turn():
            return
        row, col = self._cursor
        if col < 7:
            self._cursor = (row, col + 1)
            self.refresh_all()

    def action_select_piece(self) -> None:
        """Space: select piece or place piece."""
        if self._input_has_focus() or not self._is_local_turn():
            return  # Let input handle space
        row, col = self._cursor
        square = self._state.square_at(row, col)
        piece = self._state.piece_at(square)

        if self._selected is None:
            # No piece selected yet — try to select
            if piece is not None and piece.color == self._state.turn():
                # Select piece of the correct color
                self._selected = (row, col)
                self._set_status(f"Selected {piece.symbol()} at {chess.square_name(square)}")
                self.refresh_all()
        else:
            # Piece already selected — try to place
            from_row, from_col = self._selected
            from_square = self._state.square_at(from_row, from_col)
            to_square = square

            if (row, col) == self._selected:
                # Clicked same square — deselect
                self._selected = None
                self._set_status("Selection cancelled")
                self.refresh_all()
                return

            # Try to make the move
            move = chess.Move(from_square, to_square)
            if move in self._state.legal_moves():
                self._apply_move_with_highlight(move, from_row, from_col, row, col)
            else:
                # Check for promotion
                piece = self._state.piece_at(from_square)
                if piece and piece.piece_type == chess.PAWN:
                    # Check if this is a promotion move
                    to_rank = chess.square_rank(to_square)
                    if (piece.color == chess.WHITE and to_rank == 7) or \
                       (piece.color == chess.BLACK and to_rank == 0):
                        # Try queen promotion
                        move = chess.Move(from_square, to_square, promotion=chess.QUEEN)
                        if move in self._state.legal_moves():
                            self._apply_move_with_highlight(move, from_row, from_col, row, col)
                            return

                # Try to select a different piece of the correct color
                if piece is not None and piece.color == self._state.turn():
                    self._selected = (row, col)
                    self._set_status(f"Selected {piece.symbol()} at {chess.square_name(square)}")
                    self.refresh_all()
                else:
                    self._set_status_error("Illegal move")

    def action_confirm_selection(self) -> None:
        """Enter: confirm move if cursor is on a legal destination."""
        if self._selected is None or not self._is_local_turn():
            return

        row, col = self._cursor
        from_row, from_col = self._selected
        from_square = self._state.square_at(from_row, from_col)
        to_square = self._state.square_at(row, col)

        move = chess.Move(from_square, to_square)
        if move in self._state.legal_moves():
            self._apply_move_with_highlight(move, from_row, from_col, row, col)

    def action_cancel_selection(self) -> None:
        """Escape: cancel current selection."""
        if self._input_has_focus() or not self._is_local_turn():
            return  # Let input handle escape
        if self._selected is not None:
            self._selected = None
            self._set_status("Selection cancelled")
            self.refresh_all()

    def _apply_move_with_highlight(
        self,
        move: chess.Move,
        from_row: int,
        from_col: int,
        to_row: int,
        to_col: int,
    ) -> None:
        """Apply a move and set up last-move highlight."""
        try:
            self._state.apply_move(move)
            self._selected = None
            self._last_move_squares = (move.from_square, move.to_square)
            self._commit(sound=True)
        except IllegalMoveError as exc:
            self._set_status_error(str(exc))

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

    def _commit(self, *, sound: bool = False) -> None:
        if sound:
            play_click()
        self.refresh_all()
        self._maybe_request_network_move()

    def action_flip(self) -> None:
        self._state.flip()
        self._commit()

    def action_reset(self) -> None:
        self._state.reset()
        self._selected = None
        self._last_move_squares = None
        self._cursor = (7, 4)
        self._commit()

    # ---- input handling --------------------------------------------------

    def _handle_input(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        try:
            move = self._state.apply_san(text)
            self._last_move_squares = (move.from_square, move.to_square)
        except (IllegalMoveError, ValueError, chess.AmbiguousMoveError):
            square = self._state.parse_display_square(text)
            if square is not None:
                self._show_destinations(square)
                return
            self._set_status_error(f"unrecognized input: {text!r}")
            return
        self._commit(sound=True)

    def _try_apply(self, text: str, *, is_uci: bool = False) -> None:
        try:
            if is_uci:
                move = self._state.board.parse_uci(text)
                if move not in self._state.legal_moves():
                    raise IllegalMoveError(f"illegal move: {text}")
                self._state.apply_move(move)
                self._last_move_squares = (move.from_square, move.to_square)
            else:
                move = self._state.apply_san(text)
                self._last_move_squares = (move.from_square, move.to_square)
        except (IllegalMoveError, ValueError, chess.InvalidMoveError, chess.AmbiguousMoveError) as exc:
            self._set_status_error(str(exc))
            return
        self._commit(sound=True)

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

    def _set_status(self, msg: str) -> None:
        status = self.query_one("#status", TextLine)
        status.set_text(msg)

    def _set_status_error(self, msg: str) -> None:
        status = self.query_one("#status", TextLine)
        status.set_text(msg)


def main(argv: list[str] | None = None) -> None:
    import argparse

    from . import sound

    parser = argparse.ArgumentParser(
        prog="chess-tui",
        description="TUI chess app. Pass --white / --black to route that "
        "side to a network player (see openapi/chess-tui-net.yaml).",
    )
    parser.add_argument(
        "--white",
        metavar="URL",
        help="URL of a network player to use for white "
        "(e.g. http://localhost:8080). Omit for a local human.",
    )
    parser.add_argument(
        "--black",
        metavar="URL",
        help="URL of a network player to use for black. Omit for a local human.",
    )
    parser.add_argument(
        "-s", "--silent",
        action="store_true",
        help="disable click sound",
    )
    args = parser.parse_args(argv)

    if args.silent:
        sound.SILENT = True

    players: dict[chess.Color, Player] = {}
    if args.white:
        players[chess.WHITE] = NetworkPlayer(color=chess.WHITE, url=args.white)
    else:
        players[chess.WHITE] = LocalPlayer(color=chess.WHITE)
    if args.black:
        players[chess.BLACK] = NetworkPlayer(color=chess.BLACK, url=args.black)
    else:
        players[chess.BLACK] = LocalPlayer(color=chess.BLACK)

    ChessApp(players=players).run()


if __name__ == "__main__":
    main()
