# mesh_decode.py

[`mesh_decode.py`](../../mesh_decode.py)

## Purpose

`mesh_decode.py` is the unified geometry decoder: it turns a LOTRO GfxObj
mesh record (DID type `0x06`, from `client_mesh.dat`) into renderable
positions + normals + UVs + triangles. It handles **both** static meshes
(`Flags != 0x1000____`) and skinned meshes (`Flags == 0x1000____`) with one
decoder, `_decode_gfxobj`, because static turns out to be the 1-submesh case
of the same format that skinned meshes use. See
[../mesh-format.md](../mesh-format.md) for the full byte-layout writeup.

In the pipeline (item → PropertiesSet → worn appearance → mesh + material →
texture → dye → render), this is the "mesh" stage: it consumes a mesh DID
(reached via [selector.py](selector.md) or [wearable2.py](wearable2.md)) and
produces the vertex/triangle arrays that [tex_extract.py](tex_extract.md)
then textures and [compose.py](compose.md)/[viewer.md](viewer.md) render.

Reference sample used throughout the module's docstrings: mesh
`0x06001989` (static, `Flags=0x00000001`, 7186 bytes).

## CLI usage

```
python3 mesh_decode.py <did> [--json FILE] [--no-textures] [--game-dir DIR] [--out-dir DIR]
```

| Argument | Meaning |
|---|---|
| `did` | mesh DID, hex (e.g. `0x06001989`) |
| `--json FILE` | write the decoded mesh as viewer JSON to `FILE` (`-` for stdout) |
| `--no-textures` | skip diffuse-texture resolution (faster; every group's `texture` is `None`) |
| `--game-dir DIR` | LOTRO install directory (default `$LOTRO_DIR` or a probe of common paths) |
| `--out-dir DIR` | output root for `decoded/`/`textures/` (default `$LOTRO_OUT` or the repo dir) |

Examples:

```
python3 mesh_decode.py 0x06001989
python3 mesh_decode.py 0x06001989 --json decoded/spindle.json
python3 mesh_decode.py 0x0600D54A --no-textures --json -
```

Output: validation `stats` (see below) printed to stdout, and optionally the
decoded mesh written as JSON.

## Public API

| Function | Signature | Returns |
|---|---|---|
| `decode_mesh` | `decode_mesh(did, with_textures=True, texture_override=None)` | `{id, flags, num_submeshes, vertices, normals, uvs, triangles, groups}` — the primary entry point for ANY GfxObj mesh (static or skinned) |
| `decode_skinned` | `decode_skinned(did)` | alias for the raw `_decode_gfxobj` result (kept for compatibility; also has `flags`/`num_submeshes`) |
| `stats` | `stats(m)` | dict of validation statistics on a decoded mesh dict (see below) |

`decode_mesh`'s `groups` is a per-submesh list of
`{submesh, vert_start, vert_count, tri_start, tri_count, texture}`, where
`texture` is the resolved diffuse `0x41` texture DID string (via
[`tex_extract.mesh_textures`](tex_extract.md)) or `None`. The three.js
viewer ([viewer.md](viewer.md)) builds one geometry group per entry
(`tri_start*3, tri_count*3`) so each submesh can carry its own texture map.

`texture_override` (an int DID) forces every submesh's `texture` to that
value instead of auto-resolving — used for meshes whose diffuse is bound at
the appearance/outfit level rather than in the mesh's own surface graph
(e.g. mesh `0x0600D54A`, the dress body, whose surface references only the
normal/gloss shader instance so its diffuse `0x41231998` is not
surface-local — verified by coherent UV mapping).

`stats(m)` returns:

| Key | Meaning |
|---|---|
| `num_vertices`, `num_triangles` | counts |
| `bbox_min`, `bbox_max` | axis-aligned bounding box |
| `max_index`, `indices_in_range` | triangle-index sanity |
| `vertices_referenced` | count of distinct vertex indices actually used by a triangle |
| `degenerate_tris` | triangles with a repeated vertex index |
| `nan_coords` | non-finite vertex coordinate count |
| `bbox_diag` | bounding-box diagonal length |
| `max_edge`, `sliver_tris` | longest triangle edge, and count of edges exceeding half the bbox diagonal (see "sliver check" below) |

## How it works internally

### Byte layout (see full detail in [../mesh-format.md](../mesh-format.md))

Header: `[Id][Flags][numSurfaces][0x31 surfaceDID × numSurfaces][numTextures][0x30 textureDID × numTextures][numVertices]...`.
Then, **per submesh**, in order: `uint32 vertexCount`, that many
fixed-stride vertex records (56 B static / 71 B skinned-dress, but the
stride is auto-detected per block — not assumed), a `float32 bbox[6]`, and
`uint32 boneDataCount` + that many bone-data u32s (0 for static). After all
submeshes, the index region follows sequentially: per submesh, `uint32
indexCount` + that many `uint16` submesh-local 0-based triangle indices.

Each vertex record: `pos[3]` (f32), `normal[3]` (f32, unit length — this is
the parser's primary validity check), then `uv[2]` at a fixed offset (24
bytes in from the record start, i.e. right after pos+normal).

Byte-packed tables can start at **odd offsets** — the parser never assumes
4-byte alignment; it reads offsets exactly as found.

### `_vertex_mask` — the vectorised scan

`_is_vertex(raw, o)` is the scalar predicate: "does a vertex record plausibly
start at byte offset `o`?" (finite, bounded position floats, plus a
unit-length normal at `o+12`). The straightforward implementation calls this
once per candidate offset per candidate stride — roughly 3M Python calls (6
`struct.unpack`s each) for a big garment, ~94% of `compose()`'s wall time.

