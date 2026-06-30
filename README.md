# chess-tui

A terminal chess application with network play support.

## Setup

```bash
git clone gitlab.com:wesley.li/chess-tui.git
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

Nova neural network player with ELO variability.

```bash
uv run chess-tui-nova 8082 --elo 1500

# With ELO variation:
uv run chess-tui-nova 8082 --elo 1600 \
  --elo-low 1200 --elo-low-credit 2 \
  --elo-high 2000 --elo-high-credit 3 -v

# Use with TUI:
uv run chess-tui --black http://localhost:8082
```

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
    "path": "path/to/nova_v3b.onnx",
    "classical": 0.5,
    "aggression": 0.5
  }
}
```

## Running Tests

```bash
uv run pytest
```
