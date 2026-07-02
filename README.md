# chess-tui

A terminal chess application with network play support.

## Setup

```bash
git clone git@github.com:whence/chess-tui.git
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
- `q`: quit

### chess-tui-net

Network player server (plays moves over HTTP).

```bash
uv run chess-tui-net 8080
```

Then in another terminal:
```bash
uv run chess-tui --white http://localhost:8080
```

### chess-tui-engine

Engine-powered server using UCI chess engines.

```bash
# Edit engines.json to set engine paths, then:
uv run chess-tui-engine 8081 --engine plentychess --depth 20 -v

# Use with TUI:
uv run chess-tui --black http://localhost:8081
```

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

## Configuration

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
  }
}
```

## Running Tests

```bash
uv run pytest
```
