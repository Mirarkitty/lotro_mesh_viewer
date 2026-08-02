# tex_extract.py

[`tex_extract.py`](../../tex_extract.py)

## Purpose

`tex_extract.py` covers two related jobs in the pipeline (item →
PropertiesSet → worn appearance → mesh + material → **texture** → dye →
render):

1. Decode a raw `0x41` texture record (DXT1/DXT3/DXT5, from
   `client_surface.dat` or `client_highres.dat`) to an RGBA PNG.
2. Walk the material graph outward from a mesh's `0x31` surface DIDs (→
   `0x2B` shader / `0x30` material → `0x40` texture-slot records) to resolve
   which `0x41` texture is the coherent garment **diffuse** for each
   submesh, or for a whole worn-appearance entry.

See [../textures.md](../textures.md) for the full DID-evidence writeup this
module's comments summarize.

## CLI usage

```
python3 tex_extract.py texture <texture_did>              # extract one 0x41 texture to PNG
python3 tex_extract.py mesh <mesh_did>                     # per-submesh diffuses
python3 tex_extract.py appearance <appearance_did> <mesh_did>   # wardrobe-entry diffuses
```

Every subcommand also accepts `--game-dir DIR` / `--out-dir DIR`.

| Subcommand | Arguments | What it does |
|---|---|---|
| `texture` | `did` (hex, e.g. `0x41231998`) | extracts and prints `(w, h, fourcc)` info for one `0x41` DID |
| `mesh` | `did` (hex mesh DID) | resolves and extracts each submesh's diffuse for a `0x06` mesh |
| `appearance` | `appearance_did mesh_did` (hex) | all coherent garment diffuses binding `mesh_did` inside a `0x20` worn-appearance record |

Examples:

```
python3 tex_extract.py texture 0x41231998
python3 tex_extract.py mesh 0x0600D54A
python3 tex_extract.py appearance 0x20001E55 0x0600D54A
```

## Public API

| Function | Signature | Returns |
|---|---|---|
| `extract_texture` | `extract_texture(did)` | writes `textures/0x<DID>.png`, returns the path |
| `texture_info` | `texture_info(did)` | `(w, h, fourcc, is_placeholder)` or `None` if not a texture record |
| `parse_texture` | `parse_texture(raw)` | `(w, h, fourcc, block_bytes)` or `None`, anchoring on the DXT fourcc |
| `mesh_textures` | `mesh_textures(mesh_did)` | `{submesh_index: textureDID}` for a `0x06` mesh |
| `diffuse_for_surface` | `diffuse_for_surface(surf_did)` | diffuse texture DID for one `0x31` surface, or `None` |
| `material_diffuse` | `material_diffuse(mat_did)` | diffuse texture DID for one `0x30` material (first `0x40` slot), or `None` |
| `parse_appearance_entries` | `parse_appearance_entries(appearance_did)` | list of `{mesh, materials:[0x30...], shaders:[0x2B...]}`, one per draw entry in a `0x20` record |
| `appearance_diffuses` | `appearance_diffuses(appearance_did, mesh_did)` | deduped list of coherent garment diffuse DIDs for `mesh_did` inside the record |

## How it works internally

### `0x41` texture record format

Verified on `0x41231998` (512×512 DXT1) and `0x41000081` (4×4 DXT3). The
parser doesn't rely on a fixed header offset for width/height — it anchors
on the ASCII fourcc string (`DXT1`/`DXT3`/`DXT5`) wherever it occurs in the
record. The 8 bytes immediately before the fourcc are `width, height`
(`uint32` LE); the compressed block data begins at `fourcc_offset + 8` (4
fourcc bytes + one trailing dword). Exact block size:
DXT1 = `(w/4)*(h/4)*8`; DXT3/DXT5 = `(w/4)*(h/4)*16`. `parse_texture`
validates by requiring the read to produce exactly that many bytes, guarding
against landing on a fourcc-like byte run in a non-texture record. Two
header variants exist (differing in what precedes width/height — a
type/count dword, or the self-DID) and are irrelevant to this parser because
it never looks at the record start.

`extract_texture` wraps the raw block bytes in a minimal 128-byte DDS header
(`_make_dds`) and hands that to Pillow to decode, then saves atomically
(write to a `.tmp` file, `os.replace`) because `textures/` is served live by
[viewer.md](viewer.md) while extraction may be running concurrently.

### `read_content` vs `read_asset` — the trap this module hit and fixed

