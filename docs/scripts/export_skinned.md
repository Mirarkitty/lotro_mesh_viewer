# export_skinned.py

[`export_skinned.py`](../../export_skinned.py)

## Purpose

`export_skinned.py` produces the single JSON file the `/anim` viewer page
consumes: a skinned mesh + its skeleton + one decoded animation clip, merged
into one document. This is the animation branch of the pipeline — mesh +
skeleton (from [mesh_decode.py](mesh_decode.md) and a `0x04` hkaSkeleton
record) + clip (from [havok_anim.py](havok_anim.md), a `0x05` record) → one
renderable, posable JSON consumed by [viewer.md](viewer.md)'s `anim.html`.
See [../animation.md](../animation.md) for the full skeleton/clip format
writeup.

## CLI usage

```
python3 export_skinned.py <mesh_did> <skel_json> <clip_did> <out_name> [compose_json] [--game-dir DIR] [--out-dir DIR]
```

| Argument | Meaning |
|---|---|
| `mesh_did` | mesh DID, hex (e.g. `0x060028FD`) |
| `skel_json` | `decoded/skel_*.json` (`{bones:[{name,parent,t,q,s}]}`), produced by `skeleton_bones()` from a `0x04` hkaSkeleton DID |
| `clip_did` | `client_anim.dat` `0x05` clip DID, hex or decimal |
| `out_name` | writes `decoded/<out_name>.json` |
| `compose_json` | optional `decoded/*.json` (from [compose.py](compose.md)) for the SAME mesh whose groups carry per-submesh textures to copy over — otherwise groups are untextured |

Example (clip DID from the module docstring pattern, `0x050039EA`):

```
python3 export_skinned.py 0x060028FD decoded/skel_00000123.json 0x050039EA anim_dress_83893544 decoded/compose_7000DA5B_20001E58.json
```

## Public API

| Function | Signature | Returns |
|---|---|---|
| `skin_arrays` | `skin_arrays(raw, nbones)` | `(idx, w, bad)` — per-vertex 4-slot bone indices/weights, aligned to `mesh_decode`'s vertex order |
| `skeleton_bones` | `skeleton_bones(skel_did)` | `[{name, parent, t, q, s}]`, cached as `decoded/skel_<did>.json` |
| `clip_json` | `clip_json(clip_did, nbones=None)` | `{did, duration, frames, fps, tracks}` — the viewer's clip block; warns if track count ≠ `nbones` |
| `export` | `export(mesh_did, skel_json, clip_did, out_name, compose_json=None)` | writes `decoded/<out_name>.json`; no return value |

Output document shape (written by `export`):

```
{vertices, normals, uvs, triangles, groups,
 skinIndices (flat, 4/vertex), skinWeights (flat, 4/vertex),
 bones: [{name, parent, t, q, s}],
 clip: {did, duration, frames, fps, tracks: [[[t3,q4,s3] per frame] per bone]}}
```

Coordinates stay Z-up as stored on disk; [viewer.md](viewer.md)'s
`anim.html` rotates the `SkinnedMesh` object itself, not the vertex data.

## How it works internally

### Per-vertex skin layout (`SKIN_LAYOUT`)

Skinned vertex records extend the static 56-byte record with a variable
static-prefix size and a variable bone-index/weight tail. `SKIN_LAYOUT` maps
total record **stride** → `(n, idx_off, w_off)`: `n` bone-index bytes
(`uint8`) at `idx_off`, `n` `float32` weights at `w_off`. Verified strides
(comment table in the source, cross-checked against real bone-name sanity on
a rig): 61/66/71 (56-byte static prefix, 1/2/3 bones), 65/70/75 (60-byte
prefix — hair, cloaks), 76/80 (56/60-byte prefix, 4 bones — hooded cloaks),
74/79/84 (64-byte prefix — armour garments), 73 (68-byte prefix — helms
rigid to head). Static prefix sizes seen: 56, 60, 64, 68 — **never assumed**,
always derived per stride. The comment flags a known false-positive
candidate at `io~35-39` (a `u8 0` + `f32 1.0` in the tangent region that
reads as a plausible "bone 0, weight 1.0" pair) — the layout table is
guarded against this by requiring sensible bone *names* downstream, not
just numerically-plausible weights.

