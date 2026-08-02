# Mesh Format (GfxObj)

`0x06` records in the mesh archive hold **Turbine GfxObj-family geometry** —
the same lineage used by *Asheron's Call* and *Dungeons & Dragons Online* —
decompressed via `read_asset` (see [dat-format.md](dat-format.md)). Reference
implementation: [`mesh_decode.py`](scripts/mesh_decode.md), function
`decode_mesh(did)` returning
`{id, flags, num_submeshes, vertices, normals, uvs, triangles, groups}` with
dense `0..V-1` indices, ready for a renderer.

## One decoder for static AND skinned meshes

Static meshes (`Flags == 0x00000001`) and skinned meshes
(`Flags == 0x1000____`) turned out to be **the same on-disk format** — a
static mesh is simply the one-submesh case of the general layout. This
unification was itself a finding: static and skinned geometry were originally
treated as separate formats before the shared structure was recognized.

## Layout

```
0x00  uint32  Id             self DID (type 0x06)
0x04  uint32  Flags          0x00000001 static; 0x1000____ skinned
      uint32  numSurfaces S ; S x uint32 surfaceDID   (0x31______, render props)
      uint32  numTextures T ; T x uint32 textureDID   (0x30______, material)

  per submesh (N submeshes, in order; static N=1):
      uint32  vertexCount
      vertex[vertexCount]     FIXED stride per submesh (see "Vertex record")
      float32 bbox[6]         submesh min[3], max[3]
      uint32  boneDataCount ; uint32 boneData[boneDataCount]   (0 for static)

  then the INDEX REGION, per submesh sequentially (a separate block,
  NOT interleaved with the geometry above):
      uint32  indexCount      (multiple of 3)
      uint16  index[indexCount]   triangle list, SUBMESH-LOCAL 0-based,
                                   max(index) == vertexCount - 1
```

The surface/material header is **two separate count-prefixed lists**
(`[S][S x 0x31 surfaceDID]` then `[T][T x 0x30 textureDID]`), not a single
merged array. There is one `0x31` surface DID per submesh in the common case
(verified on a 4-submesh dress mesh where all four submeshes shared one
surface DID).

### Vertex record

Fixed stride; position and normal come first, with no per-vertex key or
count prefix:

```
float32 pos[3]      render position
float32 normal[3]   unit length — used to auto-detect the stride
float32 uv[2]        at offset 24 (pos[3] + normal[3] = 24 bytes)
... (skinned strides only) tangent frame, then packed bone indices/weights —
    see the skin-weight stride table in animation.md
```

**UV is always at byte offset 24, for every stride observed** (static and
skinned alike). All stride variation lives strictly in the tail *after* the
UV — the tangent/bitangent frame and the bone index/weight tail. This was
confirmed by an exhaustive per-offset UV-edge-continuity scan across multiple
strides: offset 24 is always the most spatially coherent float pair.

To assemble one dense mesh: concatenate submesh vertex blocks in order, and
offset each submesh's local (0-based) triangle indices by the running vertex
count so far. A `groups` list (per submesh: `vert_start`, `vert_count`,
`tri_start`, `tri_count`) should be retained alongside the flattened buffers
so a renderer can bind a different texture per submesh.

## Coordinate system

LOTRO's native mesh convention is **Z-up**. A viewer using a Y-up convention
(e.g. three.js) needs a fixed rotation applied on load to display meshes
upright — see [textures.md](textures.md) for the corresponding UV/V-flip
convention used at render time.

## Stride auto-detection and index-region location

There is no explicit stride field in the format — it must be inferred. The
approach that works:

1. Scan forward from the start of the geometry block. At each candidate
   offset, read a `uint32` count `c` (accepted range 8–60000).
2. Try candidate stride values `s` in `[44, 264)`, looking for **three
   consecutive unit-length normals** at `vs`, `vs+s`, `vs+2s` (a position/
   normal pair test: finite/bounded position at offset `o`, unit-length
   vector at `o+12`).
3. For each candidate stride, count how many consecutive records pass the
   vertex test (the "run"). Accept the **first stride whose run matches the
   declared count `c` (within ±2)** as the real vertex block, and resume
   scanning immediately after it.

⚠️ **Multi-submesh variant: mixed per-vertex strides across one mesh.** A
single GfxObj mesh can contain submesh vertex blocks with **different
strides from each other** — one real garment mesh mixes 76-, 71-, and
61-byte records across its 8 submeshes. The stride probe can hit a shorter,
*wrong* stride first purely by coincidence (three unrelated vectors that
happen to look unit-length); that wrong stride's run then won't match the
declared count `c`. An early version of the finder **broke on that first hit
and discarded the entire block**, silently dropping every submesh using the
longer, correct stride — which cascaded into a wrong per-submesh index-count
list and made the index-region locator fail outright with "index region not
found" on real, present garment meshes.

**Fix**: keep scanning progressively **larger** strides until one's run
matches the declared count, instead of giving up on the first candidate.
With all submesh blocks recovered correctly, the index region locates
normally. This fix was the actual blocker behind an early "wardrobe entries
render on a substitute body instead of the item's own garment" bug — see
[wardrobe.md](wardrobe.md).