Small, exactly-sized records (like a compact `0x31` surface, 22 bytes) must
be read with `DatFile.read_content`, not `read_asset` — `read_asset`
over-reads to a whole archive block, pulling in neighbouring records'
bytes. `_compact_surface_materials` and `material_diffuse` both call
`gen.read_content(did)` specifically because of this: an earlier version
used `read_asset` on these small records and, on the "Ordakhai coat" case,
picked up a **neighbour record's texture** — a real, previously-shipped bug
(the "blue plumage" bug, see the long comment on `diffuse_for_surface`).
Legacy multi-pass surfaces (which don't parse as the compact 22-byte format)
still use `read_asset` + a brute graph scan, kept byte-identical to their
historically-verified behavior. See [datfile.py](datfile.md)'s "two payload
framings" section for the general rule.

### Surface → material → diffuse resolution

`_compact_surface_materials(raw)` parses the modern, compact `0x31` record
layout exactly:
`[self DID 0x31][shader 0x2B][slot key][u32 nmat][nmat × 0x30 material][u16 flags]`
(22 bytes for `nmat=1`). This turned out to be **the** `0x31` format, old
and new alike — records that looked like ~1KB "legacy 2-pass surfaces" in
earlier notes were actually 22-byte true records over-read by `read_asset`
into 1KB of neighbour-record garbage. For the newest-generation items (meshes
in `client_mesh_aux_1.datx`, `0x20` wearable entries with zero material
groups), this surface→material link is the **only** texture binding.

The third field is a `0x10`-tagged **slot key** on garment surfaces, but a
small plain integer (seen: `1`) on held-item surfaces (weapons/class
items) — same 22-byte layout otherwise, just a different convention for
that one field. `_compact_surface_materials` accepts both:
`(key >> 24) == 0x10` or `key <= 0xFF`; anything else is rejected as not
this format. Found while adding weapon texture support — see
[../weapons.md](../weapons.md#texture-binding) for why weapon surfaces
carry the small-int form.

`diffuse_for_surface(surf_did)` tries the compact-format parse first, walking
each referenced material through `material_diffuse`. Only if that fails does
it fall back to the historical brute graph scan (`_resolvable` over `{0x2B,
0x30, 0x31}` types, then `_brute_textures` on each reachable record, keeping
the largest non-placeholder DXT texture `>= MIN_DIFFUSE_PX` (64px) on its
longest side).

`material_diffuse(mat_did)` reads the material's **true** content bytes
(`read_content` — see above) and finds its `0x40` texture-slot records in
byte order via `_material_40_slots`. The **first** `0x40` slot by offset is
the diffuse; the function returns as soon as `_largest_tex_in_40` finds a
qualifying texture in that slot.

`mesh_textures(mesh_did)` reads the mesh header (asset framing —
`_mesh_surfaces` — one `0x31` surface DID per submesh, in order) and resolves
each surface's diffuse, with a small per-call cache (`cache = {}`) so
surfaces shared across submeshes aren't re-resolved.

### Appearance-record resolution (`0x20` records)

`parse_appearance_entries` scans for the constant marker `0x1000000C`
(`b"\x0c\x00\x00\x10"`) that delimits each draw entry in a `0x20`
worn-appearance record, and within each entry's byte range brute-scans for
`0x30` material and `0x2B` shader DIDs that resolve in `client_general.dat`.
`appearance_diffuses` then resolves each of those materials to a diffuse via
`material_diffuse`. This is a coarser tool than [wearable2.py](wearable2.md)
or [selector.py](selector.md) — it returns **every** coherent diffuse bound
to a mesh across the whole wardrobe record, not the one specific item's
selection; picking the exact entry for a specific item needs the item's own
`Item_AppearanceKey` (see [selector.py](selector.md)).

## Gotchas & lessons

- **The first `0x40` slot rule, not "largest texture across all slots."**
  Picking the largest texture across ALL of a material's `0x40` slots is
  WRONG — later slots hold a shared prop/normal atlas that can be *bigger*
  than the actual diffuse. Example: dress material `0x300043D2` — slot 0 is
  `0x41105076` (the dress fabric, correct), slot 3 is `0x4100BFC7`
  (1024×1024, a shared **wooden-crate atlas** — the "crate-atlas trap").
  Armour material `0x300045B2` has the same shape: slot 0 = gold
  breastplate, but the largest overall is again the crate atlas. Verified
  visually that slot 0 is the coherent garment diffuse for both cases.
- **`read_content` vs `read_asset` for small exact records** — see the
  dedicated section above; this is the single most consequential gotcha in
  the module (it caused a shipped, visually-wrong texture on the Ordakhai
  coat).
- **Placeholder textures.** Tiny (≤8px) DXT tiles are unassigned-slot
  stand-ins, not real diffuses; `MIN_DIFFUSE_PX = 64` and the
  `texture_info(...).is_placeholder` check exist specifically to filter
  these out of the "largest candidate" search.
- **Scope limit: composite worn-appearance meshes are not fully
  resolvable from the mesh's own material graph.** A full worn-appearance
  mesh's UV set covers bare skin AND garment cloth in one submesh, so a
  single diffuse must paint both together. The garment overlay (e.g. dress
  bodice `0x41231998` via shader instance `0x2B0007A0`) is reliably
  resolvable; the per-body-part BASE diffuse (skin/cloth) generally is not —
  a surface's `0x30` materials chain to a **shared armor-texture library**
  (breastplates/cloaks/normal-maps), not the specific garment. That binding
  lives in the appearance `0x20` record's variable-length per-mesh entries
  instead (see [wearable2.py](wearable2.md)), which this module does not
  parse.

## See also

- [../textures.md](../textures.md) — full DID-evidence writeup and known traps.
- [../weapons.md](../weapons.md#texture-binding) — the held-item corner
  case of the compact `0x31` surface format above.
- [mesh_decode.py](mesh_decode.md) — consumes `mesh_textures` to attach diffuses to decoded groups.
- [selector.py](selector.md) — uses `material_diffuse` for the exact per-item selection this module can't do alone.
- [wearable2.py](wearable2.md), [compose.py](compose.md) — the finer-grained per-entry material binding this module's `0x20` parsing is coarser than.
- [datfile.py](datfile.md) — the `read_content`/`read_asset` distinction this module's biggest bugfix hinged on.
- [../dyes.md](../dyes.md) — what happens to a resolved diffuse downstream (dye tinting).
- [INDEX.md](INDEX.md) — full script index.
