"""Bundled chess-opening lookup.

Data is sourced from the ``lichess-org/chess-openings`` dataset
(https://github.com/lichess-org/chess-openings, CC0 / public domain).
We ship the raw TSVs split by ECO volume under
``src/chess_tui/data/`` and compute the ``uci`` and ``epd`` columns at
load time, which keeps the source data identical to upstream and
sidesteps the need to re-import a CI-built artifact.

The catalog has ~3,800 openings.  Loading is done once and cached for
the lifetime of the process; each row is small so the in-memory cost
is trivial.
"""

from __future__ import annotations

import functools
from collections import defaultdict
from dataclasses import dataclass
from importlib import resources
from typing import Iterable, Sequence

import chess
import chess.pgn


# Volume order follows the ECO volumes.  Each file is header-prefixed.
_TSV_VOLUMES: tuple[str, ...] = ("a", "b", "c", "d", "e")


@dataclass(frozen=True)
class Opening:
    """A single named opening position.

    Attributes:
        eco: ECO code (e.g. ``"B90"``).
        name: Human-readable name (e.g. ``"Sicilian Defense: Najdorf
            Variation, English Attack"``).
        pgn: Canonical move sequence in PGN (e.g. ``"1. e4 c5 2. Nf3
            d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6"``).
        uci: Same moves in UCI space-separated notation.
        epd: EPD (FEN minus the half-/fullmove counters) of the
            resulting position.
    """

    eco: str
    name: str
    pgn: str
    uci: str
    epd: str

    def to_board(self) -> chess.Board:
        """Replay the canonical PGN on a fresh board and return it.

        Raises ``ValueError`` if the PGN cannot be parsed.  Used to
        derive a full FEN (with castling rights, en passant, and move
        counters) without depending on the upstream EPD column being
        formatted in a way that ``chess.Board`` accepts directly.
        """
        import io

        game = chess.pgn.read_game(io.StringIO(self.pgn))
        if game is None:
            raise ValueError(f"could not parse PGN for {self.eco} {self.name!r}")
        board = game.board()
        for move in game.mainline_moves():
            board.push(move)
        return board

    def to_fen(self) -> str:
        return self.to_board().fen()

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.name} ({self.eco})"


# ----- load + cache ---------------------------------------------------------


def _data_path(volume: str) -> str:
    """Return the filesystem path to a TSV volume file."""
    # ``resources.files`` is the modern (3.9+) API; we target >=3.11
    # in pyproject so this is safe.
    return str(resources.files("chess_tui.data").joinpath(f"{volume}.tsv"))


def _parse_row(row: dict[str, str], pgn_text: str) -> Opening:
    """Build an :class:`Opening` from a TSV row, deriving uci/epd."""
    board = chess.Board()
    moves_uci: list[str] = []
    try:
        # PGN strings in the dataset look like
        # ``"1. e4 c5 2. Nf3 d6 ..."`` with no comments or variations.
        tokens = pgn_text.split()
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            # Skip move numbers ("1.", "2.", ...).
            if tok.endswith(".") and tok[:-1].isdigit():
                i += 1
                continue
            # Skip game-terminating markers ("1-0", "0-1", "1/2-1/2", "*").
            if tok in {"1-0", "0-1", "1/2-1/2", "*"}:
                i += 1
                continue
            try:
                move = board.parse_san(tok)
            except ValueError as exc:
                raise ValueError(
                    f"bad SAN {tok!r} in PGN {pgn_text!r} for "
                    f"{row.get('eco')!r} {row.get('name')!r}: {exc}"
                ) from exc
            board.push(move)
            moves_uci.append(move.uci())
            i += 1
    except ValueError:
        # Skip the entire row on any parse error: the dataset has a
        # handful of pathological entries we don't want to crash the
        # whole load.  Re-raise in strict mode if/when we add one.
        raise

    uci = " ".join(moves_uci)
    # EPD = FEN without the halfmove / fullmove counters.
    fen = board.fen()
    epd = " ".join(fen.split()[:4])
    return Opening(
        eco=row["eco"],
        name=row["name"],
        pgn=pgn_text,
        uci=uci,
        epd=epd,
    )


def _read_tsv(path: str) -> Iterable[Opening]:
    with open(path, encoding="utf-8") as fh:
        header: list[str] | None = None
        for raw in fh:
            line = raw.rstrip("\n")
            if not line:
                continue
            cols = line.split("\t")
            if header is None:
                header = cols
                # Sanity check: must be exactly eco, name, pgn.
                if header[:3] != ["eco", "name", "pgn"]:
                    raise ValueError(
                        f"{path}: unexpected header {header!r} "
                        "(expected ['eco', 'name', 'pgn'])"
                    )
                continue
            # Defensive: pad/truncate to header length.
            if len(cols) < 3:
                continue
            row = {
                "eco": cols[0],
                "name": cols[1],
                "pgn": cols[2],
            }
            try:
                yield _parse_row(row, row["pgn"])
            except ValueError as exc:
                # A small number of entries in the upstream dataset
                # contain SAN that python-chess doesn't accept (e.g.
                # the "creepy crawly" family with illegal-by-our-rules
                # sequences).  We log to stderr and skip rather than
                # failing the whole load.
                import sys
                print(
                    f"openings: skipping {row['eco']} {row['name']!r}: {exc}",
                    file=sys.stderr,
                )