`_find_index_region` looks for the offset from which a **strictly
sequential** `[uint32 count][count x uint16]` parse yields exactly one index
buffer per submesh, each with `max(indices) == vertexCount - 1` for its
submesh.

⚠️ **Byte-packed regions are not 4-byte-aligned.** Index/bone tables have
been observed starting at odd byte offsets — every offset must be read
exactly as found, never rounded or assumed aligned.

One decode red herring worth recording: in at least one mesh, a run of bytes
partway through the record *looks* like a plausible index buffer
(`[count][1][uint16 x count]`, with a maximum index inside vertex range) but
does not cohere with any real geometry when rendered — it is a vertex-remap
or bone table, not triangle data. Don't accept the first byte-run that merely
parses; validate against the geometry it would produce.

## Validation trap: the self-fulfilling unit-normal check

⚠️ This is the single most important lesson from decoding this format. An
early decode attempt recovered *per-vertex* UV/attribute count by scanning
forward from each vertex until the next 12 bytes formed a unit-length vector
— i.e., treating "the next thing that looks like a normal" as marking the
start of the next vertex. That approach is **self-fulfilling by
construction**: it can only ever stop on something that looks like a unit
normal, so it passes *every* numeric sanity check — vertex/triangle counts
match, all indices are in range, zero degenerate triangles reported — while
still landing on spatially wrong vertex data for many vertices. The visible
result was **slivers**: triangles fanning out from a spike across the whole
mesh, invisible to any purely count-based or range-based check.

The fix was to stop inferring per-vertex structure entirely and instead use
a **fixed stride per vertex block**, derived from the `[vertexCount]`
prefix (the approach described above). The general lesson applies beyond
this one bug: **numeric validation is necessary but not sufficient** — a
decoder can satisfy every automated check and still be wrong. Visual
inspection (a wireframe render) plus an explicit geometric sanity check
catches what count/range checks cannot. See [limitations.md](limitations.md)
for other instances of this same failure mode elsewhere in the project.

### The sliver self-check

Compute, for every decoded mesh: vertex/triangle counts, bounding box, and
— for every triangle edge — its length compared against **0.5 × the mesh's
bounding-box diagonal**. A correct solid mesh has all edges far shorter than
the bbox diagonal; an edge that long is by definition spanning most of the
mesh and flags a scrambled index/vertex mapping. The sliver-triangle count
in this check should always be 0 for a correctly decoded mesh — treat any
nonzero count as a decode bug, not noise.

A correctly decoded, correctly indexed mesh looks like this in the viewer —
clean shading and a wireframe with uniformly small triangles, no cross-mesh
spikes (a composed multi-part garment; see
[scripts/compose.md](scripts/compose.md)):

| ![Composed garment, shaded](img/viewer-shaded.png) | ![Same mesh, wireframe](img/viewer-wire.png) |
|:--:|:--:|
| Shaded | Wireframe (the sliver check, visually) |

## UV values outside [0,1] are normal, not corruption

A well-decoded mesh can legitimately have `u` values outside `[0, 1]` —
e.g. `u ∈ [-1, 2]` — where the texture is tiled 2× with wrap-around
addressing. This was investigated in depth after a report of "scrambled"
textures on two particular meshes; the decoder turned out to be correct on
every count (right stride selected, right offset, geometrically clean, zero
slivers). The actual cause of the visible scramble was an unrelated wrong
texture being force-applied to those meshes (a texture/material *binding*
bug, not a mesh decode bug) — see [garment sibling-item note in
wardrobe.md](wardrobe.md#sibling-items-and-sleeve-variants) and
[textures.md](textures.md) for the full story. **Do not add a UV-range
sanity check to the stride probe** — it would incorrectly reject legitimate
tiling meshes and there is no better stride to fall back to.

## Known gaps

- **3-vertex sprite/billboard/stub meshes** have zero-length normals and
  fail the unit-normal stride detector; not specially handled. Many wardrobe
  part-slots are deliberately these tiny (~200–350 byte) 3-vertex
  placeholder stubs, not garments — see [wardrobe.md](wardrobe.md).
- Some large multi-megabyte, many-surface meshes (dozens of surfaces) need
  extra handling not yet implemented.
- Skin weights and the per-submesh bone-data region are present in the
  skinned vertex record; their layout is documented in
  [animation.md](animation.md) rather than here, since parsing them is a
  separate concern from geometry decode.

## See also
- [dat-format.md](dat-format.md) — the container/decompression this reads from
- [textures.md](textures.md) — resolving a decoded mesh's diffuse texture
- [wardrobe.md](wardrobe.md) — which mesh DID to decode for a given item
- [animation.md](animation.md) — skin weights, bone indices, and posing a decoded mesh
- [scripts/mesh_decode.md](scripts/mesh_decode.md) — the reference implementation
- [limitations.md](limitations.md) — decoder gaps and the failure-mode log