`_vertex_mask(raw)` replaces that with one NumPy pass: all six floats a
vertex probe reads sit at `o, o+4, ..., o+20` — i.e. **the same offset
residue mod 4** — so one strided `float32` view per residue (`r` in `0..3`)
evaluates the whole predicate array-at-a-time via vectorised NumPy ops. The
result is bit-identical to the scalar predicate (guarded by
`test_mesh_decode.py`), returned as a `bytes` mask the same length as `raw`.

### `_find_vertex_blocks` — locating each submesh's vertex block

Scans for `[uint32 count][count vertices]` at a fixed stride. For each
candidate count prefix (`8 <= c <= 60000`) whose next byte reads as a vertex
per the mask, it tries **every plausible stride from 44 to 264 bytes**,
looking for the one whose consecutive unit-normal run matches the declared
count exactly (within ±2). This exhaustive stride search matters for
multi-submesh meshes where **different submeshes use different vertex
strides** (e.g. mesh `0x060028FC` mixes 76/71/61-byte records) — a shorter
wrong stride can pass an initial 3-consecutive-vertex probe, so the scan
must not stop at the first hit; it must keep searching larger strides for
the one that actually spans the declared count. Results are memoised in
`_VBLK_CACHE` (keyed on `(len(raw), hash(raw))`, capped at 64 entries) since
[export_skinned.py](export_skinned.md)'s `skin_arrays()` scans the identical
buffer a second time for every part of a composed outfit.

### `_find_index_region` — locating the index buffers

Scans forward from byte 8, trying at every offset to sequentially parse
`[count][count × uint16]` once per submesh, accepting the offset only if
every buffer's max index equals its submesh's `vertex_count - 1`.

### `stats()`'s sliver check

A correctly-parsed solid mesh has every triangle edge much shorter than the
bounding-box diagonal. "Sliver" triangles — edges spanning roughly the
whole mesh — are the signature of a **scrambled index/vertex mapping that
still passes in-range and degenerate checks** (i.e. the numbers all look
individually valid, but the geometry is garbage). `stats` flags any edge
longer than half the bbox diagonal as a sliver; `sliver_tris == 0` on a
correctly decoded mesh is a load-bearing check used elsewhere (e.g.
[selector.py](selector.md)'s `human_standin_mesh`).

## Gotchas & lessons

- **Statistics alone can lie.** `stats()` catches NaN coordinates,
  out-of-range indices, and degenerate triangles, but a scrambled
  index/vertex mapping can pass all of those and still render as garbage —
  only the sliver-edge heuristic (or an actual screenshot, see
  [screenshot.py](screenshot.md)) catches that class of bug. This is called
  out explicitly in [screenshot.py](screenshot.md)'s docstring as "the
  project's most important verification lesson."
- **Never silence texture-resolution failures.** `decode_mesh` records the
  exception message in `g["texture_error"]` on every group rather than
  swallowing it — so a caller can see *why* textures came back empty instead
  of silently getting `texture: None` everywhere.
- **Never assume 4-byte alignment.** Byte-packed tables in this format can
  start at odd offsets; every offset is read exactly as found, never
  rounded.
- **The stride search must not stop at the first hit** (see
  `_find_vertex_blocks` above) — an earlier version did, and silently
  dropped every stride-76 submesh in mixed-stride meshes, which then made
  the index region unfindable for the whole record.

## See also

- [../mesh-format.md](../mesh-format.md) — full GfxObj byte-layout reference.
- [tex_extract.py](tex_extract.md) — resolves each submesh's diffuse texture (`mesh_textures`).
- [selector.py](selector.md) — resolves an item to the mesh DID(s) this module decodes.
- [compose.py](compose.md) — composes multiple decoded parts (using this module) into one textured outfit.
- [export_skinned.py](export_skinned.md) — reuses `_find_vertex_blocks` for per-vertex skin data.
- [screenshot.py](screenshot.md) — the visual-verification step this module's stats cannot replace.
- [INDEX.md](INDEX.md) — full script index.