@functools.cache
def load_all() -> tuple[Opening, ...]:
    """Load and cache the bundled opening catalog.

    The result is process-wide cached; subsequent calls return the same
    tuple instance.
    """
    openings: list[Opening] = []
    for vol in _TSV_VOLUMES:
        openings.extend(_read_tsv(_data_path(vol)))
    return tuple(openings)


# ----- query helpers --------------------------------------------------------


class AmbiguousOpeningQuery(ValueError):
    """Raised when a query matches more than one opening and we can't pick."""

    def __init__(self, query: str, matches: list[Opening]) -> None:
        self.query = query
        self.matches = matches
        names = "\n  ".join(f"{o.eco}  {o.name}" for o in matches[:10])
        suffix = "" if len(matches) <= 10 else f"\n  ... and {len(matches) - 10} more"
        super().__init__(
            f"opening query {query!r} is ambiguous ({len(matches)} matches):\n  {names}{suffix}"
        )


class UnknownOpening(ValueError):
    """Raised when no opening matches a query."""


def find(query: str) -> list[Opening]:
    """Return every opening whose name or ECO contains ``query``.

    Matching is case-insensitive substring on the name, plus prefix
    match on the ECO code so ``"B90"`` finds the whole B90x family.
    """
    q = query.strip().lower()
    if not q:
        return []
    out: list[Opening] = []
    for o in load_all():
        if q in o.name.lower() or o.eco.lower().startswith(q):
            out.append(o)
    return out


# ----- selector / disambiguation helpers -----------------------------------


def _pgn_moves(pgn: str) -> list[str]:
    """Tokenize a PGN string into a flat list of SAN moves.

    Move numbers (``"1."``, ``"12..."``) and result markers
    (``"1-0"``, ``"0-1"``, ``"1/2-1/2"``, ``"*"``) are dropped.  The
    PGNs in the bundled dataset never carry comments or variations,
    so a whitespace split is sufficient.
    """
    out: list[str] = []
    for tok in pgn.split():
        if tok.endswith(".") and tok[:-1].isdigit():
            continue
        if tok in {"1-0", "0-1", "1/2-1/2", "*"}:
            continue
        out.append(tok)
    return out


def move_suffixes(openings: Sequence[Opening]) -> list[str]:
    """Return a per-opening move-diff label, used by the selector UI.

    The lichess-org/chess-openings dataset records the *same* named
    sub-variation multiple times if it can be reached by different
    move orders (transpositions).  For example, ``"Sicilian Defense:
    Najdorf Variation, English Attack"`` appears five times in B90,
    each row with a different PGN that diverges somewhere in the
    middle.

    When the user runs ``--opening najdorf`` and gets back ~33
    candidate rows, listing them all with identical names is
    confusing.  This helper produces a short suffix for each row that
    highlights what's distinct about it:

    - If the row's ``(eco, name)`` is unique within ``openings``,
      the label is the move count in plies, e.g. ``"(10 plies)"``.
    - If the row shares its ``(eco, name)`` with at least one
      sibling, the label is the divergent suffix after the common
      prefix, prefixed with an arrow: ``"→ Be3"`` or
      ``"→ e5 Nb3 Be6 f3"``.  The opening PGN already contains the
      common moves, so the suffix alone is enough to identify which
      of the transposition variants this row is.

    The returned list is parallel to ``openings``.
    """
    # Group by (eco, name) so we can find the common prefix within
    # each transposition cluster.
    by_key: dict[tuple[str, str], list[list[str]]] = defaultdict(list)
    for o in openings:
        by_key[(o.eco, o.name)].append(_pgn_moves(o.pgn))

    out: list[str] = []
    for o in openings:
        sibling_lists = by_key[(o.eco, o.name)]
        mine = _pgn_moves(o.pgn)
        if len(sibling_lists) == 1:
            # No transposition siblings: the move count is the most
            # useful compact label.
            out.append(f"({len(mine)} plies)")
            continue
        # Multiple entries with the same (eco, name): compute the
        # common-prefix length across the siblings.
        min_len = min(len(s) for s in sibling_lists)
        prefix_len = 0
        while prefix_len < min_len:
            ref = sibling_lists[0][prefix_len]
            if all(
                len(s) > prefix_len and s[prefix_len] == ref
                for s in sibling_lists
            ):
                prefix_len += 1
            else:
                break
        suffix_moves = mine[prefix_len:]
        if not suffix_moves:
            # This row is the shortest in the group — the parent
            # that all siblings extend.  In practice this never
            # happens because all siblings in a transposition cluster
            # extend the parent, but we keep the branch for safety.
            out.append("(parent)")
        else:
            out.append("\u2192 " + " ".join(suffix_moves))
    return out


def resolve(query: str) -> Opening:
    """Resolve a query string to a single :class:`Opening`.

    Resolution order:

    1. Exact ECO code (``"B90"``) -> first match.
    2. Exact name (case-insensitive) -> first match.
    3. Unique substring match -> that match.
    4. Otherwise, raise :class:`AmbiguousOpeningQuery` or
       :class:`UnknownOpening`.
    """
    q = query.strip()
    if not q:
        raise UnknownOpening("empty opening query")

    catalog = load_all()
    ql = q.lower()

    # 1. Exact ECO.
    for o in catalog:
        if o.eco.upper() == q.upper():
            return o

    # 2. Exact name (case-insensitive).
    for o in catalog:
        if o.name.lower() == ql:
            return o

    # 3. Unique substring / ECO prefix.
    matches = find(q)
    if not matches:
        raise UnknownOpening(f"no opening matches {query!r}")
    if len(matches) == 1:
        return matches[0]
    raise AmbiguousOpeningQuery(q, matches)
