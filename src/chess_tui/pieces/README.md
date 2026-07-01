# Chess piece imagery

Raster assets for rendering chess pieces in the TUI.

## Source

The Cburnett set, by [Cburnett](https://commons.wikimedia.org/wiki/User:Cburnett)
on Wikimedia Commons:
<https://commons.wikimedia.org/wiki/Category:PNG_chess_pieces/Standard_transparent>

The Wikimedia files are 60×60 RGBA PNGs derived from
[`Chess_plt45.svg` etc.](https://commons.wikimedia.org/wiki/File:Chess_plt45.svg).
We fetched the 60×60 PNGs and upscaled to **64×64** with Lanczos resampling
(Pillow) so each file is a clean 64×64 RGBA on a fully transparent background.

## File naming

`<color><piece>.png`, e.g. `wK.png` (white king), `bP.png` (black pawn).

| Code | Piece  |
|------|--------|
| P    | Pawn   |
| N    | Knight |
| B    | Bishop |
| R    | Rook   |
| Q    | Queen  |
| K    | King   |

The `w`/`b` prefix is the side: `w` for white (light-coloured piece),
`b` for black (outlined dark piece).

## Licensing

The Cburnett set is dual-licensed **GFDL 1.2+** and **CC BY-SA 3.0**.
See `THIRD_PARTY_NOTICES.txt` in the project root for the full attribution
and the verbatim license texts.
