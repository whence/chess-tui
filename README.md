# chess-tui

A terminal chess application with network play support.

## Why another chess TUI?

There are already great terminal chess apps out there:

- [chess-tui](https://github.com/thomas-mauran/chess-tui) (Rust) — Local play, Stockfish, Lichess, custom skins
- [cli-chess](https://github.com/trevorbayless/cli-chess) (Python) — Lichess online, Fairy-Stockfish offline
- [chess-cli](https://github.com/Nemo984/chess-cli) (Go) — UCI engine play, game database

**This one is different because:**

1. **Network player architecture** — Players are HTTP servers. Run `chess-tui-engine`, `chess-tui-nova`, or `chess-tui-maia` on one machine, play from another. Any two can be matched against each other (e.g. Nova vs. Maia at the same ELO) since they all speak the same RESTful protocol.
2. **Two neural network engines** — Uses either the [Nova chess predictor](https://huggingface.co/novachess/novachess-engine) (style-conditioned, with `--temperature` / `--top-p` / `--blunder-rate` knobs) or the [Maia-3 human-move predictor](https://huggingface.co/collections/MaiaChess/maia3) (Elo-conditioned, with `--temperature` / `--top-p` / `--use-history`). Both produce human-like play at any strength.
3. **Image-based pieces** — Renders actual PNG piece images (via [textual-image](https://github.com/voidstarHQ/textual-image)) for crisp graphics. Verified working on [cmux](https://cmux.com/) and [Kitty](https://sw.kovidgoyal.net/kitty/), which both implement the Kitty Graphics Protocol's `U=1` unicode-placeholder placement that `textual-image` relies on. Also works on [Ghostty](https://ghostty.org/). **Not supported on wezterm or iTerm2** — both report TGP support but lack the `U=1` diacritic placement mode, so the image protocol selection in `textual-image` silently no-ops there. Other terminals fall through to half-cell rendering (blurry but functional).
4. **Move/capture sounds** — Different sounds for regular moves and captures.
5. **Promotion selector** — Visual picker when pawn reaches the last rank.
6. **FEN support** — Start from any position with `--fen`.
7. **Named openings** — Start from a named opening with `--opening` (e.g. `--opening B90` or `--opening "Sicilian Defense: Najdorf Variation"`). The opening's moves are replayed so the SAN history is populated — this means history-aware network players like `chess-tui-maia --use-history` get the opening positions as transformer context. The opening name and ECO code are shown in the title bar. Browse the bundled catalog with `--list-openings` (optionally filter by substring). If the query matches more than one row, an interactive selector pops up before the board mounts (arrow keys + Enter to pick, Esc to cancel). Sourced from the [lichess-org/chess-openings](https://github.com/lichess-org/chess-openings) dataset (CC0).
7. **Python/Textual** — Easy to extend, modify, and contribute to.

## Setup

```bash
git clone https://github.com/whence/chess-tui.git
cd chess-tui
uv sync
```

## Scripts

### chess-tui

Play chess locally in your terminal.

```bash
uv run chess-tui
```

Controls:
- Arrow keys: navigate board
- Space: select/place piece
- Enter: confirm move
- Escape: cancel selection
- `f`: flip board
- `r`: reset game
- `o`: re-fire every `--observer` URL with the current position (manual trigger, in addition to the automatic per-move notification)
- `q`: quit

Flags:
- `--fen FEN` — start from a specific position.
- `--opening NAME` — start from a named opening. Accepts an ECO code
  (e.g. `B90`), a full name (e.g. `Sicilian Defense: Bowdler
  Attack`), or a case-insensitive substring. Mutually exclusive
  with `--fen`. The opening's name and ECO are shown in the title bar.
  Any query that matches more than one row triggers an interactive
  selector before the board mounts: pick with the arrow keys and
  Enter, or Escape to cancel and quit. This applies uniformly — even
  an exact ECO like `--opening B90` (15 entries in the B90 family)
  and an exact name like `--opening "Sicilian Defense: Najdorf
  Variation, English Attack"` (5 transposition duplicates) show the
  selector so the user can pick the specific line they want. The
  only fast path is a query that matches exactly one entry
  (e.g. `--opening Bongcloud Attack`), which resolves silently.
  Transposition duplicates (rows with the same ECO *and* name) are
  listed with a move-diff column that omits the common prefix and
  shows only the divergent tail, e.g. `→ e5 Nb3 Be6 f3`.
- `--list-openings [SUBSTRING]` — print every bundled opening whose
  name or ECO matches `SUBSTRING` (default: list all ~3,800) and exit.
  Useful for discovering the exact name to feed into `--opening`.

Examples:
```bash
# Start from the Sicilian Najdorf.
uv run chess-tui --opening B90

# Browse the catalog interactively.
uv run chess-tui --list-openings najdorf
```

### chess-tui-net

Network player server (plays moves over HTTP).

```bash
uv run chess-tui-net --port 8080
```

Then in another terminal:
```bash
uv run chess-tui --white http://localhost:8080
```

### chess-tui-engine

Engine-powered server using UCI chess engines.

```bash
# Edit engines.json to set engine paths, then:
uv run chess-tui-engine --port 8081 --engine plentychess --depth 20 --multipv 5

# Use with TUI:
uv run chess-tui --black http://localhost:8081
```

By default (no flag needed), every `POST /move` request logs the full
analysis in the same format as `chess-coach-v3`'s `/engine` command —
board, engine line, and one row per principal variation with score,
depth, and SAN moves:

```
────────────────────────────────────────
Move 1 — White to move
r n b q k b n r
p p p p p p p p
. . . . . . . .
. . . . . . . .
. . . . . . . .
. . . . . . . .
P P P P P P P P
R N B Q K B N R
Thinking for 0.0s...
  Engine: plentychess (depth 12, multipv 3)
  FEN: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1

  PVs:
    #1  +0.51/12  e4 c5 Nf3 d6 Bb5+ Bd7 Bxd7+ Qxd7 O-O e6
    #2  +0.26/12  Nf3 Nf6 g3 g6 c4 c5 Bg2 Bg7 Nc3 O-O O-O d5 cxd5
    #3  +0.16/12  d4 Nf6 Nf3 e6 e3 Be7 Be2 d5 O-O O-O c3
  Engine plays: e4
```

`--multipv N` (default 1, max 20) controls how many lines are printed
and analyzed. The score is always from **White's perspective** — `+`
means white is better, `-` means black is better, regardless of whose
turn it is. The engine still plays the best (top) line either way.

Pass `--quiet` (`-q`) to silence the per-move log entirely (useful for
background processes / log-file redirection). The engine still picks
the best move; only stdout is silenced.

This is especially useful with `chess-tui --observer`: the engine's
own terminal shows the analysis, while the TUI plays its own game
unaffected.

### chess-tui-nova

Nova neural network player. Uses per-move sampling knobs
(`--temperature`, `--top-p`, `--blunder-rate`) to vary playing strength
and style from a single ELO conditioning.

```bash
uv run chess-tui-nova 8082 --elo 1500
uv run chess-tui --black http://localhost:8082
```

The default invocation (`--elo` only) samples directly from Nova's
natural policy distribution — equivalent to
`--temperature 1.0 --top-p 1.0 --blunder-rate 0.0`.

#### Quick presets

```bash
# Greedy / engine-like: always play Nova's top move
uv run chess-tui-nova 8082 --elo 1500 --temperature 0

# Strong, focused: sharpen the distribution, restrict to top moves
uv run chess-tui-nova 8082 --elo 2000 --temperature 0.6 --top-p 0.85

# Casual: slight randomness on top of Nova's distribution
uv run chess-tui-nova 8082 --elo 1500 --temperature 1.1 --top-p 0.9

# Beginner: more random, plus occasional outright blunders
uv run chess-tui-nova 8082 --elo 1000 --temperature 1.3 --top-p 0.95 --blunder-rate 0.05
```

#### Sampling knobs

- `--temperature` (`>=0`, default `1.0`): `0` is greedy; `1.0` is Nova's
  natural distribution; values `<1` sharpen the distribution (more
  confident), values `>1` flatten it (more random).
- `--top-p` (`(0, 1]`, default `1.0`): nucleus sampling cutoff — keep only
  the smallest set of top moves whose cumulative probability exceeds this
  value. `1.0` disables filtering.
- `--blunder-rate` (`[0, 1]`, default `0.0`): probability of replacing the
  sampled move with a uniformly random legal move, simulating an outright
  human-style mistake.

### chess-tui-maia

Maia-3 (5M) human-move predictor, driven via UCI. Uses Elo conditioning
(`Elo` / `SelfElo` / `OppoElo`) plus the same `Temperature` / `TopP`
sampling knobs as Nova. Exposes the same `POST /move` RESTful protocol as
the other network players, so it can substitute for Nova in any TUI match.

The `maia3` Python package is **not** a chess-tui dependency — it is
installed separately. The server reads the `maia3-5m` (or equivalent)
executable path from `engines.json` and spawns it as a long-lived UCI
subprocess.

```bash
uv run chess-tui-maia 8083 --elo 1500
uv run chess-tui --black http://localhost:8083
```

#### One-time setup

`maia3` is **not on PyPI** — it lives on GitHub and must be installed
from source. Pick one:

```bash
# Recommended: uv tool install (puts maia3-5m, maia3-cache, etc. on PATH)
uv tool install 'maia3 @ git+https://github.com/CSSLab/maia3.git'

# Or: pip (user-site, requires PATH to include the user bin)
pip install --user git+https://github.com/CSSLab/maia3.git

# Or: clone and install locally
git clone https://github.com/CSSLab/maia3.git
cd maia3
pip install .
```

Then pre-download the 5M model so the first match doesn't time out
waiting on Hugging Face:

```bash
maia3-cache
```

Then point `engines.json` at the executable (use the absolute path if
`maia3-5m` is not on `PATH`):

```json
{
  "maia": { "path": "maia3-5m" }
}
```

#### Quick presets

```bash
# Default: maia's natural distribution at the requested Elo
uv run chess-tui-maia 8083 --elo 1500

# Greedy
uv run chess-tui-maia 8083 --elo 1500 --temperature 0

# Stronger / more focused
uv run chess-tui-maia 8083 --elo 2000 --temperature 0.6 --top-p 0.85

# Asymmetric match (self plays at one Elo, opponent at another)
uv run chess-tui-maia 8083 --elo 1500 --self-elo 1400 --oppo-elo 1800

# Disable history mode (engine gets the FEN only, no move history)
uv run chess-tui-maia 8083 --elo 1500 --no-use-history
```

#### Nova vs. Maia match

Both servers speak the same RESTful protocol, so a cross-engine match is
just two servers + the TUI pointing at both:

```bash
# Terminal 1
uv run chess-tui-nova 8082 --elo 1400

# Terminal 2
uv run chess-tui-maia 8083 --elo 1400

# Terminal 3
uv run chess-tui --white http://localhost:8082 --black http://localhost:8083
```

Note: Nova and Maia's "1400" are not strictly the same 1400 — they were
trained on different data and condition on Elo differently (Nova takes a
single rating scalar; Maia splits SelfElo / OppoElo). The match is
well-defined (both engines do their best to play like a 1400 human by
their own lights), but the result tells you about each model's
definition of 1400, not a single ground truth.

#### Sampling knobs

- `--elo` (required, `800`-`2700`): ELO applied to both sides. Overridden
  individually by `--self-elo` / `--oppo-elo` if either is set.
- `--self-elo` / `--oppo-elo` (optional): ELO of the side to move /
  opponent. Default to `--elo` if unset.
- `--temperature` (`>=0`, default `1.0`): `0` is argmax; `1.0` is maia's
  natural distribution; values `<1` sharpen, `>1` flatten.
- `--top-p` (`(0, 1]`, default `1.0`): nucleus sampling threshold.
- `--multi-pv` (`[1, 20]`, default `1`): number of top candidate moves
  maia logs per move. **Logging-only** — maia still plays one sampled
  move, and the MultiPV list is computed from the raw softmax (T=1),
  independent of `--temperature` / `--top-p`.
- `--use-history` / `--no-use-history` (default on): pass the full move
  history to maia via `--use-uci-history`. On, the engine receives the
  last 8 board states as transformer context (matching training). Off,
  the engine sees only the current FEN.

## Configuration

### Network play

All four servers (`chess-tui-net`, `chess-tui-engine`, `chess-tui-nova`,
`chess-tui-maia`) bind to `0.0.0.0` by default — i.e. **all network
interfaces** — so a TUI on a different machine can reach them over
LAN/internet. The startup banner shows both the bound URL and the
discovered LAN address:

```
chess-tui engine server listening on http://0.0.0.0:8080 (all interfaces; LAN: http://192.168.1.42:8080)
```

To restrict to localhost (e.g. you're on a public network and don't want
to expose the server), pass `--host 127.0.0.1`:

```bash
uv run chess-tui-engine --port 8080 --engine plentychess --host 127.0.0.1
```

When a TUI's observer POST fails (e.g. an observer on a remote machine
is still bound to localhost), the failure is logged to **stderr** as a
single line:

```
[chess-tui observer] http://192.168.1.99:8084: could not reach http://192.168.1.99:8084/move: [Errno 61] Connection refused
```

This makes "the first observer isn't being called" bugs visible: the
silent connection failure now appears in the TUI's terminal.

### Observer mode (`--observer`)

In addition to routing a side to a network player with `--white` / `--black`,
you can also attach any number of **observer** servers to the TUI. After
every move (both white and black), the TUI POSTs the current FEN and SAN
move history to each observer, but **does not wait for or use the
response**. Observers are best-effort and fire-and-forget — a slow
observer never slows the game down, and an unreachable one is silently
ignored.

Observers are just regular chess-tui network player servers
(`chess-tui-engine`, `chess-tui-nova`, `chess-tui-maia`, etc.). They
have no idea whether they're being used as a player or an observer; they
compute a move and print it on their own stdout. The TUI discards the
response.

```bash
# Terminal 1: one engine
uv run chess-tui-engine --port 8082 --engine plentychess --depth 20

# Terminal 2: a different engine
uv run chess-tui-nova 8083 --elo 1500

# Terminal 3: a third observer
uv run chess-tui-maia 8084 --elo 1400

# Terminal 4: a local human plays white vs. stockfish, with nova + maia watching
uv run chess-tui --black http://localhost:8082 \
                --observer http://localhost:8083 \
                --observer http://localhost:8084
```

You can also pass multiple URLs after a single `--observer`:

```bash
uv run chess-tui --white http://localhost:8080 \
                --observer http://localhost:8081 http://localhost:8082 http://localhost:8083
```

Observers are notified **only after a move** — never on game start, board
flip (`f`), or reset (`r`). They run in parallel (each POST is dispatched
on a separate worker thread), so a single slow engine can't bottleneck the
others.

#### Manual observer re-fire (`o` key)

To get the engine's take on the *current* position without playing a
move, press `o` to manually re-fire the same notification that the
per-move path would send. Useful when you've been thinking for a
while and want a fresh analysis snapshot, or when an observer was
briefly down and you want to catch it up. The payload is identical
to the automatic path (current FEN + SAN history), and all
configured `--observer` URLs are notified in parallel.

If the TUI was started without `--observer`, pressing `o` shows a
status hint rather than failing silently.

### `engines.json`

Engine paths are configured in `engines.json`:

```json
{
  "engines": {
    "plentychess": "path/to/plentychess",
    "stockfish": "path/to/stockfish",
    "dragon": "path/to/dragon"
  },
  "nova": {
    "path": "path/to/nova_v3b.onnx"
  },
  "maia": {
    "path": "maia3-5m"
  }
}
```

## Running Tests

```bash
uv run pytest
```
