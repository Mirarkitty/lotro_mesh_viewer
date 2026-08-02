# charparts.py

[`charparts.py`](../../charparts.py)

## Purpose

`charparts.py` composes a playable character's own **head (face), hair, and
beard** geometry — the chargen choices made at character creation, as
opposed to worn items. It is the implementation behind
[../hair-face.md](../hair-face.md)'s "Composing a full avatar head"
section: that page describes the format and the derivation; this module is
the code that decodes the chain and appends the resulting meshes to a
compose-format JSON dict. [api_common.py](api_common.md)'s `compose_face`
is the thin caching wrapper the outfit composer server calls; this module
does the actual decode.

## The chargen APR chain

```
playable (race, sex)
  -> 0x47 avatar entity record (client_gamelogic.dat)
     AppearanceUI_Controls: per-slot APRControl entries
       AppearanceUI_APRKey  0x10000009 -> HEAD/FACE  (AC_Mesh_Head)
       AppearanceUI_APRKey  0x10000032 -> HAIR       (Av_Avatar_Hair_Style)
       AppearanceUI_APRKey  0x10000018 -> BEARD      (Av_Avatar_Beard_Style)
  -> AppearanceUI_APRFile: a 0x20 record (client_general.dat) with ONE entry
     (key = the slot key above) whose BLOCKS are the STYLES
  -> block i's part list has exactly one part
       {tag 0x10000002 head | 0x1000000E hair | 0x10000007 beard} -> one
       0x06 GfxObj mesh
```

The block selector `q` encodes the style id (`0.00, 0.01, ...`, with
generation-jump gaps at `0.30x`/`0.40x` marking later style waves). A
**198-byte stub mesh** as a style entry means "none" — the bald/no-beard
option, the same stub convention used throughout the wardrobe format for
"nothing here" (see [../wardrobe.md](../wardrobe.md)). `charparts.py` uses
its own `STUB_SIZE = 2000` threshold (matching the on-disk stub-detection
convention used elsewhere, e.g. [api_common.py](api_common.md)'s
`STUB_BYTES`) to decide `present` per style.

`CHARGEN` hardcodes the avatar/head/hair/beard DIDs per (race, sex), derived
from the `0x47000604`–`0x47000610` avatar records:

```python
CHARGEN = {
    "man_m":    dict(avatar=0x4700060A, head=..., hair=..., beard=...),
    "man_f":    dict(avatar=0x4700060B, head=..., hair=..., beard=...),
    "elf_m":    dict(avatar=0x4700060E, head=..., hair=...),          # no beard
    "elf_f":    dict(avatar=0x4700060F, head=..., hair=...),
    "dwarf_m":  dict(avatar=0x47000610, head=..., hair=..., beard=...),
    "hobbit_m": dict(avatar=0x47000606, head=..., hair=...),
    "hobbit_f": dict(avatar=0x47000607, head=..., hair=...),
}
```

Race identification for these avatar records rests on scale + beard
availability + head z-height (hobbit 0.9, dwarf 1.1 male-only+beard, man
beard-on-male, elf no beard) — see [../hair-face.md](../hair-face.md) for
the fuller discussion of how confident that split is.