`skin_arrays(raw, nbones)` reuses
[`mesh_decode._find_vertex_blocks`](mesh_decode.md) (memoised — see that
page's cache note) to get each submesh's vertex block boundaries, then for
each vertex reads its bone indices/weights per `SKIN_LAYOUT[stride]`.
Unsupported (unrecognized) strides fall back to bone 0 / weight 1.0 for the
whole block. Per-vertex validation: any bone index `>= nbones`, any
non-finite weight, or a weight sum off from 1.0 by more than 0.02 resets
that vertex to bone-0/weight-1.0 and increments the `bad` counter — the
caller (`export`) prints a warning if `bad > 0` rather than failing.
Indices/weights are always padded to exactly 4 slots (three.js's
`skinIndex`/`skinWeight` attribute width), even for 1–3-bone records.

### Skeleton decode (`skeleton_bones`)

Finds the Havok tagfile magic (`1e0db0cacefa11d0` hex) inside the `0x04`
hkaSkeleton record's raw content, parses it via
[`havok_anim.parse_tagfile`](havok_anim.md), and extracts the first
`hkaSkeleton` object's `bones`/`parentIndices`/`referencePose`. Each
`referencePose` entry is a flat 12-float `hkQsTransform`: translation at
`[0:3]` (+1 pad), quaternion `xyzw` at `[4:8]`, scale at `[8:11]` (+1 pad).
Result is cached to `decoded/skel_<did>.json` — skeleton decode only needs
to happen once per skeleton DID, ever.

### Clip decode (`clip_json`)

Delegates entirely to [`havok_anim.decode_clip`](havok_anim.md), reshaping
its per-track-per-frame `{t, q, s}` dicts into flat `[t3, q4, s3]` triples
for compact JSON, and computing `fps = (frames-1)/duration` (or `30.0` if
`duration <= 0`). Warns (does not raise) if the clip's transform-track count
doesn't match the skeleton's bone count — a real mismatch usually means the
wrong clip was paired with the wrong skeleton.

### Assembly (`export`)

Decodes the mesh once via `mesh_decode._decode_gfxobj` (not `decode_mesh` —
texture resolution is intentionally skipped here and instead sourced from
`compose_json` if given), computes skin arrays, optionally overlays
per-submesh textures from a matching `compose_json` (only if its group count
matches the mesh's group count AND each pair's `vert_count` matches —
otherwise the mesh is left untextured with a printed warning, rather than
guessing a mismatched mapping), decodes the clip, and sanitizes every float
via `_san`/`_fin` (non-finite → `0.0`) before writing.

## Gotchas & lessons

- **Bone-index/weight layout is stride-keyed, and strides collide with
  false positives.** The `io~35-39` false-positive note in `SKIN_LAYOUT`'s
  comment block is a direct warning: a numerically plausible "bone 0,
  weight 1.0" can appear by coincidence in the tangent-frame region of an
  unskinned or wrongly-offset record. This is why the layout table was
  validated by bone *names*, not just by weight-sum sanity.
- **Static prefix size must never be assumed** — it varies (56/60/64/68
  bytes) across garment categories (rigid caps vs. cloaks vs. armour vs.
  helms), and is looked up per-stride from `SKIN_LAYOUT`, never hardcoded.
- **Texture copy-over is strict about shape matching** — `export` refuses
  to copy `compose_json` textures onto a mesh whose group/vertex-count
  shape doesn't match exactly, printing a warning and leaving the export
  untextured rather than risking a silently wrong texture-to-submesh
  mapping.
- **Bad skin data resets to bone 0, not to a discarded vertex** — a vertex
  with implausible skin weights still needs *some* valid skin binding to
  render (three.js requires every vertex to have skinIndex/skinWeight), so
  the fallback of bone-0/weight-1.0 keeps the vertex rigidly attached to
  the root-most bone rather than breaking the draw call.

## See also

- [../animation.md](../animation.md) — full skeleton/clip format writeup.
- [havok_anim.py](havok_anim.md) — the tagfile parser and spline decompressor this module wraps.
- [mesh_decode.py](mesh_decode.md) — the vertex-block scan this module reuses for skin data.
- [compose.py](compose.md) — the source of per-submesh textures this module can overlay.
- [viewer.md](viewer.md) — the `/anim` page that renders this module's output.
- [INDEX.md](INDEX.md) — full script index.
