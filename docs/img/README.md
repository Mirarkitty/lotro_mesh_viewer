# Documentation images

All images here are rendered by this toolkit from a local LOTRO install —
no image files were extracted from the game as-is.

- `viewer-shaded.png` / `viewer-wire.png` — this repo's own pipeline:
  `compose.py 0x7000DA5B 0x20001E58 exquisite_dwarfM`, then `app.py` +
  `screenshot.py exquisite_dwarfM.json <out> [--wire]`.
- `outfit-snowdusted.png` / `outfit-snowdusted-dyed.png` — the *Snow-dusted
  Travelling* set (5 items) composed, skinned and dyed on an Elf body by the
  full outfit-composer development tree built on these same modules.

Regenerate after decoder/viewer improvements and commit the new PNGs.