Chargen blocks carry **no material groups** of their own — each part mesh
binds its diffuse through its own 22-byte `0x31` surface → `0x30` material
chain, same as everywhere else (see
[tex_extract.py](tex_extract.md)'s `mesh_textures`). Hair diffuses are
grayscale strand atlases (tinted at runtime by `AC_Color_Hair`); heads carry
a real skin-tone face texture. The in-game face is further composited from
separate eyebrow/complexion/mouth layers via `0x33000002` — **not**
implemented here (see [../hair-face.md](../hair-face.md)'s "Open" section).

## API

| Function | Signature | Returns |
|---|---|---|
| `styles` | `styles(body, slot)` | `[{index, q, mesh, size, present}]` — every style block of one chargen APR file (`body` = a `CHARGEN` key, `slot` = `'head'`\|`'hair'`\|`'beard'`) |
| `head_bone` | `head_bone(bones)` | index of the head bone in a skeleton bone list (name `== "head"` or ending `_head`), `0` as fallback |
| `_append_mesh` | `_append_mesh(out, mesh_did, texture=None, skin_bones=None, rigid_bone=None, only_surface=None)` | decodes `mesh_did` and appends its LOD-0 submeshes to a compose-format dict `out` in place |
| `_sanitize` | `_sanitize(out)` | zeroes any non-finite/absurd-magnitude float in `out`'s vertex/normal/uv arrays, logging a count |
| `append_style` | `append_style(out, body, slot, index, skin_bones=None, rigid_bone=None)` | appends one chargen style mesh to `out`; returns the mesh DID, or `None` for a stub (bald) style |
| `render_part` | `render_part(body, slot, index, outname, write=True)` | decodes ONE style into a standalone viewer JSON (raises on a stub style) |
| `compose_avatar` | `compose_avatar(item_did, app_did, body, outname, head=0, hair=0, beard=None, write=True)` | `compose.compose(item, app)` plus this body's head/hair/beard styles merged into one viewer JSON |

### `_append_mesh` — the shared meshing primitive

Every other function above that needs to add geometry goes through
`_append_mesh`. It does LOD dedup (largest submesh per surface DID, the
same rule [compose.py](compose.md) uses), and, when `skin_bones` is given,
also emits flat `skinIndices`/`skinWeights` in lockstep (via
[export_skinned.py](export_skinned.md)'s `skin_arrays`) so a chargen part
can be posed by the same skeleton/clip as the rest of the body (see
[../animation.md](../animation.md) for the skinning layer).

- **`rigid_bone`**: chargen meshes are decoded in bind pose (a T-pose), so
  any vertex whose stride carries no known skin data would otherwise fall
  back to bone 0 (`export_skinned`'s default), freezing that part at the
  world origin instead of the head — a fine fallback for a garment part
  (bone 0 is usually near the torso) but wrong for a head part. `rigid_bone`
  rewrites exactly that fallback pattern (skin index `[0,0,0,0]`, weight
  `[1.0,...]`) to the given bone (typically `head_bone(bones)`), and logs
  how many vertices needed the rewrite.
- **`only_surface`**: keep only submeshes bound to one surface DID — used
  by [api_common.py](api_common.md)'s `compose_face` to pull the **bare
  foot** out of a sandal mesh (the game ships no pure bare-foot mesh, but a
  sandal is a skin-surfaced foot plus cloth-surfaced straps, so keeping just
  the skin submesh yields the naked foot).

### `append_style` vs `render_part` vs `compose_avatar`

- `append_style` is the low-level building block used by both
  `compose_avatar` here and `compose_face` in
  [api_common.py](api_common.md) — mutates a shared `out` dict, so multiple
  calls (head, then hair, then beard) accumulate into one mesh.
- `render_part` is a standalone single-style debug/CLI entry point — writes
  its own file, doesn't accumulate with anything else.
- `compose_avatar` is the CLI-facing full-avatar path: garment compose +
  head/hair/beard in one call, writing one `decoded/<outname>.json`. It is
  a simpler, unskinned sibling of `api_common.compose_face` +
  `compose_skinned` (no `skin_bones`/`rigid_bone`, no caching, no hair
  stub-detection via a worn Head item — always renders the given style
  indices as-is).

## CLI usage

From the module docstring:

```
python3 charparts.py styles man_m hair          # list hairstyles
python3 charparts.py render man_m hair 2 out    # decode style -> decoded/out.json
python3 charparts.py avatar 0x7000DA5B 0x20001E58 dwarf_m 1 0 1 out
    # garment compose + head/hair/beard of that race -> decoded/out.json
```

`styles <body> <slot>` prints one line per style: index, `q`, mesh DID,
on-disk size, and `STUB` if it's below the presence threshold. `render
<body> <slot> <index> <outname>` calls `render_part`. `avatar <item_hex>
<app_hex> <body> <head_idx> <hair_idx> <beard_idx|none|-> <outname>` calls
`compose_avatar` (a beard argument of `none` or `-` maps to `beard=None`,
skipping that slot).

## See also

- [../hair-face.md](../hair-face.md) — the format-level writeup of the
  chargen chain, the three-state head-item hair mechanism, and what's still
  unimplemented (hair tint, the face compositor).
- [api_common.py](api_common.md) — `compose_face`, the caching/skinning
  wrapper around this module used by the outfit composer server, including
  `_hair_decision` (what hair a worn Head item implies) and the bare-hands/
  bare-feet tables.
- [compose.py](compose.md) — the garment compositor `compose_avatar` merges
  chargen parts on top of.
- [export_skinned.py](export_skinned.md) — skeleton bones and `skin_arrays`,
  used for skinned head/hair.
- [../animation.md](../animation.md) — the skin-weight vertex-stride table
  `_append_mesh`'s skinning path relies on.
- [INDEX.md](INDEX.md) — full script index.
