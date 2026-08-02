# Documentation images

All images here are rendered by this toolkit from a local LOTRO install —
no image files were extracted from the game as-is.

- `viewer-shaded.png` / `viewer-wire.png` — this repo's own pipeline:
  `compose.py 0x7000DA5B 0x20001E58 exquisite_dwarfM`, then `app.py` +
  `screenshot.py exquisite_dwarfM.json <out> [--wire]`.
- `outfit-snowdusted.png` / `outfit-snowdusted-dyed.png` — the *Snow-dusted
  Travelling* set (5 items) composed, skinned and dyed on an Elf body by
  this repo's own outfit composer (`outfit_app.py`, set search, set-wide
  dye "Ered Luin Blue").
- `outfit-loader.png` — the composer with a LotroCompanion saved outfit
  loaded (toon Tawar, outfit #1), UI panel included on purpose: it
  illustrates the loader controls (see ../outfit-composer.md).

Regenerate after decoder/viewer improvements and commit the new PNGs.
